"""
Trigger script for OpenAI Fine-Tuning Auto-Labeling.

SREs run this script to pull historical poor-performing incidents
and build datasets to distill the large GPT-4o model down to a smaller, faster model.
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to python path to allow imports when run as script
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agents.feedback.finetuner import generate_finetuning_dataset
from shared.schemas import AnomalyEvent, AnomalyType, IncidentRecord, IncidentStatus, MetricsSnapshot, Severity


def get_historical_incidents(count: int = 100) -> list[IncidentRecord]:
    """
    Mock integration for fetching historical incidents from Postgres.
    In a live scenario, this connects to the canonical Agent KB.
    """
    incidents = []
    for i in range(count):
        inc = IncidentRecord(status=IncidentStatus.RESOLVED)

        # Simulate a 10% rate of human overrides/false positives
        if i % 10 == 0:
            inc.human_overrode = True
            # The SRE's exact typed response becomes the golden fine-tuning label
            inc.human_feedback = (
                "The issue was actually a downstream API ratelimit, not a database timeout. "
                "Requires scaling the application gateway, NOT the database."
            )
            inc.anomaly_event = AnomalyEvent(
                severity=Severity.HIGH,
                affected_services=["payment-gateway"],
                anomaly_type=AnomalyType.LATENCY_SPIKE,
                metrics_snapshot=MetricsSnapshot(p99_latency_ms=1200.5, error_rate=0.08),
                reasoning="Saw latency spike, assuming DB contention.",
                confidence=0.8
            )

        incidents.append(inc)
    return incidents

async def main() -> None:
    parser = argparse.ArgumentParser(description="Extract negative-reward incidents into OpenAI JSONL")
    parser.add_argument("--limit", type=int, default=500, help="Number of recent incidents to scan")
    args = parser.parse_args()

    print(f"🔍 Fetching last {args.limit} incidents from database...")
    incidents = get_historical_incidents(args.limit)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = Path(f"data/finetuning/dataset_{timestamp}.jsonl")

    print("⚙️ Running Fine-Tuning Auto-Labeler...")
    result = generate_finetuning_dataset(incidents, out_path)

    if result["status"] == "success":
        print(f"\n✅ Successfully wrote {result['count']} highly-curated examples to {result['path']}")
        print("\n🚀 Next Steps: Upload to OpenAI for distillation:")
        print(f"    openai api files.create -f {result['path']} -p fine-tune")
        print("    openai api fine_tunes.create -t <FILE_ID> -m gpt-4o-mini")
    else:
        print("\n⏩ Skipped: Not enough negative-reward incidents found to form a dataset.")

if __name__ == "__main__":
    asyncio.run(main())
