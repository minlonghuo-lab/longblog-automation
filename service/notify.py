#!/usr/bin/env python3
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LOG = logging.getLogger("notify")


def _config():
    base = (os.environ.get("LONGBLOG_BARK_BASE_URL") or "").rstrip("/")
    return {
        "base": base,
        "icon": os.environ.get("LONGBLOG_BARK_ICON_URL", ""),
        "group": os.environ.get("LONGBLOG_BARK_GROUP", "longBlog"),
        "enabled": (os.environ.get("LONGBLOG_BARK_ENABLED", "true").lower() == "true") and bool(base),
        "timeout": float(os.environ.get("LONGBLOG_BARK_TIMEOUT", "8")),
    }


def send(title: str, body: str, *, level: str = "active") -> bool:
    config = _config()
    if not config["enabled"]:
        return False
    query = {"group": config["group"], "level": level}
    if config["icon"]:
        query["icon"] = config["icon"]
    url = f"{config['base']}/{urllib.parse.quote(title, safe='')}/{urllib.parse.quote(body, safe='')}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=config["timeout"]) as response:
            response.read(256)
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        LOG.warning("bark notification failed: %s", error)
        return False


def summarize(report: dict) -> tuple[str, str]:
    failed = report.get("failed") or []
    updated = report.get("updated") or []
    removed = report.get("removed") or []
    unchanged = report.get("unchanged") or []
    removed_assets = report.get("removedAssets") or []
    ai_updated = report.get("aiUpdated") or []

    if failed:
        title = "longBlog 自动发布失败"
        details = []
        for item in failed[:3]:
            if isinstance(item, dict):
                details.append(str(item.get("title") or item.get("id") or item.get("error") or item)[:80])
            else:
                details.append(str(item)[:80])
        body = f"事件 {report.get('event') or '-'}｜错误 {len(failed)} 项｜" + "；".join(details)
        return title, body

    title = "longBlog 自动发布"
    body = (
        f"事件 {report.get('event') or '-'}｜"
        f"更新{len(updated)}篇｜撤下{len(removed)}篇｜清理资源{len(removed_assets)}个｜"
        f"未变{len(unchanged)}篇｜失败{len(failed)}篇｜AI {len(ai_updated)}篇｜"
        f"Git变更 {bool(report.get('gitChanged'))}｜已推送 {bool(report.get('gitPushed'))}｜构建 {bool(report.get('buildRan'))}"
    )
    return title, body


def notify_report(report: dict) -> bool:
    title, body = summarize(report)
    level = "timeSensitive" if (report.get("failed") or []) else "active"
    if not (report.get("gitChanged") or report.get("failed")):
        return False
    return send(title, body, level=level)


def notify_error(message: str) -> bool:
    return send("longBlog 自动化异常", str(message)[:200], level="timeSensitive")


def main():
    import sys

    path = Path(os.environ.get("LONGBLOG_REPORT_FILE", "/data/runtime/reports/last_report.json"))
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1 and sys.argv[1] == "error":
        raise SystemExit(0 if notify_error(" ".join(sys.argv[2:]) or "unknown error") else 1)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(0 if notify_error(f"report unreadable: {error}") else 1)
    notify_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
