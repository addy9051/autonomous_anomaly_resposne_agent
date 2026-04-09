"""
Feedback Loop Agent — Contextual Bandit for Continuous Improvement.

Observes (state → action → outcome) triplets from resolved incidents.
Trains a contextual bandit model to improve action selection over time.
Supports A/B testing of policy candidates before full rollout.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import numpy as np
from sklearn.linear_model import SGDClassifier
from sklearn.preprocessing import StandardScaler

from agents.feedback.reward import compute_batch_rewards, compute_reward
from shared.config import get_settings
from langfuse import Langfuse
from shared.schemas import IncidentRecord
from shared.utils import get_logger, get_tracer

logger = get_logger("feedback_agent")
tracer = get_tracer()


class FeedbackLoopAgent:
    """
    Feedback Loop Agent — contextual bandit for adaptive action selection.

    Uses a simplified contextual bandit approach (epsilon-greedy with
    linear model) for development. In production, this would be replaced
    by Vertex AI RL or Azure Personalizer.
    """

    def __init__(self, epsilon: float = 0.1) -> None:
        self.settings = get_settings()
        self.epsilon = epsilon  # Exploration rate

        # Simple linear model for action-value estimation
        self.scaler = StandardScaler()
        self.model = SGDClassifier(
            loss="log_loss",
            penalty="l2",
            alpha=0.001,
            random_state=42,
            warm_start=True,
        )

        # Training data buffer
        self.experience_buffer: list[dict[str, Any]] = []
        self.action_rewards: dict[str, list[float]] = defaultdict(list)
        self.is_fitted = False

        # Policy versioning
        self.policy_version = "v1.0.0"
        self.policy_history: list[dict] = []

        logger.info(
            "feedback_agent_initialized",
            epsilon=epsilon,
            policy_version=self.policy_version,
        )

    async def record_outcome(self, incident: IncidentRecord) -> float:
        """
        Record the outcome of a resolved incident and compute reward.

        Args:
            incident: A resolved incident record

        Returns:
            Computed reward value
        """
        with tracer.start_as_current_span("feedback_agent.record_outcome") as span:
            reward = compute_reward(incident)

            # Extract features and action
            from agents.feedback.reward import _extract_state_features, _extract_action_label
            features = _extract_state_features(incident)
            action = _extract_action_label(incident)

            # Add to experience buffer
            self.experience_buffer.append({
                "incident_id": incident.incident_id,
                "features": features,
                "action": action,
                "reward": reward,
            })

            # Track per-action rewards
            self.action_rewards[action].append(reward)

            span.set_attribute("reward", reward)
            span.set_attribute("action", action)
            span.set_attribute("buffer_size", len(self.experience_buffer))

            logger.info(
                "outcome_recorded",
                incident_id=incident.incident_id,
                action=action,
                reward=reward,
                buffer_size=len(self.experience_buffer),
            )

            # Send reward to Langfuse trace
            try:
                langfuse = Langfuse(
                    public_key=self.settings.observability.langfuse_public_key,
                    secret_key=self.settings.observability.langfuse_secret_key,
                    host=self.settings.observability.langfuse_host
                )
                langfuse.score(
                    trace_id=incident.incident_id,
                    name="reward",
                    value=reward
                )
            except Exception as e:
                logger.warning("failed_to_score_langfuse", error=str(e))

            # Periodically retrain (every 50 experiences)
            if len(self.experience_buffer) % 50 == 0 and len(self.experience_buffer) >= 50:
                await self.retrain_policy()

            return reward

    async def suggest_action(self, features: list[float]) -> dict[str, Any]:
        """
        Suggest an action based on current state features.
        Uses epsilon-greedy exploration.

        Args:
            features: State feature vector

        Returns:
            Dict with suggested action, confidence, and exploration flag
        """
        rng = np.random.default_rng()

        # Epsilon-greedy exploration
        if not self.is_fitted or rng.random() < self.epsilon:
            # Explore: random action from known actions
            actions = list(self.action_rewards.keys()) or ["scale_replicas"]
            action = rng.choice(actions)
            return {
                "action": action,
                "confidence": 0.5,
                "is_exploration": True,
                "policy_version": self.policy_version,
            }

        # Exploit: use trained model
        try:
            X = np.array(features).reshape(1, -1)
            X_scaled = self.scaler.transform(X)
            action = self.model.predict(X_scaled)[0]
            probs = self.model.predict_proba(X_scaled)[0]
            confidence = float(max(probs))

            return {
                "action": action,
                "confidence": confidence,
                "is_exploration": False,
                "policy_version": self.policy_version,
            }
        except Exception as e:
            logger.warning("model_prediction_failed", error=str(e))
            return {
                "action": "scale_replicas",
                "confidence": 0.3,
                "is_exploration": True,
                "policy_version": self.policy_version,
            }

    async def retrain_policy(self) -> dict[str, Any]:
        """
        Retrain the policy model on accumulated experience.

        Returns:
            Training metrics and new policy version
        """
        if len(self.experience_buffer) < 10:
            return {"status": "insufficient_data", "buffer_size": len(self.experience_buffer)}

        logger.info("retraining_policy", buffer_size=len(self.experience_buffer))

        # Filter to positive-reward experiences for training
        training_data = [
            exp for exp in self.experience_buffer
            if exp["reward"] > 0
        ]

        if len(training_data) < 5:
            return {"status": "insufficient_positive_examples"}

        # Prepare training data
        X = np.array([exp["features"] for exp in training_data])
        y = [exp["action"] for exp in training_data]

        # Fit scaler and model
        X_scaled = self.scaler.fit_transform(X)
        self.model.partial_fit(X_scaled, y, classes=list(set(y)))
        self.is_fitted = True

        # Update policy version
        old_version = self.policy_version
        version_num = int(self.policy_version.split(".")[-1]) + 1
        self.policy_version = f"v1.0.{version_num}"

        metrics = {
            "status": "retrained",
            "old_version": old_version,
            "new_version": self.policy_version,
            "training_examples": len(training_data),
            "total_buffer": len(self.experience_buffer),
            "mean_reward": float(np.mean([exp["reward"] for exp in training_data])),
            "unique_actions": len(set(y)),
        }

        self.policy_history.append(metrics)
        logger.info("policy_retrained", **metrics)

        return metrics

    def get_action_stats(self) -> dict[str, dict[str, float]]:
        """Get per-action reward statistics."""
        stats = {}
        for action, rewards in self.action_rewards.items():
            stats[action] = {
                "count": len(rewards),
                "mean_reward": round(float(np.mean(rewards)), 4),
                "std_reward": round(float(np.std(rewards)), 4),
                "max_reward": round(float(max(rewards)), 4),
                "min_reward": round(float(min(rewards)), 4),
            }
        return stats

    def get_policy_status(self) -> dict[str, Any]:
        """Get current policy status and training history."""
        return {
            "current_version": self.policy_version,
            "is_fitted": self.is_fitted,
            "epsilon": self.epsilon,
            "buffer_size": len(self.experience_buffer),
            "unique_actions": len(self.action_rewards),
            "action_stats": self.get_action_stats(),
            "history": self.policy_history[-5:],  # Last 5 retrains
        }
