import sys
import redis
from rq import Queue
from rq_win import WindowsWorker

from app.core.config import settings

def main():
    conn = redis.from_url(settings.REDIS_URL)
    q = Queue(settings.REDIS_QUEUE_NAME, connection=conn)
    worker = WindowsWorker([q], connection=conn)
    print(f"Starting Windows-compatible RQ worker on queue '{settings.REDIS_QUEUE_NAME}'...")
    worker.work(with_scheduler=True)

if __name__ == '__main__':
    main()
