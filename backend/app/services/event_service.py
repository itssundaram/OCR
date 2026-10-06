import json
import logging
from typing import Any
from redis import Redis

logger = logging.getLogger(__name__)

class EventService:
    def __init__(self, redis_conn: Redis):
        self.redis = redis_conn

    def publish_event(self, job_id: str, event_type: str, payload: dict[str, Any]):
        channel = f"job_events:{job_id}"
        event = {
            "event_type": event_type,
            "job_id": job_id,
            **payload
        }
        try:
            self.redis.publish(channel, json.dumps(event))
            logger.debug(f"Published event {event_type} to {channel}")
        except Exception as e:
            logger.error(f"Failed to publish event: {e}")
