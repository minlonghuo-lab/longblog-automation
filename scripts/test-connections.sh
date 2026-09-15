#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

[ -f .env ] || { echo "缺少 .env" >&2; exit 1; }

echo "== 构建并检查配置 =="
docker compose config >/dev/null
docker compose build

echo "== Trilium ETAPI 连接 =="
docker compose run --rm --entrypoint python3 longblog-automation - <<'PY'
import os, requests
base=os.environ['TRILIUM_BASE_URL'].rstrip('/')
token=os.environ['TRILIUM_ETAPI_TOKEN']
root=os.environ['TRILIUM_BLOG_ROOT_NOTE_ID']
r=requests.get(f'{base}/etapi/notes/{root}', headers={'Authorization':f'Bearer {token}'}, timeout=20)
r.raise_for_status()
d=r.json()
print('OK:', d.get('title'), d.get('noteId'))
PY

echo "== GitHub SSH 连接 =="
docker compose run --rm --entrypoint sh longblog-automation -c \
  'GIT_SSH_COMMAND="ssh -i /data/ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/data/ssh/known_hosts" git ls-remote "$LONGBLOG_REPO_URL" "$LONGBLOG_GIT_BRANCH" >/dev/null && echo OK'

echo "全部连接测试通过，可以 docker compose up -d"
