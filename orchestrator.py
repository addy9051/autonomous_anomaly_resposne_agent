"""
Main Orchestrator — End-to-End Agent Pipeline.

Coordinates the full incident lifecycle:
  TelemetryEvent → Monitoring Agent → Diagnosis Agent → Action Agent → Feedback Loop

Supports both:
- Streaming mode: continuous processing from Kafka / in-memory event bus
- Batch mode: process a list of events and return results
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any

import structlog

from agents.action.agent import ActionAgent
from agents.action.pagerduty import resolve_pagerduty_incident
from agents.diagnosis.graph import DiagnosisAgent
from agents.feedback.regression import RegressionDetector, alert_key as make_alert_key
from agents.monitoring.agent import MonitoringAgent
from data_pipeline.connectors.adapters import get_telemetry_adapter
from data_pipeline.connectors.synthetic_producer import SyntheticTelemetryProducer
from data_pipeline.flink_jobs.anomaly_features import AlertDeduplicator, RollingWindowAggregator
from shared.config import get_settings
from shared.episodic_memory import (
    BRANCH_AUTO_FIX,
    BRANCH_CRITICAL,
    BRANCH_RECOMMEND,
    Episode,
    EpisodicMemory,
)
from shared.incident_store import get_incident_store
from shared.schemas import (
    AnomalyEvent,
    DiagnosisResult,
    IncidentRecord,
    IncidentStatus,
    Severity,
    TelemetryEvent,
)
from shared.utils import LLMCostTracker, Timer, get_logger, langfuse_session_context, setup_logging

logger = get_logger("orchestrator")


class AgentOrchestrator:
    """
    Master orchestrator that wires all agents together.

    Target Blueprint Lifecycle:
      investigating → recommendation_ready → awaiting_approval
      → executing → verifying → verified_recovered / escalated
    """

    def __init__(self, memory: EpisodicMemory | None = None) -> None:
        setup_logging()
        self.settings = get_settings()

        self.monitoring_agent = MonitoringAgent()
        self.diagnosis_agent = DiagnosisAgent()
        self.action_agent = ActionAgent()
        self.memory = memory or EpisodicMemory()
        self.feature_aggregator = RollingWindowAggregator()
        self.alert_dedup = AlertDeduplicator()

        # Durable incident store (persists across restarts)
        self.store = get_incident_store()
        self.regression_detector = RegressionDetector(self.store)

        # Track active incidents in memory (repopulated from durable store)
        self.active_incidents: dict[str, IncidentRecord] = {
            inc.incident_id: inc for inc in self.store.list_active()
        }
        self.resolved_incidents: list[IncidentRecord] = self.store.list_resolved(limit=50)

        # Real-time telemetry pulse buffer (last 100 events)
        self.telemetry_history: deque[TelemetryEvent] = deque(maxlen=100)

        # Telemetry API adapter (Simulator, Prometheus, Datadog, Splunk)
        self.telemetry_adapter = get_telemetry_adapter()

        logger.info(
            "orchestrator_initialized",
            connector_mode=self.telemetry_adapter.source.value,
            restored_active_incidents=len(self.active_incidents),
        )

    def _update_regressions(self, new_incident: IncidentRecord) -> None:
        """Flag earlier fixes that did not hold, and settle those that did. Never blocks the pipeline."""
        try:
            changed = self.regression_detector.on_new_incident(new_incident)
            changed += self.regression_detector.settle()
        except Exception as exc:
            logger.warning("regression_update_failed", error=str(exc))
            return
        by_id = {inc.incident_id: inc for inc in changed}
        for known in self.resolved_incidents:
            if known.incident_id in by_id:
                known.regressed = by_id[known.incident_id].regressed

    async def process_event(self, event: TelemetryEvent, dry_run: bool = False) -> IncidentRecord | None:
        """
        Process a single telemetry event through the full pipeline.

        Returns:
            IncidentRecord if an anomaly was detected and processed, None otherwise.
        """
        # Buffer every incoming event for the real-time UI pulse
        self.telemetry_history.append(event)

        total_timer = Timer()
        with total_timer:
            # ── Step 1: Feature Extraction ──
            self.feature_aggregator.add_event(event)

            # ── Step 2: Monitoring Agent — Anomaly Detection ──
            cost_tracker = LLMCostTracker(incident_id="pending")
            with langfuse_session_context(event.event_id):
                anomaly = await self.monitoring_agent.process_event(event, cost_tracker)

            if not anomaly:
                return None

        return await self.process_anomaly(anomaly, cost_tracker, dry_run=dry_run)

    async def process_anomaly(
        self,
        anomaly: AnomalyEvent,
        cost_tracker: LLMCostTracker,
        dry_run: bool = False,
    ) -> IncidentRecord | None:
        """Bind the event id to every log line so logs join to the Langfuse session of the same id."""
        with structlog.contextvars.bound_contextvars(event_id=anomaly.event_id), langfuse_session_context(
            anomaly.event_id
        ):
            return await self._process_anomaly(anomaly, cost_tracker, dry_run=dry_run)

    async def _process_anomaly(
        self,
        anomaly: AnomalyEvent,
        cost_tracker: LLMCostTracker,
        dry_run: bool = False,
    ) -> IncidentRecord | None:
        """
        Run an already-detected anomaly (from the monitoring agent or an external
        alert) through dedupe, incident creation, diagnosis and action policy.
        """
        total_timer = Timer()
        with total_timer:
            # Deduplicate alerts
            alert_key = make_alert_key(anomaly)
            if not self.alert_dedup.should_fire(alert_key, anomaly.model_dump(mode="json")):
                suppressed = self.alert_dedup.get_suppressed_count(alert_key)
                logger.debug("alert_suppressed", key=alert_key, suppressed_count=suppressed)
                return None

            # Episodic memory: has this event been seen recently, and how was it handled?
            is_critical = anomaly.severity == Severity.CRITICAL
            prior = await self.memory.find_duplicate(anomaly)
            replay: DiagnosisResult | None = None
            if prior is not None:
                if prior.status in ("open", "escalated") and prior.outcome in (None, "none"):
                    await self.memory.log(prior.episode_id, "duplicate_suppressed", "orchestrator", {
                        "event_id": anomaly.event_id,
                    })
                    logger.info("duplicate_suppressed", episode_id=prior.episode_id)
                    return None
                if prior.status == "resolved" and prior.outcome == "recovered" and prior.decision:
                    try:
                        replay = DiagnosisResult.model_validate(prior.decision)
                    except Exception as exc:
                        logger.warning("replay_decision_invalid", error=str(exc))

            episode = await self.memory.open_episode(
                anomaly,
                branch=BRANCH_CRITICAL if is_critical else BRANCH_RECOMMEND,
                duplicate_of=prior.episode_id if prior else None,
            )

            incident = IncidentRecord(
                status=IncidentStatus.DETECTED,
                anomaly_event=anomaly,
            )
            cost_tracker.incident_id = incident.incident_id
            self.active_incidents[incident.incident_id] = incident
            self.store.save(incident)
            self._update_regressions(incident)
            self.store.record_audit(incident.incident_id, "monitoring", "anomaly_detected", {
                "services": anomaly.affected_services,
                "severity": anomaly.severity.value,
                "type": anomaly.anomaly_type.value,
            })

            logger.info(
                "incident_created",
                incident_id=incident.incident_id,
                severity=anomaly.severity.value,
                anomaly_type=anomaly.anomaly_type.value,
            )

            # ── Step 3: Investigation & Diagnosis Agent ──
            incident.status = IncidentStatus.INVESTIGATING
            self.store.save(incident)

            incident.episode_id = episode.episode_id
            incident.duplicate_of = prior.episode_id if prior else None
            incident.handling_branch = episode.branch
            if replay is not None:
                diagnosis = replay.model_copy(update={"incident_id": incident.incident_id, "event_id": anomaly.event_id})
                await self.memory.log(episode.episode_id, "diagnosis_replayed", "orchestrator", {
                    "from_episode": prior.episode_id if prior else None,
                })
            else:
                diagnosis = await self.diagnosis_agent.diagnose(anomaly, cost_tracker)
                incident.skills_used = list(getattr(self.diagnosis_agent, "last_skills", []) or [])
            if is_critical:
                diagnosis.recommended_actions = []
            incident.diagnosis_result = diagnosis
            incident.time_to_detect_seconds = (datetime.now(timezone.utc) - incident.created_at).total_seconds()
            self.store.save(incident)
            self.store.record_audit(incident.incident_id, "diagnosis", "diagnosis_complete", {
                "root_cause": diagnosis.root_cause,
                "category": diagnosis.root_cause_category.value,
                "confidence": diagnosis.confidence,
                "recommended_actions": [a.action for a in diagnosis.recommended_actions],
            })

            logger.info(
                "diagnosis_complete",
                incident_id=incident.incident_id,
                root_cause=diagnosis.root_cause_category.value,
                confidence=diagnosis.confidence,
                is_novel=diagnosis.is_novel_incident,
            )

            # ── Step 4: Action Policy Evaluation & Remediation ──
            incident.status = IncidentStatus.RECOMMENDATION_READY
            self.store.save(incident)

            await self.action_agent.execute(diagnosis, incident, cost_tracker, dry_run=dry_run)
            incident.time_to_mitigate_seconds = (datetime.now(timezone.utc) - incident.created_at).total_seconds()

            # Sync cost tracker
            incident.total_llm_tokens_used = cost_tracker.total_tokens
            incident.total_llm_cost_usd = cost_tracker.total_cost
            self.store.save(incident)
            await self._close_episode(episode, incident, diagnosis, is_critical)

            # ── Step 5: Lifecycle Transition & Durability ──
            if incident.status in (IncidentStatus.VERIFIED_RECOVERED, IncidentStatus.RESOLVED):
                # Only move to resolved if independent verification confirmed recovery
                if incident.incident_id in self.active_incidents:
                    del self.active_incidents[incident.incident_id]
                self.resolved_incidents.append(incident)
                self.store.record_audit(incident.incident_id, "orchestrator", "verified_recovered", {
                    "auto_resolved": incident.auto_resolved,
                })
                # Sync with PagerDuty (Close the ticket)
                await resolve_pagerduty_incident(incident.incident_id)
            else:
                # If awaiting approval, executing, or escalated: KEEP ACTIVE in store and active_incidents!
                self.active_incidents[incident.incident_id] = incident
                self.store.record_audit(incident.incident_id, "orchestrator", incident.status.value, {
                    "pending_actions": [a.model_dump(mode="json") for a in incident.action_results],
                })
                logger.info(
                    "incident_in_progress",
                    incident_id=incident.incident_id,
                    status=incident.status.value,
                )

        logger.info(
            "pipeline_complete",
            incident_id=incident.incident_id,
            total_elapsed_ms=total_timer.elapsed_ms,
            status=incident.status.value,
        )

        return incident

    async def _close_episode(
        self, episode: Episode, incident: IncidentRecord, diagnosis: DiagnosisResult, is_critical: bool
    ) -> None:
        """Persist the outcome to episodic memory and attach the SRE-facing brief to the incident."""
        results = incident.action_results
        executed = [r for r in results if r.execution_status == "success"]
        recovered = incident.status in (IncidentStatus.VERIFIED_RECOVERED, IncidentStatus.RESOLVED)
        if is_critical:
            branch, status, outcome = BRANCH_CRITICAL, "escalated", "none"
        elif executed:
            branch = BRANCH_AUTO_FIX
            status = "resolved" if recovered else "failed"
            outcome = "recovered" if recovered else "not_recovered"
        else:
            branch, status, outcome = BRANCH_RECOMMEND, "escalated", "none"

        anomaly = incident.anomaly_event
        brief = {
            "issue": {
                "type": anomaly.anomaly_type.value if anomaly else None,
                "severity": anomaly.severity.value if anomaly else None,
                "services": anomaly.affected_services if anomaly else [],
            },
            "handling": branch,
            "root_cause": diagnosis.root_cause,
            "root_cause_category": diagnosis.root_cause_category.value,
            "confidence": diagnosis.confidence,
            "summary": diagnosis.decision_summary,
            "evidence_gaps": diagnosis.missing_sources,
            "fix_applied": [r.action_taken for r in executed],
            "recommended_steps": [] if is_critical else [a.action for a in diagnosis.recommended_actions],
            "action_statuses": {r.action_taken: r.execution_status for r in results},
            "duplicate_of": incident.duplicate_of,
            "skills_used": incident.skills_used,
        }
        incident.brief = brief
        incident.handling_branch = branch
        self.store.save(incident)
        await self.memory.log(episode.episode_id, "outcome", "orchestrator", {"branch": branch, "outcome": outcome})
        await self.memory.close(
            episode,
            branch=branch,
            status=status,
            outcome=outcome,
            root_cause=diagnosis.root_cause,
            decision=diagnosis.model_dump(mode="json"),
            action=executed[0].action_taken if executed else None,
            skill=(incident.skills_used[0] if incident.skills_used else None),
            brief=brief,
        )

    async def run_streaming(
        self,
        events_per_second: float = 5.0,
        anomaly_probability: float = 0.05,
        duration_seconds: float = 60.0,
        dry_run: bool = True,
    ) -> list[IncidentRecord]:
        """
        Run in streaming mode with synthetic telemetry.

        Args:
            events_per_second: Telemetry event generation rate
            anomaly_probability: Probability of anomalous events
            duration_seconds: Total duration to run
            dry_run: If True, simulate actions without executing

        Returns:
            List of incident records created
        """
        producer = SyntheticTelemetryProducer()
        incidents = []

        logger.info(
            "streaming_mode_started",
            eps=events_per_second,
            anomaly_prob=anomaly_probability,
            duration=duration_seconds,
        )

        async for event in producer.stream_events(
            events_per_second=events_per_second,
            anomaly_probability=anomaly_probability,
            duration_seconds=duration_seconds,
        ):
            try:
                incident = await self.process_event(event, dry_run=dry_run)
                if incident:
                    incidents.append(incident)
            except Exception as e:
                logger.error("event_processing_error", error=str(e), event_id=event.event_id)

        logger.info(
            "streaming_complete",
            total_events=producer.event_count,
            incidents_detected=len(incidents),
        )

        return incidents

    async def run_batch(
        self,
        events: list[TelemetryEvent],
        dry_run: bool = True,
    ) -> list[IncidentRecord]:
        """
        Process a batch of telemetry events.

        Returns:
            List of incident records for detected anomalies.
        """
        incidents = []
        for event in events:
            try:
                incident = await self.process_event(event, dry_run=dry_run)
                if incident:
                    incidents.append(incident)
            except Exception as e:
                logger.error("batch_event_error", error=str(e), event_id=event.event_id)

        return incidents

    def get_status(self) -> dict[str, Any]:
        """Get current orchestrator status."""
        return {
            "active_incidents": len(self.active_incidents),
            "resolved_incidents": len(self.resolved_incidents),
            "episodic_memory_degraded": self.memory.degraded,
            "feature_windows": len(self.feature_aggregator.windows),
        }


# ─── CLI Entry Point ────────────────────────────────────────────


async def main() -> None:
    """Main entry point."""
    # 1. Load settings early to sync environment (Final Hardening)
    from shared.config import get_settings

    get_settings()

    import argparse

    parser = argparse.ArgumentParser(description="Autonomous Anomaly Response Orchestrator")
    parser.add_argument("--mode", choices=["stream", "batch", "demo"], default="demo")
    parser.add_argument("--duration", type=float, default=30.0, help="Streaming duration in seconds")
    parser.add_argument("--eps", type=float, default=2.0, help="Events per second")
    parser.add_argument("--anomaly-prob", type=float, default=0.1, help="Anomaly probability")
    parser.add_argument(
        "--connector",
        choices=["simulator", "prometheus", "datadog", "splunk"],
        default=None,
        help="Observability platform telemetry connector adapter",
    )
    parser.add_argument(
        "--scenario",
        choices=["bad_deploy", "database_lock"],
        default=None,
        help="Execute a specific story-based incident narrative scenario",
    )
    args = parser.parse_args()

    orchestrator = AgentOrchestrator()
    if args.connector:
        orchestrator.telemetry_adapter = get_telemetry_adapter(args.connector)

    if args.scenario:
        from data_pipeline.connectors.story_simulation import StorySimulationEngine

        engine = StorySimulationEngine()
        events = engine.get_scenario_events(args.scenario)
        print(f"\n🎬 Running Story-Based Simulation Scenario: '{args.scenario}' ({len(events)} timeline events)...\n")
        incidents = await orchestrator.run_batch(events, dry_run=True)
        print(f"\n{'=' * 60}")
        print(f"📊 Scenario Evaluation Complete: {len(incidents)} incidents triggered from story narrative.")
        for inc in incidents:
            print(f"\n  🔴 Incident {inc.incident_id[:8]}...")
            severity = inc.anomaly_event.severity.value if inc.anomaly_event else "UNKNOWN"
            anomaly_type = inc.anomaly_event.anomaly_type.value if inc.anomaly_event else "UNKNOWN"
            print(f"     Severity: {severity} | Type: {anomaly_type}")
            if inc.diagnosis_result:
                print(f"     Diagnosed Root Cause: {inc.diagnosis_result.root_cause}")
                print(f"     Category: {inc.diagnosis_result.root_cause_category.value} | Confidence: {inc.diagnosis_result.confidence}")
                for action in inc.diagnosis_result.recommended_actions:
                    print(f"     Recommended Remediation: {action.action} (Tier {action.tier.value})")
        return

    if args.mode == "demo":
        # Quick demo: generate 5 events with 2 anomalies
        producer = SyntheticTelemetryProducer(seed=42)
        events = [

            producer.generate_normal_metrics(),
            producer.generate_anomalous_event("latency_spike"),
            producer.generate_normal_transaction(),
            producer.generate_anomalous_event("error_rate"),
            producer.generate_normal_metrics(),
        ]

        print("\n🚀 Running demo with 5 telemetry events (2 anomalies)...\n")
        incidents = await orchestrator.run_batch(events, dry_run=True)

        print(f"\n{'=' * 60}")
        print(f"📊 Results: {len(incidents)} incidents detected from 5 events")
        for inc in incidents:
            print(f"\n  🔴 Incident {inc.incident_id[:8]}...")
            severity = inc.anomaly_event.severity.value if inc.anomaly_event else "UNKNOWN"
            anomaly_type = inc.anomaly_event.anomaly_type.value if inc.anomaly_event else "UNKNOWN"
            print(f"     Severity: {severity}")
            print(f"     Type: {anomaly_type}")
            print(f"     Status: {inc.status.value}")
            if inc.diagnosis_result:
                print(f"     Root Cause: {inc.diagnosis_result.root_cause_category.value}")
                print(f"     Confidence: {inc.diagnosis_result.confidence}")
            print(f"     Tokens: {inc.total_llm_tokens_used} | Cost: ${inc.total_llm_cost_usd:.4f}")

    elif args.mode == "stream":
        incidents = await orchestrator.run_streaming(
            events_per_second=args.eps,
            anomaly_probability=args.anomaly_prob,
            duration_seconds=args.duration,
            dry_run=args.dry_run,
        )
        print(f"\nStreaming complete: {len(incidents)} incidents detected")

    elif args.mode == "batch":
        producer = SyntheticTelemetryProducer()
        events = producer.generate_batch(count=50, anomaly_fraction=0.1)
        incidents = await orchestrator.run_batch(events, dry_run=True)
        print(f"\nBatch complete: {len(incidents)} incidents from {len(events)} events")


if __name__ == "__main__":
    asyncio.run(main())
