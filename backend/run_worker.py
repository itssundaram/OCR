import sys
import platform
import redis
from rq import Queue, Worker

from app.core.config import settings


def _build_worker(queues, connection):
    """RQ's default Worker relies on os.fork(), which doesn't exist on
    Windows — hence the rq-win WindowsWorker used on the dev box. On Linux
    (the GPU server), the standard rq.Worker is the right, simpler choice;
    only import rq_win when actually on Windows so this file runs on both
    without requiring rq-win to be installed on Linux at all."""
    if platform.system() == "Windows":
        from rq_win import WindowsWorker
        return WindowsWorker(queues, connection=connection)
    return Worker(queues, connection=connection)


def main():
    conn = redis.from_url(settings.REDIS_URL)
    q = Queue(settings.REDIS_QUEUE_NAME, connection=conn)
    worker = _build_worker([q], connection=conn)
    print(f"Starting RQ worker ({platform.system()}) on queue '{settings.REDIS_QUEUE_NAME}'...")
    worker.work(with_scheduler=True)


if __name__ == '__main__':
    main()
