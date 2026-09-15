#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

[ -f .env ] || { echo "错误：请先执行 cp .env.example .env 并填写配置" >&2; exit 1; }
mkdir -p data/runtime/{logs,reports,state} data/workspace data/ssh
chmod 700 data/ssh

REGISTRY=data/runtime/state/published_at_registry.json
if [ ! -f "$REGISTRY" ] && [ -f migration/published_at_registry.json ]; then
  cp migration/published_at_registry.json "$REGISTRY"
  echo "已导入历史首次发布时间登记表"
fi

KEY=data/ssh/id_ed25519
if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -N '' -C 'longBlog automation deploy key' -f "$KEY"
  echo
  echo "请把下面这行公钥添加到 GitHub 博客仓库："
  echo "Settings → Deploy keys → Add deploy key，并勾选 Allow write access"
  cat "$KEY.pub"
  echo
fi

ssh-keyscan -t ed25519 github.com > data/ssh/known_hosts 2>/dev/null
chmod 600 "$KEY" data/ssh/known_hosts
chmod 644 "$KEY.pub"

echo "目录已初始化。添加 Deploy Key 并填写 .env 后执行："
echo "docker compose up -d --build"
