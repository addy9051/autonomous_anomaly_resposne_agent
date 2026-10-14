"""
Action Agent — executes remediation actions based on diagnosis results.

Implements 3-tier action classification:
- Tier 1: Execute autonomously
- Tier 2: Request SRE approval via Slack, then execute
- Tier 3: Page on-call engineer, provide recommendations

Uses N8n webhooks for action execution and Slack for notifications/approvals.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langfuse.callback import CallbackHandler

from agents.action.policy import DeterministicActionPolicy
from agents.action.tiers import classify_action
from agents.action.workflows import trigger_workflow
from agents.verification.verifier import RecoveryVerifier
from shared.config import get_settings
from shared.llm import get_chat_model
from shared.schemas import (
    ActionPolicyDecision,
    ActionResult,
    ActionTier,
    DiagnosisResult,
    IncidentRecord,
    IncidentStatus,
    RecommendedAction,
    VerificationOutcome,
)
from shared.utils import LLMCostTracker, Timer, get_logger, get_tracer

logger = get_logger("action_agent")
tracer = get_tracer()


class ActionAgent:
    """
    Action Agent — executes tiered remediation actions.

    Enforces deterministic safety policy, requires explicit SRE approval for writes,
    disables development auto-approval on live infrastructure, and independently
    verifies post-action recovery.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = get_chat_model(
            model_name=self.settings.llm.action_agent_model,
            temperature=0.1,
            max_tokens=2048,
        )
        self.verifier = RecoveryVerifier(stabilization_window_seconds=0.1)
        logger.info("action_agent_initialized")

    async def execute(
        self,
        diagnosis: DiagnosisResult,
        incident: IncidentRecord,
        cost_tracker: LLMCostTracker | None = None,
        dry_run: bool = False,
    ) -> list[ActionResult]:
        """
        Execute or propose recommended actions based on deterministic policy.
        """
        with tracer.start_as_current_span("action_agent.execute") as span:
            span.set_attribute("incident.id", diagnosis.incident_id)
            span.set_attribute("num_actions", len(diagnosis.recommended_actions))

            results: list[ActionResult] = []
            incident.status = IncidentStatus.EXECUTING

            service = "unknown"
            if incident.anomaly_event and incident.anomaly_event.affected_services:
                service = incident.anomaly_event.affected_services[0]

            for action in diagnosis.recommended_actions:
                timer = Timer()
                with timer:
                    result = await self._evaluate_and_execute_action(
                        action, diagnosis, incident, service, dry_run=dry_run
                    )
                    result.execution_time_ms = timer.elapsed_ms
                    results.append(result)

                logger.info(
                    "action_evaluated",
                    action=action.action,
                    tier=action.tier.value,
                    status=result.execution_status,
                    policy_decision=result.policy_decision,
                    elapsed_ms=timer.elapsed_ms,
                )

            # Langfuse observability
            handler = None
            if self.settings.observability.langfuse_public_key and self.settings.observability.langfuse_enabled:
                try:
                    handler = CallbackHandler(
                        public_key=self.settings.observability.langfuse_public_key,
                        secret_key=self.settings.observability.langfuse_secret_key,
                        host=self.settings.observability.langfuse_host,
                        session_id=diagnosis.incident_id,
                        user_id="sre-system",
                        tags=["agent:action", f"env:{self.settings.app.app_env}"],
                        metadata={"agent_version": "1.0.0", "prompt_version": "act-tier-v1.8.0"},
                    )
                except Exception as e:
                    logger.warning("langfuse_init_failed", error=str(e))
                    handler = None

            summary = await self._generate_incident_summary(diagnosis, results, cost_tracker, handler)

            # Send notification
            await self._send_notification(diagnosis, results, summary)

            # Update incident record
            incident.action_results = results

            # Determine incident lifecycle state based on policy and execution results
            has_pending_approval = any(r.execution_status == "pending_approval" for r in results)
            has_executed_actions = any(r.execution_status in ("success", "executed") for r in results)
            all_recommend_only = len(results) > 0 and all(r.execution_status == "recommend_only" for r in results)

            if has_pending_approval:
                incident.status = IncidentStatus.AWAITING_APPROVAL
            elif all_recommend_only:
                incident.status = IncidentStatus.RECOMMENDATION_READY
            elif has_executed_actions:
                # P0 Blueprint requirement: Action completion is not recovery. Run independent verifier.
                incident.status = IncidentStatus.VERIFYING
                verification = await self.verifier.verify(incident, results, dry_run=dry_run)
                incident.verification_result = verification
                if verification.outcome == VerificationOutcome.VERIFIED_RECOVERY:
                    incident.status = IncidentStatus.VERIFIED_RECOVERED
                    incident.auto_resolved = True
                    incident.resolved_at = datetime.now(timezone.utc)
                else:
                    incident.status = IncidentStatus.ESCALATED
            else:
                incident.status = IncidentStatus.ESCALATED

            return results

    async def _evaluate_and_execute_action(
        self,
        action: RecommendedAction,
        diagnosis: DiagnosisResult,
        incident: IncidentRecord,
        service: str,
        dry_run: bool,
    ) -> ActionResult:
        """Evaluate action against deterministic policy gate and execute if permitted."""
        tier = classify_action(action.action)

        policy_eval = DeterministicActionPolicy.evaluate(
            action_name=action.action,
            service=service,
            environment=self.settings.app.app_env,
            params=action.params,
            dry_run=dry_run,
        )

        if policy_eval.decision == ActionPolicyDecision.DENIED:
            logger.warning("action_policy_denied", action=action.action, reason=policy_eval.reason)
            return ActionResult(
                incident_id=diagnosis.incident_id,
                action_taken=action.action,
                tier=tier,
                execution_status="policy_denied",
                policy_decision=ActionPolicyDecision.DENIED,
                error=policy_eval.reason,
                is_simulated=dry_run,
            )

        if policy_eval.decision == ActionPolicyDecision.RECOMMEND_ONLY:
            logger.info("action_recommend_only", action=action.action)
            return ActionResult(
                incident_id=diagnosis.incident_id,
                action_taken=action.action,
                tier=tier,
                execution_status="recommend_only",
                policy_decision=ActionPolicyDecision.RECOMMEND_ONLY,
                output={
                    "message": policy_eval.reason,
                    "recommended_params": action.params,
                    "rollback_steps": action.rollback_steps,
                    "estimated_impact": action.estimated_impact,
                },
                is_simulated=dry_run,
            )

        if policy_eval.decision == ActionPolicyDecision.APPROVAL_REQUIRED:
            logger.info("action_approval_required", action=action.action)
            return ActionResult(
                incident_id=diagnosis.incident_id,
                action_taken=action.action,
                tier=tier,
                execution_status="pending_approval",
                policy_decision=ActionPolicyDecision.APPROVAL_REQUIRED,
                output={
                    "message": "Explicit SRE approval required before execution.",
                    "recommended_params": action.params,
                    "rollback_steps": action.rollback_steps,
                    "policy_reason": policy_eval.reason,
                },
                human_approved=None,
                is_simulated=dry_run,
            )

        # Action is PERMITTED_AUTO under deterministic policy constraints
        logger.info("action_permitted_auto_executing", action=action.action, dry_run=dry_run)
        res = await trigger_workflow(action.action, action.params, dry_run=dry_run)
        return ActionResult(
            incident_id=diagnosis.incident_id,
            action_taken=action.action,
            tier=tier,
            execution_status=res.get("status", "failed"),
            policy_decision=ActionPolicyDecision.PERMITTED_AUTO,
            output=res,
            is_simulated=dry_run,
            human_approved=None,
        )

    async def execute_approved_action(
        self,
        action_name: str,
        params: dict[str, Any],
        incident: IncidentRecord,
        approver: str = "sre-engineer",
        dry_run: bool = False,
    ) -> ActionResult:
        """
        Execute an action that has received explicit human approval.
        Followed immediately by independent recovery verification.
        """
        logger.info("executing_approved_action", action=action_name, approver=approver, dry_run=dry_run)
        incident.status = IncidentStatus.EXECUTING

        tier = classify_action(action_name)
        result = await trigger_workflow(action_name, params, dry_run=dry_run)
        status = "success" if result.get("status") == "success" else "failed"

        action_result = ActionResult(
            incident_id=incident.incident_id,
            action_taken=action_name,
            tier=tier,
            execution_status=status,
            output=result,
            human_approved=True,
            approver=approver,
            is_simulated=dry_run,
        )
        incident.action_results.append(action_result)

        # Independent verification
        incident.status = IncidentStatus.VERIFYING
        verification = await self.verifier.verify(incident, [action_result], dry_run=dry_run)
        incident.verification_result = verification

        if verification.outcome == VerificationOutcome.VERIFIED_RECOVERY:
            incident.status = IncidentStatus.VERIFIED_RECOVERED
            incident.auto_resolved = True
            incident.resolved_at = datetime.now(timezone.utc)
        else:
            incident.status = IncidentStatus.ESCALATED

        return action_result

    async def _generate_incident_summary(
        self,
        diagnosis: DiagnosisResult,
        results: list[ActionResult],
        cost_tracker: LLMCostTracker | None = None,
        handler: CallbackHandler | None = None,
    ) -> str:
        """Generate a plain-language incident summary (≤200 words)."""
        prompt = f"""Generate a concise incident summary (max 200 words) for Slack notification.

Root Cause: {diagnosis.root_cause}
Category: {diagnosis.root_cause_category.value}
Confidence: {diagnosis.confidence}

Actions Taken:
{json.dumps([r.model_dump(mode="json") for r in results], indent=2, default=str)}

Format as:
🔍 **Root Cause**: [brief description]
⚡ **Actions Taken**: [list of actions and their status]
📊 **Current Status**: [resolved/pending/escalated]
🔮 **Next Steps**: [if any]
"""
        response = await self.llm.ainvoke(
            [
                SystemMessage(content="You are an SRE writing a concise incident summary for Slack."),
                HumanMessage(content=prompt),
            ],
            config={"callbacks": [handler]} if handler else None,
        )

        if cost_tracker and hasattr(response, "usage_metadata") and response.usage_metadata:
            cost_tracker.track(
                model=self.settings.llm.action_agent_model,
                input_tokens=response.usage_metadata.get("input_tokens", 0),
                output_tokens=response.usage_metadata.get("output_tokens", 0),
            )

        content = response.content
        return content if isinstance(content, str) else str(content)

    async def _send_notification(
        self,
        diagnosis: DiagnosisResult,
        results: list[ActionResult],
        summary: str,
    ) -> None:
        """Send Slack notification about the incident."""
        # Log locally first
        logger.info(
            "incident_notification",
            incident_id=diagnosis.incident_id,
            num_actions=len(results),
        )

        # Attempt real Slack notification
        if self.settings.integrations.slack_bot_token:
            try:
                from slack_sdk.errors import SlackApiError
                from slack_sdk.web.async_client import AsyncWebClient

                client = AsyncWebClient(token=self.settings.integrations.slack_bot_token)
                channel = self.settings.integrations.slack_alert_channel

                # Add severity icon to summary
                icon = "🔴" if diagnosis.confidence > 0.8 else "🟡"
                header = f"{icon} *Anomaly Detected: {diagnosis.incident_id[:8]}*"

                blocks = [
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"{header}\n{summary}"}},
                    {
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": f"*Incident ID:* `{diagnosis.incident_id}`"},
                            {"type": "mrkdwn", "text": f"*Category:* `{diagnosis.root_cause_category.value}`"},
                        ],
                    },
                ]

                await client.chat_postMessage(channel=channel, blocks=blocks, text=summary)
                logger.debug("slack_notified", incident_id=diagnosis.incident_id)

            except ImportError:
                logger.warning(
                    "slack_sdk_missing", incident_id=diagnosis.incident_id, tip="Install with: pip install slack_sdk"
                )
            except SlackApiError as e:
                error_type = e.response["error"]
                if error_type == "channel_not_found":
                    logger.error(
                        "slack_api_error_channel_not_found",
                        error=error_type,
                        incident_id=diagnosis.incident_id,
                        tip=(
                            f"HINT: Have you invited the bot to channel '{channel}'? "
                            "Type /invite @YourBotName in that channel."
                        ),
                    )
                else:
                    logger.error("slack_api_error", error=error_type, incident_id=diagnosis.incident_id)
            except Exception as e:
                logger.error("slack_unexpected_error", error=str(e), incident_id=diagnosis.incident_id)
        else:
            logger.debug("slack_skipped", reason="no_token")
