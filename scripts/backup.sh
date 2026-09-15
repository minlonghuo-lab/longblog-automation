#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
STAMP=$(date +%Y%m%d-%H%M%S)
DEST=${1:-./backups}
mkdir -p "$DEST"

tar -czf "$DEST/longblog-automation-$STAMP.tar.gz" \
  --exclude='data/workspace/current/node_modules' \
  --exclude='data/workspace/current/dist' \
  .env compose.yaml Dockerfile service scripts data/runtime data/ssh

echo "$DEST/longblog-automation-$STAMP.tar.gz"
