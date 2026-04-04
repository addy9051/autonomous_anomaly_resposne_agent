"""
Reward Function for the Feedback Loop Agent.

Computes reward signals from incident outcomes to train the
contextual bandit model. Reward is shaped by:
- MTTR reduction vs. baseline
- False positive penalty
- Human override penalty (agent's decision was wrong)
- Novel incident handling bonus
"""

from __future__ import annotations

from shared.schemas import IncidentRecord
from shared.utils import get_logger

logger = get_logger("reward_function")

# Baseline metrics (from historical data)
BASELINE_TTM_SECONDS = 2700.0   # 45 minutes baseline MTTR
BASELINE_TTD_SECONDS = 900.0    # 15 minutes baseline MTTA


def compute_reward(incident: IncidentRecord) -> float:
    """
    Compute reward for a resolved incident.

    Reward signal components:
    - MTTR improvement: +1.0 if fully resolved faster than baseline
    - False positive: -0.5 penalty
    - Human override: -0.3 penalty (agent was wrong)
    - Auto-resolved: +0.2 bonus (no human intervention needed)
    - Novel incident handling: +0.15 if correctly identified as novel

    Returns:
        Reward value (typically between -1.0 and +1.5)
    """
    r = 0.0

    # ── MTTR improvement reward ──
    if incident.auto_resolved and incident.time_to_mitigate_seconds:
        improvement = 1.0 - (incident.time_to_mitigate_seconds / BASELINE_TTM_SECONDS)
        r += max(0.0, improvement)  # Only reward improvement, don't penalize for being slower

        logger.debug(
            "reward_mttr",
            ttm=incident.time_to_mitigate_seconds,
            baseline=BASELINE_TTM_SECONDS,
            improvement=improvement,
        )

    # ── Auto-resolution bonus ──
    if incident.auto_resolved:
        r += 0.2

    # ── False positive penalty ──
    if incident.false_positive:
        r -= 0.5
        logger.debug("reward_false_positive_penalty")

    # ── Human override penalty ──
    if incident.human_overrode:
        r -= 0.3
        logger.debug("reward_human_override_penalty")

    # ── Correct detection bonus ──
    if incident.time_to_detect_seconds and incident.time_to_detect_seconds < BASELINE_TTD_SECONDS:
        detection_improvement = 1.0 - (incident.time_to_detect_seconds / BASELINE_TTD_SECONDS)
        r += detection_improvement * 0.3  # Smaller weight for detection
        logger.debug(
            "reward_detection",
            ttd=incident.time_to_detect_seconds,
            improvement=detection_improvement,
        )

    logger.info(
        "reward_computed",
        incident_id=incident.incident_id,
        total_reward=round(r, 4),
        auto_resolved=incident.auto_resolved,
        false_positive=incident.false_positive,
        human_overrode=incident.human_overrode,
    )

    return round(r, 4)


def compute_batch_rewards(incidents: list[IncidentRecord]) -> list[dict]:
    """
    Compute rewards for a batch of incidents.
    Returns list of (incident_id, reward, features) tuples for RL training.
    """
    results = []
    for incident in incidents:
        reward = compute_reward(incident)
        features = _extract_state_features(incident)
        results.append({
            "incident_id": incident.incident_id,
            "reward": reward,
            "features": features,
            "action": _extract_action_label(incident),
        })
    return results


def _extract_state_features(incident: IncidentRecord) -> list[float]:
    """Extract feature vector from incident state for RL model."""
    features = []

    # Anomaly features
    if incident.anomaly_event:
        metrics = incident.anomaly_event.metrics_snapshot
        features.extend([
            metrics.p99_latency_ms or 0.0,
            metrics.error_rate or 0.0,
            metrics.cpu_percent or 0.0,
            metrics.memory_percent or 0.0,
            metrics.kafka_consumer_lag or 0.0,
            metrics.fraud_score_mean or 0.0,
            incident.anomaly_event.confidence,
        ])
    else:
        features.extend([0.0] * 7)

    # Diagnosis features
    if incident.diagnosis_result:
        features.extend([
            incident.diagnosis_result.confidence,
            float(incident.diagnosis_result.is_novel_incident),
            len(incident.diagnosis_result.recommended_actions),
        ])
    else:
        features.extend([0.0] * 3)

    return features


def _extract_action_label(incident: IncidentRecord) -> str:
    """Extract the primary action taken for RL training."""
    if incident.action_results:
        return incident.action_results[0].action_taken
    return "no_action"
