"""
Fine-tuning Dataset Generator.

Automates the creation of OpenAI JSONL files for fine-tuning smaller models
(like gpt-4o-mini) using real-world incidents where the LLM hallucinated or
required human overriding (negative RL reward).
"""

import json
import logging
from pathlib import Path

from agents.feedback.reward import compute_reward
from shared.schemas import IncidentRecord

logger = logging.getLogger("finetuner")


def generate_finetuning_dataset(incidents: list[IncidentRecord], output_path: str | Path) -> dict:
    """
    Extracts negative reward incidents and turns them into a fine-tuning dataset.
    We isolate hallucinated diagnoses or incorrect actions and use the human_feedback
    as the correct completion label to teach a smaller model.
    """
    dataset = []

    for incident in incidents:
        reward = compute_reward(incident)

        # We target incidents that required human correction (e.g. reward < 0)
        if reward < 0 and incident.human_feedback:
            system_prompt = (
                "You are an expert SRE AI. Given the following anomaly telemetry, "
                "provide the correct root cause and action plan."
            )

            user_msg = {
                "anomaly_type": incident.anomaly_event.anomaly_type.value if incident.anomaly_event else "unknown",
                "severity": incident.anomaly_event.severity.value if incident.anomaly_event else "unknown",
                "metrics": (
                    incident.anomaly_event.metrics_snapshot.model_dump(exclude_none=True)
                    if incident.anomaly_event
                    else {}
                ),
            }

            # The exact ground-truth label comes strictly from the overriding SRE
            assistant_msg = incident.human_feedback

            example = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_msg)},
                    {"role": "assistant", "content": assistant_msg},
                ]
            }
            dataset.append(example)

    if not dataset:
        logger.info("No viable fine-tuning data found in the provided incidents.")
        return {"status": "skipped", "count": 0}

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    with open(out_file, "w", encoding="utf-8") as f:
        for ex in dataset:
            f.write(json.dumps(ex) + "\n")

    logger.info(f"Finetuning dataset generated with {len(dataset)} examples at {out_file}")

    return {"status": "success", "count": len(dataset), "path": str(out_file)}
