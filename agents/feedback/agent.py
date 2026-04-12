"""
Feedback Loop Agent — Contextual Bandit for Continuous Improvement.

Observes (state → action → outcome) triplets from resolved incidents.
Trains a contextual bandit model to improve action selection over time.
Supports A/B testing of policy candidates before full rollout.
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from typing import TYPE_CHECKING, Any

import numpy as np
from google.cloud import storage
from vowpalwabbit import pyvw

from agents.feedback.reward import compute_reward
from shared.config import get_settings
from shared.utils import get_logger, get_tracer

if TYPE_CHECKING:
    from shared.schemas import IncidentRecord, SemanticReward

logger = get_logger("feedback_agent")
tracer = get_tracer()


class FeedbackLoopAgent:
    """
    Feedback Loop Agent — Contextual Bandit powered by Vowpal Wabbit.

    Uses the cb_explore_adf (Action Dependent Features) algorithm for
    online learning. VW is the gold standard for reliable, high-throughput
    contextual bandits in production systems.
    """

    def __init__(self, epsilon: float = 0.2) -> None:
        self.settings = get_settings()
        self.epsilon = epsilon  # Exploration rate

        # Initialize VW with CB Explore ADF
        # --cb_explore_adf: Multiline format for actions
        # --epsilon: Epsilon-greedy exploration
        # --quiet: suppress stdout
        vw_params = (
            f"--cb_explore_adf --epsilon {epsilon} --bit_precision 18 "
            f"--quiet --cb_type mtr"  # Model-based Training with Reward
        )
        self.vw = pyvw.vw(vw_params)

        # A/B Testing: Experimental model with aggressive exploration
        vw_experimental_params = vw_params.replace(f"--epsilon {epsilon}", "--epsilon 0.20")
        self.vw_experimental = pyvw.vw(vw_experimental_params)

        # Persistence location
        self.model_path = "data/models/feedback_policy.vw"
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)

        # Buffer for observability / debugging
        self.experience_buffer: list[dict[str, Any]] = []
        self.action_rewards: dict[str, list[float]] = defaultdict(list)

        # Policy versioning
        self.policy_version = "v2.0.0-vw"
        self.policy_history: list[dict] = []

        # GCS Distributed Settings
        if self.settings.app.vw_model_gcs_bucket:
            self.gcs_client = storage.Client()
            self.bucket_name = self.settings.app.vw_model_gcs_bucket
            self.blob_name = f"models/{self.policy_version}.vw"
        else:
            self.gcs_client = None

        # Background sync state
        self.sync_task: asyncio.Task | None = None

        # Start background sync if we are not a trainer and GCS is configured
        if self.gcs_client and not self.settings.app.vw_is_trainer:
            self.start_sync_loop()

        logger.info(
            "feedback_agent_initialized_vw", epsilon=epsilon, policy_version=self.policy_version, params=vw_params
        )

    async def _init_redis(self) -> None:
        if hasattr(self, "redis_client"):
            return
        # Lazy load redis
        import redis.asyncio as redis

        self.redis_client = redis.Redis.from_url(self.settings.data.redis_url)
        await self.load_policy()

    async def save_policy(self) -> None:
        """Serialize and save the VW model binary and status to filesystem/Redis."""
        try:
            # 1. Save VW binary model
            self.vw.save(self.model_path)

            # 2. Upload to GCS if we are the trainer
            if self.gcs_client and self.settings.app.vw_is_trainer:
                await self.sync_model_to_gcs()

            # 2. Save metadata to Redis for cross-instance sync
            import pickle

            state = {
                "experience_buffer": self.experience_buffer[-1000:],
                "action_rewards": self.action_rewards,
                "policy_version": self.policy_version,
            }
            await self.redis_client.set("feedback_agent_metadata", pickle.dumps(state))  # noqa: S301
            logger.info("policy_saved", version=self.policy_version, path=self.model_path)
        except Exception as e:
            logger.error("failed_to_save_policy", error=str(e))

    async def load_policy(self) -> None:
        """Load the VW model and metadata."""
        try:
            # 1. Sync from GCS if available
            if self.gcs_client:
                await self.sync_model_from_gcs()
            if os.path.exists(self.model_path):
                self.vw = pyvw.vw(f"-i {self.model_path} --quiet")
                logger.debug("vw_model_binary_loaded", path=self.model_path)

            import pickle

            state_bytes = await self.redis_client.get("feedback_agent_metadata")
            if state_bytes:
                state = pickle.loads(state_bytes)  # noqa: S301
                self.experience_buffer = state.get("experience_buffer", [])
                self.action_rewards = state.get("action_rewards", self.action_rewards)
                self.policy_version = state.get("policy_version", self.policy_version)
                logger.info("policy_metadata_loaded", version=self.policy_version)
        except Exception as e:
            logger.error("failed_to_load_policy", error=str(e))

    async def sync_model_to_gcs(self) -> None:
        """Upload the local model binary to Google Cloud Storage."""
        if not self.gcs_client or not os.path.exists(self.model_path):
            return

        try:
            bucket = self.gcs_client.bucket(self.bucket_name)
            blob = bucket.blob(self.blob_name)

            # Use blocking call in a thread to keep async loop happy
            import asyncio

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, blob.upload_from_filename, self.model_path)

            logger.info("model_uploaded_to_gcs", bucket=self.bucket_name, blob=self.blob_name)
        except Exception as e:
            logger.error("gcs_upload_failed", error=str(e))

    async def sync_model_from_gcs(self) -> None:
        """Download the shared model binary from Google Cloud Storage."""
        if not self.gcs_client:
            return

        try:
            bucket = self.gcs_client.bucket(self.bucket_name)
            blob = bucket.blob(self.blob_name)

            if not blob.exists():
                logger.info("gcs_model_not_found_skipping_sync", blob=self.blob_name)
                return

            # Use blocking call in a thread
            import asyncio

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, blob.download_to_filename, self.model_path)

            logger.info("model_downloaded_from_gcs", bucket=self.bucket_name, blob=self.blob_name)
        except Exception as e:
            logger.error("gcs_download_failed", error=str(e))

    def to_vw_format(
        self,
        incident: IncidentRecord,
        chosen_action: str | None = None,
        reward: float | None = None,
        probability: float | None = None,
    ) -> str:
        """
        Convert incident and actions into Vowpal Wabbit multiline ADF format.

        Format:
        shared |context feat1:val ...
        |action_1 feat_a:val ...
        |action_2 feat_b:val ...
        """
        # 1. Shared features (Context)
        m = incident.anomaly_event.metrics_snapshot if incident.anomaly_event else None
        diag = incident.diagnosis_result

        shared_parts = ["shared |s"]
        if m:
            shared_parts.append(f"lat:{min(10.0, (m.p99_latency_ms or 0) / 1000.0)}")
            shared_parts.append(f"err:{m.error_rate or 0}")
            shared_parts.append(f"cpu:{(m.cpu_percent or 0) / 100.0}")
        if diag:
            shared_parts.append(f"conf:{diag.confidence}")
            shared_parts.append(f"novel:{float(diag.is_novel_incident)}")

        lines = [" ".join(shared_parts)]

        # 2. Actions (ADF)
        # In a real system, these would be filtered by Tier or Service
        from agents.action.workflows import N8N_WORKFLOWS

        all_actions = list(N8N_WORKFLOWS.keys()) + ["no_action"]

        for action in all_actions:
            label = ""
            # If this is the chosen action and we have a reward, add the label
            if chosen_action == action and reward is not None:
                # VW minimizes COST. We maximize REWARD.
                # cost = -reward (with constant shift to ensure positive cost if needed,
                # but VW handles negative costs too)
                cost = -reward
                prob = probability or (1.0 / len(all_actions))
                label = f"0:{cost}:{prob:.4f} "  # 0 is relative index for the action line

            lines.append(f"{label}|a name_{action}")

        return "\n".join(lines)

    async def record_outcome(self, incident: IncidentRecord, semantic_reward: SemanticReward | None = None) -> float:
        """
        Record the outcome of a resolved incident and compute hybrid reward.

        Args:
            incident: A resolved incident record
            semantic_reward: Optional qualitative evaluation from the RewardAgent

        Returns:
            Computed hybrid reward value
        """
        await self._init_redis()
        with tracer.start_as_current_span("feedback_agent.record_outcome") as span:
            # 1. Compute hybrid reward (Extrinsic + Intrinsic)
            reward = compute_reward(incident, semantic_reward)

            # 2. Extract action and probability
            # Note: In production, we'd store the probability returned by suggest_action
            from agents.feedback.reward import _extract_action_label

            action = _extract_action_label(incident)

            # 3. Train Vowpal Wabbit online (with A/B splitting)
            import hashlib

            vw_example = self.to_vw_format(
                incident,
                chosen_action=action,
                reward=reward,
                probability=0.25,  # simplified for now
            )

            is_experimental = hashlib.sha256(incident.incident_id.encode()).digest()[0] % 2 == 0
            if is_experimental and hasattr(self, "vw_experimental"):
                self.vw_experimental.learn(vw_example)
                ab_group = "experimental"
            else:
                self.vw.learn(vw_example)
                ab_group = "control"

            # 4. Add to experience buffer with semantic context
            experience = {
                "incident_id": incident.incident_id,
                "vw_example": vw_example,
                "action": action,
                "reward": reward,
                "timestamp": incident.resolved_at.isoformat() if incident.resolved_at else None,
            }

            if semantic_reward:
                experience["semantic_context"] = {
                    "logical_consistency": semantic_reward.logical_consistency,
                    "action_relevance": semantic_reward.action_relevance,
                    "expert_accuracy": semantic_reward.expert_accuracy,
                    "justification": semantic_reward.justification,
                }

            self.experience_buffer.append(experience)

            # 5. Langfuse scoring removed (Using internal logging only)
            logger.debug("reward_recorded_locally", incident_id=incident.incident_id, reward=reward)
            self.action_rewards[action].append(reward)

            span.set_attribute("reward", reward)
            span.set_attribute("action", action)
            span.set_attribute("has_semantic_feedback", semantic_reward is not None)
            span.set_attribute("ab_group", ab_group)

            logger.info(
                "outcome_recorded",
                incident_id=incident.incident_id,
                action=action,
                reward=reward,
                buffer_size=len(self.experience_buffer),
            )

            # 6. Periodic Retraining (Phase 7 VW already trains online)
            retrain_threshold = 10 if "test" in self.settings.app.app_env else 50
            if len(self.experience_buffer) % retrain_threshold == 0:
                await self.save_policy()

            return reward

    async def suggest_action(self, incident: IncidentRecord) -> dict[str, Any]:
        """
        Suggest an action based on current state features using VW predict.

        Returns:
            Dict with suggested action, confidence, and exploration flag
        """
        await self._init_redis()

        # 1. Format example for prediction (shared context + action lines, no labels)
        vw_example = self.to_vw_format(incident)

        # 2. Get action probabilities from VW
        probs = self.vw.predict(vw_example)

        # 3. Sample an action from the distribution
        from agents.action.workflows import N8N_WORKFLOWS

        all_actions = list(N8N_WORKFLOWS.keys()) + ["no_action"]

        action_idx = np.random.choice(len(all_actions), p=probs)
        action = all_actions[action_idx]
        confidence = float(probs[action_idx])

        return {
            "action": action,
            "confidence": confidence,
            "is_exploration": confidence < (1.0 / len(all_actions)) * 1.5,  # heuristic
            "policy_version": self.policy_version,
            "probabilities": {a: p for a, p in zip(all_actions, probs, strict=False)},
        }

    async def retrain_policy(self) -> dict[str, Any]:
        """
        In VW, training happens online via learn().
        This method is now a wrapper for batch-replay if needed.
        """
        return {"status": "online_learning_active", "version": self.policy_version}

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
            "buffer_size": len(self.experience_buffer),
            "engine": "vowpal_wabbit",
            "action_stats": self.get_action_stats(),
        }

    def start_sync_loop(self) -> None:
        """Start a background task to periodically pull the model from GCS."""
        if self.sync_task and not self.sync_task.done():
            return

        self.sync_task = asyncio.create_task(self._periodic_sync())
        logger.info("background_sync_loop_started", interval=self.settings.app.vw_sync_interval_seconds)

    async def _periodic_sync(self) -> None:
        """Internal background loop for model synchronization."""
        while True:
            try:
                await asyncio.sleep(self.settings.app.vw_sync_interval_seconds)
                logger.debug("triggering_periodic_vw_sync")
                await self.sync_model_from_gcs()

                # Reload the model into VW memory if it changed
                if os.path.exists(self.model_path):
                    self.vw = pyvw.vw(f"-i {self.model_path} --quiet")
                    logger.debug("vw_model_reloaded_from_periodic_sync")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("periodic_sync_error", error=str(e))
                await asyncio.sleep(60)
