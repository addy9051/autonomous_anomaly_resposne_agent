"""
Reward Function for the Feedback Loop Agent.

Computes reward signals from incident outcomes to train the
contextual bandit model. Reward is shaped by correctness signals only:
- Independent verifier outcome (recovered / not recovered; inconclusive is no signal)
- Regression (same problem re-fired after the fix)
- False positive and human override penalties
- Optional LLM-as-a-Judge score

Speed (MTTR/MTTA) is deliberately NOT part of the reward: it rewards acting early
rather than acting correctly and is dominated by approval latency. Track it as a
reported metric instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shared.schemas import IncidentStatus, RootCauseCategory, VerificationOutcome
from shared.utils import get_logger

if TYPE_CHECKING:
    from shared.schemas import IncidentRecord, SemanticReward

logger = get_logger("reward_function")

VERIFIED_RECOVERY_REWARD = 0.6
NOT_RECOVERED_PENALTY = 0.6
REGRESSION_PENALTY = 0.5
NO_REGRESSION_BONUS = 0.1
FALSE_POSITIVE_PENALTY = 0.6
HUMAN_OVERRIDE_PENALTY = 0.4
EXTRINSIC_WEIGHT = 0.6
INTRINSIC_WEIGHT = 0.4


def compute_reward(incident: IncidentRecord, semantic_reward: SemanticReward | None = None) -> float:
    """
    Compute the reward for a resolved incident.

    - Extrinsic: verifier outcome, regression, false-positive and override signals.
      Unknown signals (inconclusive verification, unknown regression) contribute nothing.
    - Intrinsic: LLM-as-a-Judge score. When the judge is unavailable (None) the reward
      is the extrinsic signal alone, not a diluted or neutral blend.
    """
    extrinsic_r = 0.0

    verification = incident.verification_result
    if verification is not None:
        if verification.outcome == VerificationOutcome.VERIFIED_RECOVERY:
            extrinsic_r += VERIFIED_RECOVERY_REWARD
        elif verification.outcome == VerificationOutcome.NOT_RECOVERED:
            extrinsic_r -= NOT_RECOVERED_PENALTY

    if incident.regressed is True:
        extrinsic_r -= REGRESSION_PENALTY
    elif incident.regressed is False and verification is not None:
        extrinsic_r += NO_REGRESSION_BONUS

    if incident.false_positive:
        extrinsic_r -= FALSE_POSITIVE_PENALTY
    if incident.human_overrode:
        extrinsic_r -= HUMAN_OVERRIDE_PENALTY

    if semantic_reward is not None:
        intrinsic_r = (semantic_reward.overall_quality_score * 2.0) - 1.0
        total_reward = extrinsic_r * EXTRINSIC_WEIGHT + intrinsic_r * INTRINSIC_WEIGHT
    else:
        intrinsic_r = None
        total_reward = extrinsic_r

    logger.info(
        "reward_computed",
        incident_id=incident.incident_id,
        extrinsic=round(extrinsic_r, 4),
        intrinsic=None if intrinsic_r is None else round(intrinsic_r, 4),
        judge_available=semantic_reward is not None,
        total=round(total_reward, 4),
    )

    return round(total_reward, 4)


def compute_batch_rewards(incidents: list[IncidentRecord]) -> list[dict]:
    """
    Compute rewards for a batch of incidents.
    Returns list of (incident_id, reward, features) tuples for RL training.
    """
    results = []
    for incident in incidents:
        reward = compute_reward(incident)
        features = _extract_state_features(incident)
        results.append(
            {
                "incident_id": incident.incident_id,
                "reward": reward,
                "features": features,
                "action": _extract_action_label(incident),
            }
        )
    return results


def _extract_state_features(incident: IncidentRecord) -> list[float]:
    """
    Extract a comprehensive feature vector for RL model training.

    Includes normalized metrics, diagnosis metadata, and cost dimensions.
    """
    features = []

    # 1. Anomaly dimensions (5 features)
    if incident.anomaly_event:
        m = incident.anomaly_event.metrics_snapshot
        features.extend(
            [
                min(1.0, (m.p99_latency_ms or 0.0) / 10000.0),  # Latency capped at 10s
                min(1.0, (m.error_rate or 0.0)),
                (m.cpu_percent or 0.0) / 100.0,
                (m.memory_percent or 0.0) / 100.0,
                incident.anomaly_event.confidence,
            ]
        )
    else:
        features.extend([0.0] * 5)

    # 2. Diagnosis dimensions (4 features)
    if incident.diagnosis_result:
        features.extend(
            [
                incident.diagnosis_result.confidence,
                float(incident.diagnosis_result.is_novel_incident),
                min(1.0, len(incident.diagnosis_result.recommended_actions) / 5.0),
                # Numeric encoding of root cause category (Phase 7 expansion)
                float(list(RootCauseCategory).index(incident.diagnosis_result.root_cause_category)) / 10.0,
            ]
        )
    else:
        features.extend([0.0] * 4)

    # 3. Cost & Context (3 features)
    features.extend(
        [
            min(1.0, incident.total_llm_tokens_used / 100000.0),
            min(1.0, (incident.time_to_detect_seconds or 0.0) / 1800.0),  # Capped at 30 mins
            float(incident.status == IncidentStatus.RESOLVED),
        ]
    )

    return features


def _extract_action_label(incident: IncidentRecord) -> str:
    """Extract the primary action taken for RL training."""
    if incident.action_results:
        return incident.action_results[0].action_taken
    return "no_action"
