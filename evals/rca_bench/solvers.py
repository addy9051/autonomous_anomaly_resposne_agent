"""Solvers: anything that turns a Scenario into a Prediction.

The baselines exist to show the benchmark can tell weak strategies from strong ones. If a trivial heuristic
scores as well as the agent, the suite is too easy or the agent adds nothing.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Protocol

from evals.rca_bench.schema import Category, Change, Prediction, Scenario, Suspect

if TYPE_CHECKING:
    from collections.abc import Callable

KIND_TO_CATEGORY: dict[str, Category] = {
    "deploy": "application",
    "config": "application",
    "feature_flag": "application",
    "resource_limit": "infrastructure",
    "infra": "infrastructure",
}
# Heuristics have no calibration, so they state a fixed high confidence. This is deliberate: it shows how
# the calibration metrics expose a solver that is always sure.
HEURISTIC_CONFIDENCE = 0.8


class Solver(Protocol):
    name: str

    async def solve(self, scenario: Scenario) -> Prediction: ...


def _before_alert(changes: list[Change]) -> list[Change]:
    return sorted((c for c in changes if c.offset_s <= 0), key=lambda c: -c.offset_s)


def _prediction_from_change(scenario: Scenario, change: Change | None) -> Prediction:
    if change is None:
        return Prediction(scenario_id=scenario.scenario_id, abstained=True)
    return Prediction(
        scenario_id=scenario.scenario_id,
        suspects=[Suspect(change_id=change.change_id, score=HEURISTIC_CONFIDENCE)],
        category=KIND_TO_CATEGORY.get(change.kind, "unknown"),
        confidence=HEURISTIC_CONFIDENCE,
        cited_evidence_ids=[change.evidence_id],
    )


class MostRecentChangeSolver:
    """Blames the latest change anywhere. The usual first instinct, and a weak one."""

    name = "most_recent_change"

    async def solve(self, scenario: Scenario) -> Prediction:
        recent = _before_alert(scenario.changes)
        return _prediction_from_change(scenario, recent[0] if recent else None)


class AlertServiceRecentChangeSolver:
    """Blames the latest change on the alerting service, else the latest change anywhere."""

    name = "alert_service_recent_change"

    async def solve(self, scenario: Scenario) -> Prediction:
        recent = _before_alert(scenario.changes)
        same_service = [c for c in recent if c.service == scenario.alert.service]
        pick = same_service[0] if same_service else (recent[0] if recent else None)
        return _prediction_from_change(scenario, pick)


class RandomSolver:
    name = "random"

    def __init__(self, seed: int = 0) -> None:
        self._seed = seed

    async def solve(self, scenario: Scenario) -> Prediction:
        rng = random.Random(f"{self._seed}:{scenario.scenario_id}")  # noqa: S311
        if not scenario.changes or rng.random() < 0.2:
            return Prediction(scenario_id=scenario.scenario_id, abstained=True)
        ranked = rng.sample(scenario.changes, len(scenario.changes))
        return Prediction(
            scenario_id=scenario.scenario_id,
            suspects=[
                Suspect(change_id=c.change_id, score=round(HEURISTIC_CONFIDENCE - 0.01 * i, 2))
                for i, c in enumerate(ranked)
            ],
            category=rng.choice(list(KIND_TO_CATEGORY.values())),
            confidence=HEURISTIC_CONFIDENCE,
            cited_evidence_ids=[ranked[0].evidence_id],
        )


class AlwaysAbstainSolver:
    """Never concludes. Safe but useless: scores perfect abstention and zero coverage."""

    name = "always_abstain"

    async def solve(self, scenario: Scenario) -> Prediction:
        return Prediction(scenario_id=scenario.scenario_id, abstained=True)


class OracleSolver:
    """Reads the ground truth. A sanity check that the metrics reach their ceiling, not a real solver."""

    name = "oracle"

    async def solve(self, scenario: Scenario) -> Prediction:
        truth = scenario.ground_truth
        if not truth.answerable:
            return Prediction(scenario_id=scenario.scenario_id, abstained=True)
        suspects = [Suspect(change_id=truth.culprit_change_id, score=0.9)] if truth.culprit_change_id else []
        return Prediction(
            scenario_id=scenario.scenario_id,
            suspects=suspects,
            category=truth.category,
            confidence=0.9,
            cited_evidence_ids=list(truth.supporting_evidence_ids),
        )


def _agent_solver(skip_runbook_search: bool = False) -> Solver:
    from evals.rca_bench.agent_solver import AgentSolver

    return AgentSolver(skip_runbook_search=skip_runbook_search)


SOLVERS: dict[str, Callable[..., Solver]] = {
    "most_recent_change": MostRecentChangeSolver,
    "alert_service_recent_change": AlertServiceRecentChangeSolver,
    "random": RandomSolver,
    "always_abstain": AlwaysAbstainSolver,
    "oracle": OracleSolver,
    "agent": _agent_solver,
}
