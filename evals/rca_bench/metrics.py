"""Benchmark metrics. Pure functions over (scenario, prediction) pairs; no I/O and no randomness."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from evals.rca_bench.schema import Prediction, Scenario

CONFIDENT_THRESHOLD = 0.7
LOW_CONFIDENCE_THRESHOLD = 0.4
ECE_BINS = 5
SCORING_VERSION = 2


@dataclass(frozen=True)
class Pair:
    scenario: Scenario
    prediction: Prediction


def worst_case_rank(pred: Prediction, change_id: str) -> int | None:
    """Rank of a change among the suspects, with ties broken against the solver.

    Implicating several changes at the same score is hedging, not ranking, so every tied suspect counts as
    ahead of the culprit. Without this, a solver that blames everything is credited whenever the culprit
    happens to be listed first.
    """
    scores = {s.change_id: s.score for s in pred.suspects}
    if change_id not in scores:
        return None
    mine = scores[change_id]
    return sum(1 for value in scores.values() if value >= mine)


def top_k_correct(pair: Pair, k: int) -> bool:
    """Whether the change attribution is right.

    When no listed change caused the incident, the right answer is to implicate none. Otherwise the culprit
    must rank within the first k suspects (ties count against the solver). Abstaining on an answerable
    scenario is a miss.
    """
    truth = pair.scenario.ground_truth
    pred = pair.prediction
    if pred.error or not truth.answerable:
        return False
    if truth.culprit_change_id is None:
        return not pred.abstained and not pred.suspects
    if pred.abstained:
        return False
    rank = worst_case_rank(pred, truth.culprit_change_id)
    return rank is not None and rank <= k


def is_correct(pair: Pair) -> bool:
    """Overall correctness used for calibration: right on an answerable case, or abstained on an unanswerable one."""
    if pair.prediction.error:
        return False
    truth = pair.scenario.ground_truth
    if not truth.answerable:
        return pair.prediction.abstained
    return top_k_correct(pair, 1)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def expected_calibration_error(points: list[tuple[float, bool]], bins: int = ECE_BINS) -> float | None:
    """Weighted gap between stated confidence and observed accuracy over equal-width confidence bins."""
    if not points:
        return None
    buckets: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for conf, ok in points:
        buckets[min(int(conf * bins), bins - 1)].append((conf, ok))
    total = len(points)
    ece = 0.0
    for items in buckets.values():
        avg_conf = sum(c for c, _ in items) / len(items)
        acc = sum(1 for _, ok in items if ok) / len(items)
        ece += len(items) / total * abs(avg_conf - acc)
    return round(ece, 4)


def brier_score(points: list[tuple[float, bool]]) -> float | None:
    if not points:
        return None
    return round(sum((c - (1.0 if ok else 0.0)) ** 2 for c, ok in points) / len(points), 4)


@dataclass
class Metrics:
    n: int
    n_answerable: int
    n_unanswerable: int
    top1_change_accuracy: float | None
    top3_change_accuracy: float | None
    category_accuracy: float | None
    abstention_recall: float | None
    abstention_precision: float | None
    false_abstention_rate: float | None
    confident_error_rate: float | None
    coverage: float | None
    ece: float | None
    brier: float | None
    citation_validity: float | None
    grounded_correct_rate: float | None
    error_rate: float | None
    mean_latency_s: float | None
    low_confidence_when_unanswerable: float | None = None
    low_confidence_when_answerable: float | None = None
    predicted_categories: dict[str, int] = field(default_factory=dict)
    by_fault_class: dict[str, dict[str, Any]] = field(default_factory=dict)


def compute_metrics(pairs: list[Pair]) -> Metrics:
    answerable = [p for p in pairs if p.scenario.ground_truth.answerable]
    unanswerable = [p for p in pairs if not p.scenario.ground_truth.answerable]

    abstained = [p for p in pairs if p.prediction.abstained]
    abstained_unanswerable = [p for p in abstained if not p.scenario.ground_truth.answerable]
    abstained_answerable = [p for p in abstained if p.scenario.ground_truth.answerable]

    category_hits = sum(
        1
        for p in answerable
        if not p.prediction.abstained
        and not p.prediction.error
        and p.prediction.category == p.scenario.ground_truth.category
    )

    # A confident answer that is wrong is the failure SREs distrust most.
    confident_errors = sum(1 for p in pairs if p.prediction.confidence >= CONFIDENT_THRESHOLD and not is_correct(p))

    answered = [p for p in pairs if not p.prediction.abstained and not p.prediction.error]
    calibration_points = [(p.prediction.confidence, is_correct(p)) for p in answered]

    cited = [i for p in pairs for i in p.prediction.cited_evidence_ids]
    valid = sum(1 for p in pairs for i in p.prediction.cited_evidence_ids if i in p.scenario.evidence_ids)

    correct_with_culprit = [p for p in answerable if p.scenario.ground_truth.culprit_change_id and top_k_correct(p, 1)]
    grounded = sum(
        1
        for p in correct_with_culprit
        if set(p.prediction.cited_evidence_ids) & set(p.scenario.ground_truth.supporting_evidence_ids)
    )

    latencies = [p.prediction.latency_s for p in pairs if p.prediction.latency_s > 0]

    # Secondary signal, declared before the agent was scored a second time: does the solver at least flag
    # low confidence on cases that cannot be answered? Errors are excluded.
    def low_confidence(items: list[Pair]) -> int:
        return sum(1 for p in items if not p.prediction.error and p.prediction.confidence <= LOW_CONFIDENCE_THRESHOLD)

    predicted_categories: dict[str, int] = defaultdict(int)
    for p in answered:
        predicted_categories[p.prediction.category] += 1

    by_class: dict[str, list[Pair]] = defaultdict(list)
    for p in pairs:
        by_class[p.scenario.ground_truth.fault_class].append(p)
    class_breakdown = {
        name: {
            "n": len(items),
            "correct": sum(1 for p in items if is_correct(p)),
            "accuracy": _rate(sum(1 for p in items if is_correct(p)), len(items)),
        }
        for name, items in sorted(by_class.items())
    }

    return Metrics(
        n=len(pairs),
        n_answerable=len(answerable),
        n_unanswerable=len(unanswerable),
        top1_change_accuracy=_rate(sum(top_k_correct(p, 1) for p in answerable), len(answerable)),
        top3_change_accuracy=_rate(sum(top_k_correct(p, 3) for p in answerable), len(answerable)),
        category_accuracy=_rate(category_hits, len(answerable)),
        abstention_recall=_rate(len(abstained_unanswerable), len(unanswerable)),
        abstention_precision=_rate(len(abstained_unanswerable), len(abstained)),
        false_abstention_rate=_rate(len(abstained_answerable), len(answerable)),
        confident_error_rate=_rate(confident_errors, len(pairs)),
        coverage=_rate(len(answered), len(pairs)),
        ece=expected_calibration_error(calibration_points),
        brier=brier_score(calibration_points),
        citation_validity=_rate(valid, len(cited)),
        grounded_correct_rate=_rate(grounded, len(correct_with_culprit)),
        error_rate=_rate(sum(1 for p in pairs if p.prediction.error), len(pairs)),
        mean_latency_s=round(sum(latencies) / len(latencies), 3) if latencies else None,
        low_confidence_when_unanswerable=_rate(low_confidence(unanswerable), len(unanswerable)),
        low_confidence_when_answerable=_rate(low_confidence(answerable), len(answerable)),
        predicted_categories=dict(sorted(predicted_categories.items())),
        by_fault_class=class_breakdown,
    )
