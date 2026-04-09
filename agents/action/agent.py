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
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage
from langfuse.callback import CallbackHandler

from shared.llm import get_chat_model

from agents.action.tiers import classify_action
from agents.action.workflows import trigger_workflow
from shared.config import get_settings
from shared.schemas import (
    ActionResult,
    ActionTier,
    DiagnosisResult,
    IncidentRecord,
    IncidentStatus,
    RecommendedAction,
)
from shared.utils import LLMCostTracker, Timer, get_logger, get_tracer

logger = get_logger("action_agent")
tracer = get_tracer()


class ActionAgent:
    """
    Action Agent — executes tiered remediation actions.

    Processes DiagnosisResult, classifies recommended actions by tier,
    executes Tier 1 autonomously, requests approval for Tier 2,
    and pages humans for Tier 3.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = get_chat_model(
            model_name=self.settings.llm.action_agent_model,
            temperature=0.1,
            max_tokens=2048,
        )
        logger.info("action_agent_initialized")

    async def execute(
        self,
        diagnosis: DiagnosisResult,
        incident: IncidentRecord,
        cost_tracker: LLMCostTracker | None = None,
        dry_run: bool = False,
    ) -> list[ActionResult]:
        """
        Execute recommended actions from a diagnosis result.

        Args:
            diagnosis: The root cause analysis with recommended actions
            incident: The incident record to update
            cost_tracker: Optional LLM cost tracker
            dry_run: If True, simulate all actions without executing

        Returns:
            List of ActionResult objects for each action attempted.
        """
        with tracer.start_as_current_span("action_agent.execute") as span:
            span.set_attribute("incident.id", diagnosis.incident_id)
            span.set_attribute("num_actions", len(diagnosis.recommended_actions))

            results = []
            incident.status = IncidentStatus.ACTION_EXECUTING

            for action in diagnosis.recommended_actions:
                timer = Timer()
                with timer:
                    result = await self._execute_action(action, diagnosis, dry_run)
                    result.execution_time_ms = timer.elapsed_ms
                    results.append(result)

                logger.info(
                    "action_executed",
                    action=action.action,
                    tier=action.tier.value,
                    status=result.execution_status,
                    elapsed_ms=timer.elapsed_ms,
                )

            # Only initialize Langfuse if credentials are provided
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
                        metadata={
                            "agent_version": "1.0.0",
                            "prompt_version": "act-tier-v1.8.0"
                        }
                    )
                except Exception as e:
                    logger.warning("langfuse_init_failed", error=str(e))
                    handler = None

            summary = await self._generate_incident_summary(diagnosis, results, cost_tracker, handler)

            # Send notification
            await self._send_notification(diagnosis, results, summary)

            # Update incident record
            incident.action_results = results
            if all(r.execution_status == "success" for r in results):
                incident.status = IncidentStatus.RESOLVED
                incident.auto_resolved = True
                incident.resolved_at = datetime.utcnow()
            elif any(r.execution_status == "pending_approval" for r in results):
                incident.status = IncidentStatus.AWAITING_APPROVAL
            else:
                incident.status = IncidentStatus.ESCALATED

            return results

    async def _execute_action(
        self,
        action: RecommendedAction,
        diagnosis: DiagnosisResult,
        dry_run: bool,
    ) -> ActionResult:
        """Execute a single action based on its tier."""

        tier = classify_action(action.action)

        if tier == ActionTier.TIER_1_AUTO:
            return await self._execute_tier1(action, diagnosis, dry_run)
        elif tier == ActionTier.TIER_2_APPROVE:
            return await self._execute_tier2(action, diagnosis, dry_run)
        else:
            return await self._execute_tier3(action, diagnosis)

    async def _execute_tier1(
        self,
        action: RecommendedAction,
        diagnosis: DiagnosisResult,
        dry_run: bool,
    ) -> ActionResult:
        """Execute Tier 1 action autonomously."""
        logger.info("tier1_auto_execute", action=action.action)

        result = await trigger_workflow(action.action, action.params, dry_run=dry_run)

        return ActionResult(
            incident_id=diagnosis.incident_id,
            action_taken=action.action,
            tier=ActionTier.TIER_1_AUTO,
            execution_status=result.get("status", "failed"),
            output=result,
            human_approved=None,  # No approval needed
        )

    async def _execute_tier2(
        self,
        action: RecommendedAction,
        diagnosis: DiagnosisResult,
        dry_run: bool,
    ) -> ActionResult:
        """Request approval for Tier 2 action via Slack."""
        logger.info("tier2_approval_requested", action=action.action)

        # In development, simulate approval
        if self.settings.app.app_env == "development":
            # Auto-approve in dev
            result = await trigger_workflow(action.action, action.params, dry_run=dry_run)
            return ActionResult(
                incident_id=diagnosis.incident_id,
                action_taken=action.action,
                tier=ActionTier.TIER_2_APPROVE,
                execution_status=result.get("status", "failed"),
                output=result,
                human_approved=True,
            )

        # In production, this would post a Slack interactive message
        # and wait for approval before executing
        return ActionResult(
            incident_id=diagnosis.incident_id,
            action_taken=action.action,
            tier=ActionTier.TIER_2_APPROVE,
            execution_status="pending_approval",
            output={"message": "Approval requested via Slack"},
            human_approved=None,
        )

    async def _execute_tier3(
        self,
        action: RecommendedAction,
        diagnosis: DiagnosisResult,
    ) -> ActionResult:
        """Page on-call for Tier 3 action — never execute autonomously."""
        logger.info("tier3_human_required", action=action.action)

        return ActionResult(
            incident_id=diagnosis.incident_id,
            action_taken=action.action,
            tier=ActionTier.TIER_3_HUMAN,
            execution_status="escalated",
            output={
                "message": f"⚠️ Human action required: {action.action}",
                "recommended_params": action.params,
                "rollback_steps": action.rollback_steps,
                "estimated_impact": action.estimated_impact,
            },
            human_approved=None,
        )

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
        response = await self.llm.ainvoke([
            SystemMessage(content="You are an SRE writing a concise incident summary for Slack."),
            HumanMessage(content=prompt),
        ], config={"callbacks": [handler]} if handler else None)

        if cost_tracker and hasattr(response, "usage_metadata") and response.usage_metadata:
            cost_tracker.track(
                model=self.settings.llm.action_agent_model,
                input_tokens=response.usage_metadata.get("input_tokens", 0),
                output_tokens=response.usage_metadata.get("output_tokens", 0),
            )

        return response.content

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
            from slack_sdk.web.async_client import AsyncWebClient
            from slack_sdk.errors import SlackApiError

            client = AsyncWebClient(token=self.settings.integrations.slack_bot_token)
            channel = self.settings.integrations.slack_alert_channel

            try:
                # Add severity icon to summary
                icon = "🔴" if diagnosis.confidence > 0.8 else "🟡"
                header = f"{icon} *Anomaly Detected: {diagnosis.incident_id[:8]}*"
                
                blocks = [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"{header}\n{summary}"}
                    },
                    {
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": f"*Incident ID:* `{diagnosis.incident_id}`"},
                            {"type": "mrkdwn", "text": f"*Category:* `{diagnosis.root_cause_category.value}`"}
                        ]
                    }
                ]

                await client.chat_postMessage(channel=channel, blocks=blocks, text=summary)
                logger.debug("slack_notified", incident_id=diagnosis.incident_id)
            
            except SlackApiError as e:
                error_type = e.response["error"]
                if error_type == "channel_not_found":
                    logger.error(
                        "slack_api_error_channel_not_found", 
                        error=error_type, 
                        incident_id=diagnosis.incident_id,
                        tip=f"HINT: Have you invited the bot to channel '{channel}'? Type /invite @YourBotName in that channel."
                    )
                else:
                    logger.error("slack_api_error", error=error_type, incident_id=diagnosis.incident_id)
            except Exception as e:
                logger.error("slack_unexpected_error", error=str(e), incident_id=diagnosis.incident_id)
        else:
            logger.debug("slack_skipped", reason="no_token")
