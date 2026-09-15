#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

echo "== container =="
docker compose ps

echo "== health =="
docker inspect longblog-automation --format '{{json .State.Health}}' 2>/dev/null || true

echo "== daemon log =="
tail -20 data/runtime/logs/daemon.log 2>/dev/null || true

echo "== poller log =="
tail -30 data/runtime/logs/poller.log 2>/dev/null || true

echo "== runner log =="
tail -30 data/runtime/logs/runner.log 2>/dev/null || true

echo "== last report =="
python3 - <<'PY'
import json
from pathlib import Path
p=Path('data/runtime/reports/last_report.json')
if not p.exists():
    print('暂无运行报告（首次快照初始化后属正常）')
else:
    d=json.loads(p.read_text())
    for k in ('runStartedAt','runFinishedAt','event','noteId','updated','removed','failed','gitChanged','gitPushed','buildRan'):
        print(f'{k}: {d.get(k)}')
PY
