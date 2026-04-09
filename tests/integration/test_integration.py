from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.action.agent import ActionAgent
from agents.diagnosis.graph import DiagnosisAgent
from shared.schemas import AnomalyEvent, IncidentRecord, IncidentStatus, RootCauseCategory, Severity


@pytest.mark.asyncio
async def test_end_to_end_mocked_incident_flow() -> None:
    """
    Simulates a full end-to-end integration test.
    Mocks the graph output to ensure predictable state transitions.
    """
    # 1. Provide a mocked Anomaly Event from the Data Pipeline
    event = AnomalyEvent(
        event_id="test-event-uuid-1234",
        timestamp="2026-04-09T00:00:00Z",
        severity=Severity.HIGH,
        affected_services=["payment-gateway"],
        anomaly_type="latency_spike",
        metrics_snapshot={"p99_latency_ms": 1500, "error_rate": 0.05},
        reasoning="Mocked anomaly detection triggered.",
        confidence=0.95
    )

    # 2. Patch the graph invocation and action execution
    with patch("agents.diagnosis.graph.build_diagnosis_graph") as mock_build, \
         patch("agents.action.agent.ChatOpenAI") as mock_action_llm, \
         patch("agents.action.agent.trigger_workflow") as mock_trigger_workflow:

        # Mock theCompiled Graph
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={
            "diagnosis_result": {
                "root_cause": "Test Cause",
                "root_cause_category": "application",
                "runbook_references": [],
                "recommended_actions": [{"action": "scale_replicas", "tier": 1, "params": {}}],
                "reasoning_chain": "Scale it",
                "confidence": 0.99
            }
        })
        mock_build.return_value = mock_graph

        # Mock the Action LLM response (for summary)
        mock_action_instance = MagicMock()
        mock_action_instance.ainvoke = AsyncMock(
            return_value=MagicMock(content="Root Cause: Test summary\nActions Taken: Scaled\nStatus: Resolved")
        )
        mock_action_llm.return_value = mock_action_instance
        # Mock trigger_workflow
        mock_trigger_workflow.return_value = {"status": "success", "execution_id": "999"}

        # 3. Execute Diagnosis Agent
        diagnosis_agent = DiagnosisAgent()
        diagnosis_result = await diagnosis_agent.diagnose(event)

        assert diagnosis_result.event_id == "test-event-uuid-1234"
        assert diagnosis_result.root_cause_category == RootCauseCategory.APPLICATION

        # 4. Execute Action Agent
        action_agent = ActionAgent()

        incident = IncidentRecord(
            incident_id=diagnosis_result.incident_id,
            event_id=event.event_id,
            status=IncidentStatus.DETECTED,
            created_at=datetime.utcnow()
        )

        action_results = await action_agent.execute(diagnosis_result, incident)

        assert incident.status == IncidentStatus.RESOLVED
        assert incident.auto_resolved
        assert action_results[0].action_taken == "scale_replicas"

        mock_trigger_workflow.assert_called_once()
