"""
Continuous Improvement Loop - Weekly Ops Review Script
Pulls traces from Langfuse where human_overrode = true
and exports them for offline training of the RL model.
"""

import pandas as pd
from langfuse import Langfuse

from shared.config import get_settings


def fetch_overrides_for_retraining() -> None:
    settings = get_settings()

    # Initialize Langfuse Client
    _ = Langfuse(
        public_key=settings.observability.langfuse_public_key,
        secret_key=settings.observability.langfuse_secret_key,
        host=settings.observability.langfuse_host,
    )

    print("Fetching sessions from Langfuse where human override was triggered...")

    # Normally we would filter by a tag or score indicating override
    # Since Langfuse SDK exposes get_sessions/get_traces, we'll mock the fetch for documentation
    # In a live setup, we filter traces by the 'reward' score being negative or human_override tag

    # MOCK Data Structure for export
    historical_data = [
        {
            "incident_id": "uuid-001",
            "state_anomaly": "latency_spike",
            "agent_action": "scale_replicas",
            "human_corrected_action": "rollback_deployment",
            "reward_score": -0.5,
        },
        {
            "incident_id": "uuid-002",
            "state_anomaly": "error_rate",
            "agent_action": "clear_cache",
            "human_corrected_action": "restart_unhealthy_pod",
            "reward_score": -0.8,
        },
    ]

    df = pd.DataFrame(historical_data)
    export_path = "offline_training_data.csv"
    df.to_csv(export_path, index=False)

    print(f"Exported {len(df)} false-positive traces to {export_path} for offline RL training.")


if __name__ == "__main__":
    fetch_overrides_for_retraining()
