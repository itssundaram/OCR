import os
import requests
from redis import Redis
from sqlalchemy.orm import Session
from sqlalchemy import text

class HealthService:
    def __init__(self, db_session: Session, redis_conn: Redis):
        self.db = db_session
        self.redis = redis_conn
        self.ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    def check_oracle(self) -> dict:
        try:
            dialect = self.db.get_bind().dialect.name
            probe = "SELECT 1 FROM DUAL" if dialect == "oracle" else "SELECT 1"
            result = self.db.execute(text(probe)).scalar()
            return {"status": "ok", "latency_ms": 0} if result == 1 else {"status": "error", "error": "Query failed"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def check_redis(self) -> dict:
        try:
            return {"status": "ok"} if self.redis.ping() else {"status": "error", "error": "Ping failed"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def check_workers(self) -> dict:
        """Phase 10: distinguish "Redis itself is reachable" from "an RQ
        worker is actually alive and consuming our queue" — the gap the
        implementation plan calls out (today /health only proved the
        former). RQ already registers each worker and refreshes its
        heartbeat in Redis internally (Worker.all); this just surfaces
        that existing registry rather than inventing a new one.
        """
        try:
            from rq import Worker
            from rq.queue import Queue
            from app.core.config import settings

            queue = Queue(settings.REDIS_QUEUE_NAME, connection=self.redis)
            workers = Worker.all(connection=self.redis)
            active = [w for w in workers if queue in w.queues]
            return {
                "status": "ok" if active else "error",
                "worker_count": len(active),
                "worker_names": [w.name for w in active],
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def check_ollama(self) -> dict:
        try:
            resp = requests.get(self.ollama_url, timeout=2)
            if resp.status_code == 200:
                return {"status": "ok"}
            return {"status": "error", "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
