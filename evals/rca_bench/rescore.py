"""Re-score saved predictions with the temporal rule, with no model calls.

The temporal rule ("a change that landed after the earliest symptom cannot be the cause") is a deterministic
function of the scenario, so applying it to a saved prediction gives exactly what the ``temporal`` attribution mode
would have produced from the same diagnosis. That makes it possible to measure the rule on runs that already exist.

Applied to a ``decompose`` run it approximates ``decompose_temporal``: a chosen change that is ruled out becomes "no
change". The approximation is that confidence is capped at the ruled-out cap even when the full diagnosis already
agreed with "none".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from evals.rca_bench.agent_solver import RULED_OUT_CONFIDENCE_CAP, ruled_out_changes

if TYPE_CHECKING:
    from evals.rca_bench.schema import Prediction, Scenario


def apply_temporal_rule_to_prediction(prediction: Prediction, scenario: Scenario) -> Prediction:
    """A copy of the prediction with ruled-out changes removed from suspects and citations."""
    if prediction.error:
        return prediction
    excluded = ruled_out_changes(scenario)
    kept = [s for s in prediction.suspects if s.change_id not in excluded]
    removed = len(kept) != len(prediction.suspects)
    confidence = min(prediction.confidence, RULED_OUT_CONFIDENCE_CAP) if removed and not kept else prediction.confidence
    excluded_refs = {c.evidence_id for c in scenario.changes if c.change_id in excluded}
    return prediction.model_copy(
        update={
            "suspects": kept,
            "confidence": confidence,
            "cited_evidence_ids": [r for r in prediction.cited_evidence_ids if r not in excluded_refs],
            "rationale": prediction.rationale
            if not removed
            else f"[temporal rule removed a change] {prediction.rationale}"[:600],
        }
    )
