from typing import Generator
from fastapi import Request
from sqlalchemy.orm import Session
from app.db.database import get_db
import redis

# get_db is imported so it can be re-exported

def get_redis_connection() -> redis.Redis:
    from app.workers.queue import redis_conn
    if redis_conn is None:
        raise RuntimeError("Redis connection is not available")
    return redis_conn
