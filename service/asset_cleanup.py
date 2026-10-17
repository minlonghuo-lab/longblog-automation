"""Reclaim obsolete generated attachments after a successful content snapshot."""

import re
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urlsplit

NOTE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
GENERATED_FILE = re.compile(r"^[0-9a-f]{16}\.[A-Za-z0-9]+$")
ASSET_URL = re.compile(r"/trilium-assets/[^\s\"'<>()]+")


def prune_unused_assets(posts, asset_dir):
    """Delete only unreferenced generated files in the snapshot's note folders.

    Validate every reference before deleting anything. Missing files or malformed
    records fail closed; an incomplete sync must never drive garbage collection.
    Unrelated directories, hand-maintained files and symlinks are left alone.
    """
    if not posts:
        return []
    root = Path(asset_dir)
    if root.is_symlink():
        raise ValueError("attachment root is a symlink")
    note_ids = set()
    referenced = set()
    for post in posts:
        note_id = post.get("id")
        html = post.get("contentHtml")
        if not isinstance(note_id, str) or not NOTE_ID.fullmatch(note_id) or not isinstance(html, str):
            raise ValueError("invalid article snapshot for attachment cleanup")
        note_ids.add(note_id)
        for raw in ASSET_URL.findall(unescape(html)):
            path = unquote(urlsplit(raw.rstrip(",;")).path)
            parts = path.split("/")
            if len(parts) != 4 or not NOTE_ID.fullmatch(parts[2]) or not GENERATED_FILE.fullmatch(parts[3]):
                raise ValueError("unrecognized local attachment URL")
            referenced.add((parts[2], parts[3]))

    for folder, filename in referenced:
        if not (root / folder / filename).is_file():
            raise ValueError(f"referenced attachment is missing: {folder}/{filename}")

    obsolete = []
    for note_id in sorted(note_ids):
        folder = root / note_id
        if not folder.is_dir() or folder.is_symlink():
            continue
        for file in sorted(folder.iterdir()):
            if file.is_symlink() or not file.is_file() or not GENERATED_FILE.fullmatch(file.name):
                continue
            if (note_id, file.name) not in referenced:
                obsolete.append(file)

    removed = []
    for file in obsolete:
        file.unlink()
        removed.append(f"/trilium-assets/{file.parent.name}/{file.name}")
    return removed
