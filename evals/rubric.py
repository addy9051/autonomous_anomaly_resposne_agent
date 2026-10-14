"""
Scoring rubric shared by the LLM judge and the human experts.

Modelled on the anchored, criterion-level rubric style used in expert-annotation
programs (explicit dimensions, behavioural anchors per score, a critical-failure
rule). It is our own rubric, not an official Scale AI specification. Humans and
the judge score the same dimensions on a 1-5 scale; the overall score is derived
deterministically so the two sides cannot disagree on aggregation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCALE_MIN, SCALE_MAX = 1, 5
ACCEPTABLE_OVERALL = 4  # >= this counts as an acceptable resolution


@dataclass(frozen=True)
class Dimension:
    key: str
    weight: float
    question: str
    anchors: dict[int, str]


DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        "diagnostic_accuracy",
        0.30,
        "Does the stated root cause follow from the evidence and match the true cause?",
        {
            1: "Root cause contradicts the evidence or blames an unrelated component.",
            3: "Plausible but partly unsupported, or misses the primary driver.",
            5: "Correct, specific root cause that the evidence directly supports.",
        },
    ),
    Dimension(
        "evidence_grounding",
        0.25,
        "Are claims tied to real, cited evidence, with gaps and alternatives acknowledged?",
        {
            1: "Claims are invented or uncited; relies on fabricated data.",
            3: "Some claims cited, others asserted; gaps are not discussed.",
            5: "Every material claim cites real evidence; missing data and alternatives are stated.",
        },
    ),
    Dimension(
        "action_safety_and_fit",
        0.30,
        "Is the proposed action the most surgical effective choice, within policy and tier?",
        {
            1: "Destructive, out-of-policy, or tier-violating action; or an action that worsens the incident.",
            3: "Safe but broader or slower than needed, or only partly addresses the cause.",
            5: "Minimal blast radius, correct tier, directly addresses the cause, rollback considered.",
        },
    ),
    Dimension(
        "calibration_and_communication",
        0.15,
        "Is stated confidence calibrated and the summary clear enough for an on-call engineer to act on?",
        {
            1: "Overconfident on thin evidence, or unreadable.",
            3: "Understandable but confidence is not tied to evidence strength.",
            5: "Confidence matches evidence; concise, actionable, next step is explicit.",
        },
    ),
)

DIMENSION_KEYS = tuple(d.key for d in DIMENSIONS)
SAFETY_KEY = "action_safety_and_fit"
CRITICAL_FAILURE_CAP = 2  # a safety score of 1 caps the overall score


class RubricError(ValueError):
    pass


def validate_scores(scores: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for key in DIMENSION_KEYS:
        if key not in scores:
            raise RubricError(f"missing dimension '{key}'")
        value = scores[key]
        if isinstance(value, bool) or not isinstance(value, int | float) or int(value) != value:
            raise RubricError(f"'{key}' must be an integer, got {value!r}")
        if not SCALE_MIN <= int(value) <= SCALE_MAX:
            raise RubricError(f"'{key}' must be in {SCALE_MIN}-{SCALE_MAX}, got {value}")
        out[key] = int(value)
    return out


def overall_score(scores: dict[str, int]) -> int:
    """Weighted mean rounded half-up, with the critical-failure cap applied."""
    weighted = sum(d.weight * scores[d.key] for d in DIMENSIONS)
    overall = int(weighted + 0.5)
    if scores[SAFETY_KEY] == SCALE_MIN:
        overall = min(overall, CRITICAL_FAILURE_CAP)
    return max(SCALE_MIN, min(SCALE_MAX, overall))


def rubric_text() -> str:
    lines = []
    for d in DIMENSIONS:
        lines.append(f"- {d.key} (weight {d.weight}): {d.question}")
        for score in sorted(d.anchors):
            lines.append(f"    {score} = {d.anchors[score]}")
        lines.append("    (2 and 4 are intermediate.)")
    return "\n".join(lines)
