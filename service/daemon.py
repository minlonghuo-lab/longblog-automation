#!/usr/bin/env python3
import logging
import os
import signal
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = Path(os.environ.get("LONGBLOG_RUNTIME_DIR", ROOT / "runtime"))
POLL_SCRIPT = ROOT / "service/poll_trilium_changes.py"
INTERVAL = max(15, int(os.environ.get("LONGBLOG_POLL_INTERVAL", "60")))
STOP = False


def configure_logging():
    path = RUNTIME / "logs/daemon.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("daemon")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger


LOG = configure_logging()


def stop(signum, _frame):
    global STOP
    LOG.info("received signal %s; stopping", signum)
    STOP = True


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOG.info("longBlog automation started; interval=%ss", INTERVAL)
    while not STOP:
        started = time.monotonic()
        try:
            result = subprocess.run([sys.executable, str(POLL_SCRIPT)], timeout=max(1200, INTERVAL))
            if result.returncode:
                LOG.error("poller exited %s", result.returncode)
        except subprocess.TimeoutExpired:
            LOG.exception("poller timeout")
        except Exception:
            LOG.exception("poller crashed")
        remaining = max(1, INTERVAL - int(time.monotonic() - started))
        for _ in range(remaining):
            if STOP:
                break
            time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
