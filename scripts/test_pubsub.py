import asyncio
import os

from google.cloud import pubsub_v1

from shared.config import get_settings
from shared.utils import get_logger, setup_logging

logger = get_logger("test_pubsub")


async def test_pubsub_connectivity() -> None:
    setup_logging()
    settings = get_settings()
    project_id = os.getenv("GOOGLE_PROJECT_ID", "amex-autonomouse-expense-ai")
    topic_id = f"anomaly-events-{settings.app.app_env}"

    print("\n--- Pub/Sub Connectivity Check ---")
    print(f"Project: {project_id}")
    print(f"Topic: {topic_id}")

    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project_id, topic_id)

    try:
        # Publish a test message
        print(f"📡 Publishing test message to {topic_id}...")
        future = publisher.publish(topic_path, b"Test connectivity message from agent", origin="test-script")
        message_id = future.result()
        print(f"✅ Success! Message ID: {message_id}")

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        if "NotFound" in str(e):
            print("   HINT: The topic may not have been created by Terraform yet.")


if __name__ == "__main__":
    asyncio.run(test_pubsub_connectivity())
