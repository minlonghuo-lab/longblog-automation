#!/usr/bin/env python3
import fcntl
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = os.environ.get("TRILIUM_BASE_URL", "").rstrip("/")
TOKEN = os.environ.get("TRILIUM_ETAPI_TOKEN", "")
ROOT_NOTE_ID = os.environ.get("TRILIUM_BLOG_ROOT_NOTE_ID", "")
ROOT_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = Path(os.environ.get("LONGBLOG_RUNTIME_DIR", ROOT_DIR / "runtime"))
REPO_DIR = os.environ.get("LONGBLOG_REPO_DIR", str(ROOT_DIR / "workspace/current"))
STATE_DIR = RUNTIME_DIR / "state"
LOG_DIR = RUNTIME_DIR / "logs"
SNAPSHOT_FILE = STATE_DIR / "trilium_poll_snapshot.json"
CTX_FILE = STATE_DIR / "last_event.json"
LOCK_FILE = STATE_DIR / "poller.lock"
SUCCESS_FILE = STATE_DIR / "poller_last_success"
LOG_FILE = LOG_DIR / "poller.log"
RUNNER = ROOT_DIR / "service/runner.py"
TEMPLATE_NOTE_ID = os.environ.get("TRILIUM_TEMPLATE_NOTE_ID", "MC7PtiChdF5S")
SKIP_KEYWORDS = tuple(x.strip().lower() for x in os.environ.get(
    "LONGBLOG_SKIP_KEYWORDS", "template,模板,logo,素材,draft-template,index-template"
).split(",") if x.strip())
TZ = timezone(timedelta(hours=8))

SESSION = requests.Session()
SESSION.headers.update({"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
SESSION.mount("http://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504))))
SESSION.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504))))


def now_iso():
    return datetime.now(TZ).isoformat()


def log(message):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{now_iso()} {message}\n")


def get(path, *, text=False, timeout=30):
    response = SESSION.get(f"{BASE_URL}{path}", timeout=timeout)
    response.raise_for_status()
    return response.text if text else response.json()


def labels(note):
    result = {}
    for attr in note.get("attributes") or []:
        if attr.get("type") == "label":
            result.setdefault(attr.get("name") or "", []).append(str(attr.get("value") or ""))
    return result


def last_label(items, name, default=""):
    for value in reversed(items.get(name) or []):
        if value.strip():
            return value.strip()
    return default


def bool_label(items, name, default=False):
    values = [v.strip().lower() for v in items.get(name, [])]
    if any(v in {"true", "1", "yes", "on"} for v in values):
        return True
    if any(v in {"false", "0", "no", "off"} for v in values):
        return False
    return default


def skip(note):
    title = (note.get("title") or "").lower()
    note_labels = labels(note)
    return (
        note.get("noteId") == ROOT_NOTE_ID
        or note.get("noteId") == TEMPLATE_NOTE_ID
        or any(word in title for word in SKIP_KEYWORDS)
        or ("template" in note_labels and not last_label(note_labels, "template"))
    )


def walk():
    result, stack, seen = [], [ROOT_NOTE_ID], set()
    while stack:
        note_id = stack.pop()
        if note_id in seen:
            continue
        seen.add(note_id)
        note = get(f"/etapi/notes/{note_id}")
        result.append(note)
        stack.extend(reversed(note.get("childNoteIds") or []))
    return result


def snapshot():
    result = {}
    for note in walk():
        if skip(note):
            continue
        item_labels = labels(note)
        item = {
            "id": note.get("noteId"),
            "title": note.get("title") or "未命名",
            "publish": bool_label(item_labels, "publish"),
            "sync": bool_label(item_labels, "sync"),
            "aiRefresh": bool_label(item_labels, "aiRefresh"),
            "pinned": bool_label(item_labels, "pinned"),
            "publishedAt": last_label(item_labels, "publishedAt"),
            "syncHash": last_label(item_labels, "syncHash"),
        }
        if item["publish"] or item["sync"] or item["aiRefresh"] or item["syncHash"]:
            body = get(f"/etapi/notes/{note['noteId']}/content", text=True, timeout=60)
            item["contentHash"] = hashlib.sha256(body.encode()).hexdigest()
        result[note["noteId"]] = item
    return result


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def differences(old, new):
    changes = []
    watched = ("title", "publish", "sync", "aiRefresh", "pinned", "contentHash")
    # 只有显式标签变化才触发同步：纯内容/标题编辑不再自动推送，
    # 用户改完内容后需要自己把 sync 置为 true 才推（2026-10-07 用户要求）。
    triggers = ("publish", "sync", "aiRefresh", "pinned")
    for note_id, item in new.items():
        previous = old.get(note_id)
        if previous is None:
            if item["publish"] or item["sync"] or item["aiRefresh"]:
                changes.append({"type": "new", "note": item, "changed": ["publish"]})
            continue
        changed = [key for key in watched if previous.get(key) != item.get(key)]
        triggered = [key for key in changed if key in triggers]
        if triggered:
            changes.append({"type": "changed", "note": item, "changed": triggered})
    for note_id, previous in old.items():
        if note_id not in new and (previous.get("publish") or previous.get("syncHash")):
            changes.append({"type": "removed", "note": previous, "changed": ["removed"]})
    return changes


def context(changes):
    # Specialized fast paths are safe only for exactly one isolated change.
    change = changes[0]
    note, changed = change["note"], change.get("changed") or []
    event = "sync_requested"
    payload = {"sync": "true"}
    if len(changes) == 1 and changed == ["pinned"]:
        event, payload = "pinned_changed", {"pinned": str(note.get("pinned", False)).lower()}
    elif len(changes) == 1 and changed == ["publish"]:
        event, payload = "publish_changed", {"publish": str(note.get("publish", False)).lower()}
    elif len(changes) == 1 and changed == ["aiRefresh"] and note.get("aiRefresh"):
        event, payload = "ai_refresh_requested", {"aiRefresh": "true"}
    return {
        "requestId": f"poller-{note.get('id')}-{int(time.time())}", "event": event,
        "noteId": note.get("id"), "noteTitle": note.get("title"), **payload,
        "triggeredAt": now_iso(), "source": "poller", "changes": [c.get("changed") for c in changes],
    }


def main():
    if not BASE_URL or not TOKEN or not ROOT_NOTE_ID:
        log("ERROR missing TRILIUM_BASE_URL / TRILIUM_ETAPI_TOKEN / TRILIUM_BLOG_ROOT_NOTE_ID")
        return 2
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("skip: poller already running")
            return 0
        try:
            old, new = read_json(SNAPSHOT_FILE), snapshot()
            if not old:
                write_json(SNAPSHOT_FILE, new)
                log(f"initialized snapshot notes={len(new)}")
            else:
                changes = differences(old, new)
                if changes:
                    log(f"changes={len(changes)}; processing sequentially")
                    for change in changes:
                        ctx = context([change])
                        write_json(CTX_FILE, ctx)
                        log(f"event={ctx['event']} noteId={ctx['noteId']} changed={change.get('changed')}")
                        result = subprocess.run([os.environ.get("PYTHON_BIN", "python3"), str(RUNNER)], env={**os.environ, "LONGBLOG_REPO_DIR": REPO_DIR}, timeout=int(os.environ.get("LONGBLOG_RUN_TIMEOUT", "1200")))
                        if result.returncode:
                            raise RuntimeError(f"runner exited {result.returncode} for note {ctx['noteId']}")
                    write_json(SNAPSHOT_FILE, snapshot())
                    log(f"runner ok for {len(changes)} change(s); snapshot updated")
                else:
                    log(f"no changes notes={len(new)}")
            SUCCESS_FILE.touch()
            return 0
        except Exception as error:
            log(f"ERROR {type(error).__name__}: {error}")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
