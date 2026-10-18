"""
Unit tests for Story-Based Incident Simulation Engine.

Tests:
- 'The Bad Deploy' scenario generation and chronological ordering
- 'Database Resource Lock' scenario generation and saturation metrics
- Story streaming and delay execution
- SimulatorAdapter scenario integration
- Invalid scenario validation
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data_pipeline.connectors.adapters import SimulatorAdapter
from data_pipeline.connectors.story_simulation import (
    IncidentScenario,
    StorySimulationEngine,
)
from shared.schemas import Severity, TelemetrySignalType


class TestStorySimulationEngine:
    """Tests for StorySimulationEngine."""

    def test_generate_bad_deploy_scenario(self) -> None:
        engine = StorySimulationEngine()
        base_time = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
        events = engine.generate_bad_deploy_scenario(base_time=base_time)

        assert len(events) == 3

        # Step 1: Deploy event at T=0
        deploy_event = events[0]
        assert deploy_event.timestamp == base_time
        assert deploy_event.signal_type == TelemetrySignalType.BUSINESS_SIGNAL
        assert deploy_event.metric_name == "deployment_event"
        assert deploy_event.tags["deployment_version"] == "v1.1.0"
        assert deploy_event.tags["previous_version"] == "v1.0.0"

        # Step 2: Latency spike at T+1m
        latency_event = events[1]
        assert (latency_event.timestamp - base_time).total_seconds() == 60
        assert latency_event.metric_name == "latency_p99_ms"
        assert latency_event.value == 2400.0
        assert latency_event.baseline_mean == 180.0
        assert latency_event.deviation_sigma == 4.8
        assert latency_event.severity == Severity.HIGH

        # Step 3: Error surge at T+2m
        error_event = events[2]
        assert (error_event.timestamp - base_time).total_seconds() == 120
        assert error_event.metric_name == "error_rate"
        assert error_event.value == 6.2
        assert error_event.severity == Severity.CRITICAL
        assert error_event.tags["error_code"] == "GATEWAY_TIMEOUT"

    def test_generate_database_lock_scenario(self) -> None:
        engine = StorySimulationEngine()
        base_time = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
        events = engine.generate_database_lock_scenario(base_time=base_time)

        assert len(events) == 3

        # Step 1: Normal traffic baseline
        normal_traffic = events[0]
        assert normal_traffic.timestamp == base_time
        assert normal_traffic.metric_name == "request_rate"
        assert normal_traffic.value == 500.0
        assert normal_traffic.severity == Severity.LOW

        # Step 2: Database CPU saturation and pool lock
        db_sat = events[1]
        assert (db_sat.timestamp - base_time).total_seconds() == 60
        assert db_sat.entity == "payment-postgres-db"
        assert db_sat.metric_name == "database_cpu_percent"
        assert db_sat.value == 99.2
        assert db_sat.tags["active_connections"] == 100
        assert db_sat.tags["pool_exhausted"] is True
        assert db_sat.severity == Severity.CRITICAL

        # Step 3: Transaction delay with low app CPU
        txn_delay = events[2]
        assert (txn_delay.timestamp - base_time).total_seconds() == 120
        assert txn_delay.metric_name == "transaction_latency_ms"
        assert txn_delay.value == 3200.0
        assert txn_delay.tags["app_server_cpu_percent"] < 20.0
        assert txn_delay.tags["database_bound"] is True

    def test_get_scenario_events_aliases(self) -> None:
        engine = StorySimulationEngine()
        events_deploy = engine.get_scenario_events("bad_deploy")
        assert len(events_deploy) == 3
        assert events_deploy[0].metric_name == "deployment_event"

        events_db = engine.get_scenario_events("database_lock")
        assert len(events_db) == 3
        assert events_db[1].metric_name == "database_cpu_percent"

    def test_unknown_scenario_raises_value_error(self) -> None:
        engine = StorySimulationEngine()
        with pytest.raises(ValueError, match="Unknown scenario"):
            engine.get_scenario_events("unsupported_scenario_123")

    @pytest.mark.asyncio
    async def test_stream_scenario(self) -> None:
        engine = StorySimulationEngine()
        collected = []
        async for event in engine.stream_scenario(IncidentScenario.BAD_DEPLOY, delay_between_steps=0.01):
            collected.append(event)
        assert len(collected) == 3

    def test_list_scenarios_metadata(self) -> None:
        scenarios = StorySimulationEngine.list_scenarios()
        assert len(scenarios) == 2
        ids = [s["id"] for s in scenarios]
        assert "bad_deploy" in ids
        assert "database_lock" in ids


class TestSimulatorAdapterScenarioIntegration:
    """Tests for SimulatorAdapter integration with story scenarios."""

    def test_simulator_adapter_get_story(self) -> None:
        adapter = SimulatorAdapter()
        events = adapter.get_story_scenario("bad_deploy")
        assert len(events) == 3
        assert events[0].metric_name == "deployment_event"

    @pytest.mark.asyncio
    async def test_simulator_adapter_stream_story(self) -> None:
        adapter = SimulatorAdapter()
        collected = []
        async for event in adapter.stream_story_scenario("database_lock", delay_between_steps=0.01):
            collected.append(event)
        assert len(collected) == 3

    def test_simulator_adapter_list_stories(self) -> None:
        adapter = SimulatorAdapter()
        stories = adapter.list_story_scenarios()
        assert len(stories) == 2
