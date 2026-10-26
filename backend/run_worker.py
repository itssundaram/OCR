"""
Run every DOCINT worker with one command:

    python run_worker.py

One process per pipeline (fast_ocr, learned_anchor, surya, tesseract, and any installed experimental pipeline) plus a
general one. Works the same on Windows and Linux. See app/workers/launcher.py.
"""
import sys

if __name__ == "__main__":
    from app.workers.launcher import main
    sys.exit(main())
