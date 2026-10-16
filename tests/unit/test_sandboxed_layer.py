"""Tests for the sandboxed intelligence layer: skills, egress, auto-action policy, episodic memory, severity branches."""

from __future__ import annotations

from collections import defaultdict, deque
from unittest.mock import AsyncMock, MagicMock

import pytest

import orchestrator as orch_module
from agents.action.policy import DeterministicActionPolicy as Policy
from data_pipeline.connectors.alertmanager_intake import parse_alertmanager_payload
from orchestrator import AgentOrchestrator
from shared import egress
from shared.config import get_settings
from shared.episodic_memory import (
    BRANCH_AUTO_FIX,
    BRANCH_CRITICAL,
    EpisodicMemory,
    InMemoryEpisodeBackend,
)
from shared.schemas import (
    ActionPolicyDecision,
    ActionResult,
    ActionTier,
    DiagnosisResult,
    IncidentStatus,
    RecommendedAction,
    RootCauseCategory,
)
from shared.skills import SkillRouter
from shared.utils import LLMCostTracker


# ─── Skills (procedural memory) ─────────────────────────────────


def test_skills_critical_gets_rca_only_not_remediation_skills() -> None:
    names = [s.name for s in SkillRouter().select("latency_spike", "critical", ["checkout"])]
    assert "critical-rca-only" in names
    assert "baseline-conduct" in names
    assert "latency-spike" not in names


def test_skills_non_critical_gets_domain_skill() -> None:
    names = [s.name for s in SkillRouter().select("latency_spike", "medium", ["checkout"])]
    assert "latency-spike" in names
    assert "critical-rca-only" not in names


# ─── Egress guard ───────────────────────────────────────────────


def test_egress_blocks_public_and_allows_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings().app, "egress_enforce", True)
    monkeypatch.setattr(get_settings().app, "egress_allowed_hosts", "")
    with pytest.raises(egress.EgressViolation):
        egress.check_host("93.184.216.34")
    egress.check_host("10.1.2.3")
    egress.check_host("127.0.0.1")


def test_egress_allowlisted_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings().app, "egress_enforce", True)
    monkeypatch.setattr(get_settings().app, "egress_allowed_hosts", "*.datadoghq.com")
    egress.check_host("api.datadoghq.com")


def test_egress_self_test_reports_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings().app, "egress_enforce", True)
    assert egress.egress_self_test() == {"enforced": True, "public_blocked": True}


# ─── Action policy ──────────────────────────────────────────────


def test_policy_denies_everything_for_critical() -> None:
    result = Policy.evaluate("restart_unhealthy_pod", "checkout", "development", {}, dry_run=True, severity="critical")
    assert result.decision == ActionPolicyDecision.DENIED


def test_policy_non_allowlisted_action_is_never_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings().agent, "auto_action_allowlist", "clear_cache")
    result = Policy.evaluate("scale_replicas", "checkout", "production", {"replicas": 3}, dry_run=False, severity="high")
    assert result.decision != ActionPolicyDecision.PERMITTED_AUTO


def test_policy_production_auto_requires_enable_target_and_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    agent_cfg = get_settings().agent
    monkeypatch.setattr(agent_cfg, "auto_action_allowlist", "clear_cache")
    monkeypatch.setattr(agent_cfg, "auto_remediation_enabled", False)
    ok, _ = Policy._auto_allowed("clear_cache", "checkout", False, "production")
    assert not ok

    monkeypatch.setattr(agent_cfg, "auto_remediation_enabled", True)
    monkeypatch.setattr(agent_cfg, "auto_action_target_services", "other-svc")
    ok, _ = Policy._auto_allowed("clear_cache", "checkout", False, "production")
    assert not ok

    monkeypatch.setattr(agent_cfg, "auto_action_target_services", "checkout")
    monkeypatch.setattr(agent_cfg, "auto_action_max_per_hour", 1)
    monkeypatch.setattr(Policy, "_auto_history", defaultdict(deque))
    ok, _ = Policy._auto_allowed("clear_cache", "checkout", False, "production")
    assert ok
    Policy.record_auto_execution("checkout")
    ok, reason = Policy._auto_allowed("clear_cache", "checkout", False, "production")
    assert not ok and "limit" in reason


# ─── Episodic memory + severity branches ────────────────────────


def _alert(severity: str, service: str = "checkout-api") -> dict:
    return {
        "status": "firing",
        "labels": {"alertname": "HighP99Latency", "severity": severity, "service": service},
        "annotations": {"summary": "p99 latency high"},
        "fingerprint": f"fp-{severity}-{service}",
        "startsAt": "2026-01-01T00:00:00Z",
    }


def _make_orchestrator(recover: bool) -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch.alert_dedup = MagicMock()
    orch.alert_dedup.should_fire.return_value = True
    orch.memory = EpisodicMemory(backend=InMemoryEpisodeBackend(), window_minutes=60)
    orch.store = MagicMock()
    orch.active_incidents = {}
    orch.resolved_incidents = []
    orch._update_regressions = MagicMock()

    async def diagnose(anomaly, cost_tracker):  # noqa: ANN001, ANN202
        return DiagnosisResult(
            event_id=anomaly.event_id,
            root_cause="connection pool exhausted",
            root_cause_category=RootCauseCategory.DATABASE,
            recommended_actions=[
                RecommendedAction(action="clear_cache", tier=ActionTier.TIER_1_AUTO, params={})
            ],
            confidence=0.8,
        )

    orch.diagnosis_agent = MagicMock()
    orch.diagnosis_agent.diagnose = AsyncMock(side_effect=diagnose)
    orch.diagnosis_agent.last_skills = ["latency-spike"]

    async def execute(diagnosis, incident, cost_tracker, dry_run=False):  # noqa: ANN001, ANN202
        if incident.anomaly_event.severity.value == "critical":
            incident.status = IncidentStatus.ESCALATED
            return []
        result = ActionResult(
            incident_id=incident.incident_id,
            action_taken="clear_cache",
            tier=ActionTier.TIER_1_AUTO,
            execution_status="success",
        )
        incident.action_results = [result]
        incident.status = IncidentStatus.VERIFIED_RECOVERED if recover else IncidentStatus.ESCALATED
        return [result]

    orch.action_agent = MagicMock()
    orch.action_agent.execute = AsyncMock(side_effect=execute)
    return orch


async def _run(orch: AgentOrchestrator, severity: str):  # noqa: ANN202
    anomaly = parse_alertmanager_payload({"alerts": [_alert(severity)]})[0]
    return await orch.process_anomaly(anomaly, LLMCostTracker(incident_id="pending"), dry_run=True)


@pytest.fixture(autouse=True)
def _no_pagerduty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orch_module, "resolve_pagerduty_incident", AsyncMock())


@pytest.mark.asyncio
async def test_critical_is_rca_only_with_no_actions() -> None:
    orch = _make_orchestrator(recover=True)
    incident = await _run(orch, "critical")
    assert incident.handling_branch == BRANCH_CRITICAL
    assert incident.diagnosis_result.recommended_actions == []
    assert incident.brief["recommended_steps"] == []
    assert incident.brief["fix_applied"] == []
    assert incident.brief["root_cause"] == "connection pool exhausted"
    episode = await orch.memory.get(incident.episode_id)
    assert episode.status == "escalated" and episode.branch == BRANCH_CRITICAL


@pytest.mark.asyncio
async def test_non_critical_auto_fix_is_recorded() -> None:
    orch = _make_orchestrator(recover=True)
    incident = await _run(orch, "medium")
    assert incident.handling_branch == BRANCH_AUTO_FIX
    assert incident.brief["fix_applied"] == ["clear_cache"]
    episode = await orch.memory.get(incident.episode_id)
    assert episode.status == "resolved" and episode.outcome == "recovered"
    assert [e["stage"] for e in await orch.memory.timeline(incident.episode_id)][0] == "intake"


@pytest.mark.asyncio
async def test_duplicate_of_resolved_episode_replays_diagnosis() -> None:
    orch = _make_orchestrator(recover=True)
    first = await _run(orch, "medium")
    second = await _run(orch, "medium")
    assert orch.diagnosis_agent.diagnose.await_count == 1  # second one replayed, not re-diagnosed
    assert second.duplicate_of == first.episode_id
    assert second.diagnosis_result.root_cause == first.diagnosis_result.root_cause
    assert second.diagnosis_result.incident_id == second.incident_id
    assert orch.action_agent.execute.await_count == 2  # policy gate still runs on replay


@pytest.mark.asyncio
async def test_duplicate_of_unresolved_episode_is_suppressed() -> None:
    orch = _make_orchestrator(recover=True)
    await _run(orch, "critical")
    assert await _run(orch, "critical") is None
    assert orch.diagnosis_agent.diagnose.await_count == 1


@pytest.mark.asyncio
async def test_failed_prior_fix_gets_fresh_diagnosis() -> None:
    orch = _make_orchestrator(recover=False)
    await _run(orch, "medium")
    await _run(orch, "medium")
    assert orch.diagnosis_agent.diagnose.await_count == 2
