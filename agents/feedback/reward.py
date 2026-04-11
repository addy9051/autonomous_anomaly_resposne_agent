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

from typing import TYPE_CHECKING

from shared.schemas import RootCauseCategory, IncidentStatus
from shared.utils import get_logger

if TYPE_CHECKING:
    from shared.schemas import IncidentRecord, SemanticReward

logger = get_logger("reward_function")

# Baseline metrics (from historical data)
BASELINE_TTM_SECONDS = 2700.0   # 45 minutes baseline MTTR
BASELINE_TTD_SECONDS = 900.0    # 15 minutes baseline MTTA


def compute_reward(incident: IncidentRecord, semantic_reward: SemanticReward | None = None) -> float:
    """
    Compute a hybrid reward for a resolved incident.

    Reward signal components:
    - Extrinsic (60%): MTTR improvement, status success, resolution speed.
    - Intrinsic (40%): Qualitative score from the RewardAgent (LLM-as-a-Judge).

    Returns:
        Hybrid reward value (-1.0 to +1.5)
    """
    extrinsic_r = 0.0

    # ── MTTR improvement reward ──
    if incident.auto_resolved and incident.time_to_mitigate_seconds:
        improvement = 1.0 - (incident.time_to_mitigate_seconds / BASELINE_TTM_SECONDS)
        extrinsic_r += max(0.0, improvement)
    
    # Status-based base rewards
    if incident.auto_resolved:
        extrinsic_r += 0.2
    if incident.false_positive:
        extrinsic_r -= 0.6
    if incident.human_overrode:
        extrinsic_r -= 0.4

    # ── Intrinsic semantic assessment (Phase 7) ──
    intrinsic_r = 0.0
    if semantic_reward:
        # Scale 0.0-1.0 score to a more impactful -1.0 to +1.0 signal
        intrinsic_r = (semantic_reward.overall_quality_score * 2.0) - 1.0
        logger.info(
            "intrinsic_reward_added",
            score=semantic_reward.overall_quality_score,
            scaled_reward=intrinsic_r,
            justification=semantic_reward.justification[:100]
        )
    else:
        # If no semantic reward, we default to no intrinsic signal (neutral 0.0)
        intrinsic_r = 0.0

    # Weighted Hybrid Blend
    total_reward = (extrinsic_r * 0.6) + (intrinsic_r * 0.4)

    logger.info(
        "hybrid_reward_computed",
        incident_id=incident.incident_id,
        extrinsic=round(extrinsic_r, 4),
        intrinsic=round(intrinsic_r, 4),
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
        results.append({
            "incident_id": incident.incident_id,
            "reward": reward,
            "features": features,
            "action": _extract_action_label(incident),
        })
    return results


def _extract_state_features(incident: IncidentRecord) -> list[float]:
    """
    Extract a comprehensive feature vector for RL model training.
    
    Includes normalized metrics, diagnosis metadata, and cost dimensions.
    """
    features = []

    # 1. Anomaly dimensions (7 features)
    if incident.anomaly_event:
        m = incident.anomaly_event.metrics_snapshot
        features.extend([
            min(1.0, (m.p99_latency_ms or 0.0) / 10000.0), # Latency capped at 10s
            min(1.0, (m.error_rate or 0.0)),
            (m.cpu_percent or 0.0) / 100.0,
            (m.memory_percent or 0.0) / 100.0,
            min(1.0, (m.kafka_consumer_lag or 0.0) / 50000.0),
            (m.fraud_score_mean or 0.0),
            incident.anomaly_event.confidence,
        ])
    else:
        features.extend([0.0] * 7)

    # 2. Diagnosis dimensions (4 features)
    if incident.diagnosis_result:
        features.extend([
            incident.diagnosis_result.confidence,
            float(incident.diagnosis_result.is_novel_incident),
            min(1.0, len(incident.diagnosis_result.recommended_actions) / 5.0),
            # Numeric encoding of root cause category (Phase 7 expansion)
            float(list(RootCauseCategory).index(incident.diagnosis_result.root_cause_category)) / 10.0
        ])
    else:
        features.extend([0.0] * 4)

    # 3. Cost & Context (3 features)
    features.extend([
        min(1.0, incident.total_llm_tokens_used / 100000.0),
        min(1.0, (incident.time_to_detect_seconds or 0.0) / 1800.0), # Capped at 30 mins
        float(incident.status == IncidentStatus.RESOLVED)
    ])

    return features


def _extract_action_label(incident: IncidentRecord) -> str:
    """Extract the primary action taken for RL training."""
    if incident.action_results:
        return incident.action_results[0].action_taken
    return "no_action"
