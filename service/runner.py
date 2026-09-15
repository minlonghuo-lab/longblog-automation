#!/usr/bin/env python3
import fcntl
import hashlib
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify import notify_report  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = Path(os.environ.get("LONGBLOG_RUNTIME_DIR", ROOT / "runtime"))
WORKSPACE = Path(os.environ.get("LONGBLOG_REPO_DIR", ROOT / "workspace/current"))
EVENT_FILE = RUNTIME / "state/last_event.json"
REPORT_FILE = RUNTIME / "reports/last_report.json"
LOCK_FILE = RUNTIME / "state/runner.lock"
LOG_FILE = RUNTIME / "logs/runner.log"
BUILD_LOG = RUNTIME / "logs/build.log"
SYNC_SCRIPT = ROOT / "service/sync_trilium_posts.py"
REPO_URL = os.environ.get("LONGBLOG_REPO_URL", "")
REMOTE = os.environ.get("LONGBLOG_GIT_REMOTE", "origin")
BRANCH = os.environ.get("LONGBLOG_GIT_BRANCH", "main")
SSH_KEY = os.environ.get("LONGBLOG_GIT_SSH_KEY", "/data/ssh/id_ed25519")
GIT_SSH = os.environ.get(
    "LONGBLOG_GIT_SSH_COMMAND",
    f"ssh -i {SSH_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/data/ssh/known_hosts",
)


def logger():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("runner")
    log.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    return log


LOG = logger()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def run(command, *, cwd=None, env=None, timeout=600, capture=True, check=True):
    LOG.info("run: %s", " ".join(command))
    result = subprocess.run(command, cwd=cwd, env=env, timeout=timeout, text=True, capture_output=capture)
    if check and result.returncode:
        output = ((result.stdout or "") + (result.stderr or ""))[-3000:]
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{output}")
    return result


def git_env():
    return {**os.environ, "GIT_SSH_COMMAND": GIT_SSH}


def prepare_workspace():
    if not REPO_URL:
        raise RuntimeError("LONGBLOG_REPO_URL is required")
    WORKSPACE.parent.mkdir(parents=True, exist_ok=True)
    env = git_env()
    if not (WORKSPACE / ".git").is_dir():
        run(["git", "clone", "--branch", BRANCH, "--single-branch", REPO_URL, str(WORKSPACE)], env=env, timeout=300)
    run(["git", "remote", "set-url", REMOTE, REPO_URL], cwd=WORKSPACE)
    run(["git", "fetch", REMOTE, BRANCH], cwd=WORKSPACE, env=env, timeout=300)
    run(["git", "checkout", "-B", BRANCH, f"{REMOTE}/{BRANCH}"], cwd=WORKSPACE)
    run(["git", "reset", "--hard", f"{REMOTE}/{BRANCH}"], cwd=WORKSPACE)
    run(["git", "config", "user.name", os.environ.get("LONGBLOG_GIT_USER_NAME", "longBlog Bot")], cwd=WORKSPACE)
    run(["git", "config", "user.email", os.environ.get("LONGBLOG_GIT_USER_EMAIL", "longblog-bot@users.noreply.github.com")], cwd=WORKSPACE)


def event_args():
    try:
        event = json.loads(EVENT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        event = {}
    args = []
    for key in ("requestId", "event", "noteId", "pinned"):
        value = event.get(key)
        if value is not None and str(value):
            args.extend([f"--{key}", str(value)])
    return event, args


def package_hash():
    path = WORKSPACE / "package-lock.json"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def append_build_log(text):
    BUILD_LOG.parent.mkdir(parents=True, exist_ok=True)
    if BUILD_LOG.exists() and BUILD_LOG.stat().st_size > 5_000_000:
        old = BUILD_LOG.with_suffix(".log.1")
        old.unlink(missing_ok=True)
        BUILD_LOG.replace(old)
    with BUILD_LOG.open("a", encoding="utf-8") as f:
        f.write(f"\n[{datetime.now().isoformat()}]\n{text}\n")


def build():
    digest = package_hash()
    digest_file = RUNTIME / "state/package-lock.sha256"
    previous = digest_file.read_text().strip() if digest_file.exists() else ""
    if not (WORKSPACE / "node_modules").is_dir() or digest != previous:
        result = run(["npm", "ci"], cwd=WORKSPACE, timeout=900)
        append_build_log((result.stdout or "") + (result.stderr or ""))
        digest_file.parent.mkdir(parents=True, exist_ok=True)
        digest_file.write_text(digest)
    result = run(["npm", "run", "build"], cwd=WORKSPACE, timeout=900)
    append_build_log((result.stdout or "") + (result.stderr or ""))


def commit_and_push(report):
    paths = ["src/data/trilium-posts.content.generated.ts", "src/data/trilium-posts.meta.generated.ts", "public/trilium-assets", "src/data/posts.ts"]
    run(["git", "add", "--", *paths], cwd=WORKSPACE)
    staged = run(["git", "diff", "--cached", "--quiet"], cwd=WORKSPACE, check=False)
    if staged.returncode == 0:
        return False, False, ""
    updated = report.get("updated") or []
    titles = ", ".join(str(x.get("title", "")) for x in updated[:3] if isinstance(x, dict))
    message = f"chore(trilium): sync {len(updated)} post(s)" + (f" - {titles}" if titles else "")
    run(["git", "commit", "-m", message], cwd=WORKSPACE)
    run(["git", "push", REMOTE, BRANCH], cwd=WORKSPACE, env=git_env(), timeout=300)
    return True, True, message


def main():
    RUNTIME.joinpath("state").mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOG.info("skip: runner already active")
            return 0
        started = datetime.now().astimezone().isoformat()
        report = {"runStartedAt": started, "failed": []}
        try:
            prepare_workspace()
            event, args = event_args()
            env = {**os.environ, "LONGBLOG_REPO_DIR": str(WORKSPACE), "LONGBLOG_RUNTIME_DIR": str(RUNTIME), "LONGBLOG_AUTO_PUSH": "false"}
            result = run([sys.executable, str(SYNC_SCRIPT), *args], cwd=WORKSPACE, env=env, timeout=900)
            report = json.loads(result.stdout)
            changed = run(["git", "status", "--porcelain", "--", "src/data", "public/trilium-assets"], cwd=WORKSPACE).stdout.strip()
            if changed:
                build()
                committed, pushed, message = commit_and_push(report)
            else:
                committed, pushed, message = False, False, ""
            report.update({"gitChanged": bool(changed), "gitCommitted": committed, "gitPushed": pushed, "gitMessage": message, "buildRan": bool(changed), "runStartedAt": started, "runFinishedAt": datetime.now().astimezone().isoformat(), "source": event.get("source", "manual"), "event": event.get("event") or "", "noteId": event.get("noteId") or ""})
            atomic_json(REPORT_FILE, report)
            EVENT_FILE.unlink(missing_ok=True)
            try:
                notify_report(report)
            except Exception:
                LOG.exception("bark notification failed (non-fatal)")
            LOG.info("success event=%s noteId=%s changed=%s pushed=%s", event.get("event"), event.get("noteId"), bool(changed), pushed)
            return 0
        except Exception as error:
            report.setdefault("failed", []).append({"error": str(error)[-3000:]})
            report.update({"runStartedAt": started, "runFinishedAt": datetime.now().astimezone().isoformat(), "gitPushed": False, "buildRan": False})
            atomic_json(REPORT_FILE, report)
            try:
                notify_report(report)
            except Exception:
                LOG.exception("bark notification failed (non-fatal)")
            LOG.exception("runner failed")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
