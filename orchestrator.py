"""
Main Orchestrator — End-to-End Agent Pipeline.

Coordinates the full incident lifecycle:
  TelemetryEvent → Monitoring Agent → Diagnosis Agent → Action Agent → Feedback Loop

Supports both:
- Streaming mode: continuous processing from Kafka / in-memory event bus
- Batch mode: process a list of events and return results
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from collections import deque

from agents.action.agent import ActionAgent
from agents.diagnosis.graph import DiagnosisAgent
from agents.feedback.agent import FeedbackLoopAgent
from agents.feedback.reward_agent import RewardAgent
from agents.monitoring.agent import MonitoringAgent
from data_pipeline.connectors.synthetic_producer import SyntheticTelemetryProducer
from data_pipeline.flink_jobs.anomaly_features import AlertDeduplicator, RollingWindowAggregator
from shared.schemas import IncidentRecord, IncidentStatus, TelemetryEvent
from shared.utils import LLMCostTracker, Timer, get_logger, setup_logging

logger = get_logger("orchestrator")


class AgentOrchestrator:
    """
    Master orchestrator that wires all agents together.

    Pipeline:
    1. Telemetry events → feature extraction → anomaly detection
    2. Anomaly events → diagnosis (LangGraph DAG)
    3. Diagnosis results → action execution (tiered)
    4. Action outcomes → feedback loop (RL reward)
    """

    def __init__(self) -> None:
        setup_logging()
        self.monitoring_agent = MonitoringAgent()
        self.diagnosis_agent = DiagnosisAgent()
        self.action_agent = ActionAgent()
        self.feedback_agent = FeedbackLoopAgent()
        self.reward_agent = RewardAgent()
        self.feature_aggregator = RollingWindowAggregator()
        self.alert_dedup = AlertDeduplicator()

        # Track active incidents
        self.active_incidents: dict[str, IncidentRecord] = {}
        self.resolved_incidents: list[IncidentRecord] = []
        
        # Real-time telemetry pulse buffer (last 100 events)
        self.telemetry_history: deque[TelemetryEvent] = deque(maxlen=100)

        logger.info("orchestrator_initialized")

    async def process_event(
        self, event: TelemetryEvent, dry_run: bool = False
    ) -> IncidentRecord | None:
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
            anomaly = await self.monitoring_agent.process_event(event, cost_tracker)

            if not anomaly:
                return None

            # Deduplicate alerts
            alert_key = f"{':'.join(anomaly.affected_services)}:{anomaly.anomaly_type.value}"
            if not self.alert_dedup.should_fire(alert_key, anomaly.model_dump(mode="json")):
                suppressed = self.alert_dedup.get_suppressed_count(alert_key)
                logger.debug("alert_suppressed", key=alert_key, suppressed_count=suppressed)
                return None

            # Create incident record
            incident = IncidentRecord(
                status=IncidentStatus.DETECTED,
                anomaly_event=anomaly,
            )
            cost_tracker.incident_id = incident.incident_id
            self.active_incidents[incident.incident_id] = incident

            logger.info(
                "incident_created",
                incident_id=incident.incident_id,
                severity=anomaly.severity.value,
                anomaly_type=anomaly.anomaly_type.value,
            )

            # ── Step 3: Diagnosis Agent — Root Cause Analysis ──
            incident.status = IncidentStatus.DIAGNOSING
            datetime.utcnow()

            diagnosis = await self.diagnosis_agent.diagnose(anomaly, cost_tracker)
            incident.diagnosis_result = diagnosis
            incident.time_to_detect_seconds = (
                datetime.utcnow() - incident.created_at
            ).total_seconds()

            logger.info(
                "diagnosis_complete",
                incident_id=incident.incident_id,
                root_cause=diagnosis.root_cause_category.value,
                confidence=diagnosis.confidence,
                is_novel=diagnosis.is_novel_incident,
            )

            # ── Step 4: Action Agent — Execute Remediation ──
            incident.status = IncidentStatus.ACTION_PENDING
            await self.action_agent.execute(
                diagnosis, incident, cost_tracker, dry_run=dry_run
            )

            incident.time_to_mitigate_seconds = (
                datetime.utcnow() - incident.created_at
            ).total_seconds()

            # ── Step 5: Feedback Loop — Record Outcome (Phase 7 Hybrid) ──
            # First, use LLM-as-a-Judge to evaluate the qualitative resolution
            semantic_reward = await self.reward_agent.evaluate(incident, cost_tracker)
            
            # Sync cost tracker after evaluation
            incident.total_llm_tokens_used = cost_tracker.total_tokens
            incident.total_llm_cost_usd = cost_tracker.total_cost

            # Record final outcome and compute hybrid reward
            reward = await self.feedback_agent.record_outcome(incident, semantic_reward)
            
            logger.info(
                "feedback_recorded",
                incident_id=incident.incident_id,
                reward=reward,
                tokens_used=cost_tracker.total_tokens,
                cost_usd=round(cost_tracker.total_cost, 4),
                justification=semantic_reward.justification[:100] if semantic_reward else "N/A"
            )

            # Move to resolved
            del self.active_incidents[incident.incident_id]
            self.resolved_incidents.append(incident)

        logger.info(
            "pipeline_complete",
            incident_id=incident.incident_id,
            total_elapsed_ms=total_timer.elapsed_ms,
            status=incident.status.value,
        )

        return incident

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
            "feedback_policy": self.feedback_agent.get_policy_status(),
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
    parser.add_argument("--dry-run", action="store_true", default=True, help="Simulate actions")
    args = parser.parse_args()

    orchestrator = AgentOrchestrator()

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

        print(f"\n{'='*60}")
        print(f"📊 Results: {len(incidents)} incidents detected from 5 events")
        for inc in incidents:
            print(f"\n  🔴 Incident {inc.incident_id[:8]}...")
            print(f"     Severity: {inc.anomaly_event.severity.value}")
            print(f"     Type: {inc.anomaly_event.anomaly_type.value}")
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
