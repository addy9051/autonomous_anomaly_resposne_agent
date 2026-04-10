"""
Google Cloud Pub/Sub Client.

Provides a simplified asynchronous wrapper for publishing telemetry events
to GCP Pub/Sub topics.
"""

import json
from typing import Any
from google.cloud import pubsub_v1
from shared.config import get_settings
from shared.utils import get_logger

logger = get_logger("pubsub_client")

class PubSubClient:
    """Async-friendly wrapper for Google Cloud Pub/Sub."""
    
    def __init__(self):
        settings = get_settings()
        self.project_id = settings.data.pubsub_project_id or settings.llm.google_project_id
        if not self.project_id:
            logger.warning("pubsub_client_missing_project_id")
            
        self.publisher = pubsub_v1.PublisherClient()
        self.topic_prefix = settings.data.pubsub_topic_prefix
        
    def publish_event(self, topic_name: str, data: dict[str, Any]) -> str:
        """
        Publish a message to a Pub/Sub topic.
        
        Args:
            topic_name: Short name of the topic (e.g. 'telemetry')
            data: Dictionary to be JSON-encoded and published
            
        Returns:
            The message ID
        """
        if not self.project_id:
            logger.error("pubsub_publish_failed_no_project")
            return ""
            
        # Standardize full topic path: projects/{project}/topics/{prefix}-{topic}
        full_topic_path = f"projects/{self.project_id}/topics/{self.topic_prefix}-{topic_name}"
        
        try:
            message_bytes = json.dumps(data, default=str).encode("utf-8")
            future = self.publisher.publish(full_topic_path, message_bytes)
            message_id = future.result()
            logger.debug("pubsub_message_published", topic=topic_name, message_id=message_id)
            return message_id
        except Exception as e:
            logger.error("pubsub_publish_error", topic=topic_name, error=str(e))
            return ""

_client = None

def get_pubsub_client() -> PubSubClient:
    """Singleton getter for the PubSub client."""
    global _client
    if _client is None:
        _client = PubSubClient()
    return _client
