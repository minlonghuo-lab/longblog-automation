#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify import notify_error  # noqa: E402

runtime = Path(os.environ.get("LONGBLOG_RUNTIME_DIR", "/data/runtime"))
success = runtime / "state/poller_last_success"
report = runtime / "reports/last_report.json"
alerted = runtime / "state/health_alerted"
interval = max(15, int(os.environ.get("LONGBLOG_POLL_INTERVAL", "60")))
threshold = max(300, interval * 5)


def fail(message):
    # Alert once per outage window so a broken deployment does not spam.
    last = alerted.stat().st_mtime if alerted.exists() else 0
    if time.time() - last > threshold:
        try:
            notify_error(message)
        except Exception:
            pass
        alerted.parent.mkdir(parents=True, exist_ok=True)
        alerted.touch()
    raise SystemExit(message)


if not success.exists() or time.time() - success.stat().st_mtime > threshold:
    fail("poller has not succeeded recently")

if report.exists():
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("last report is invalid JSON")
    if data.get("failed") and not data.get("gitPushed"):
        # A failed publication stays visible in the report; healthcheck only
        # alerts on repeated failures, not on a single unsuccessful run.
        pass

alerted.unlink(missing_ok=True)
