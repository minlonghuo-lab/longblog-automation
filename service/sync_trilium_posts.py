#!/usr/bin/env python3
import argparse
import os
import re
import json
import hashlib
import mimetypes
from datetime import datetime, timezone, timedelta
from html import escape as html_escape
from typing import Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from asset_cleanup import prune_unused_assets
from article_metadata import normalize_post_metadata, note_url_identifier, summarize_html
from legacy_attributes import cleanup_legacy_attributes

BASE_URL = os.environ.get("TRILIUM_BASE_URL", "").rstrip("/")
TOKEN = os.environ.get("TRILIUM_ETAPI_TOKEN", "")
ROOT_NOTE_ID = os.environ.get("TRILIUM_BLOG_ROOT_NOTE_ID", "")
BASE_DIR = os.environ.get("LONGBLOG_REPO_DIR") or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_CONTENT_TS = os.path.join(BASE_DIR, "src", "data", "trilium-posts.content.generated.ts")
OUT_META_TS = os.path.join(BASE_DIR, "src", "data", "trilium-posts.meta.generated.ts")
ASSET_DIR = os.path.join(BASE_DIR, "public", "trilium-assets")
TEMPLATE_NOTE_ID = os.environ.get("TRILIUM_TEMPLATE_NOTE_ID", "MC7PtiChdF5S")

SESSION = requests.Session()
SESSION.mount("http://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504))))
SESSION.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504))))

HEADERS_JSON = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
HEADERS_AUTH = {"Authorization": f"Bearer {TOKEN}"}
SKIP_KEYWORDS = ("template", "模板", "logo", "素材", "draft-template", "index-template")
TZ_UTC8 = timezone(timedelta(hours=8))
STATE_LABEL_NAMES = ["publish", "sync", "syncStatus"]
PUBLISHED_AT_REGISTRY = os.path.join(os.environ.get("LONGBLOG_RUNTIME_DIR", os.path.join(os.path.dirname(BASE_DIR), "runtime")), "state", "published_at_registry.json")


def load_published_at_registry() -> Dict[str, str]:
    try:
        with open(PUBLISHED_AT_REGISTRY, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_published_at_registry(data: Dict[str, str]):
    os.makedirs(os.path.dirname(PUBLISHED_AT_REGISTRY), exist_ok=True)
    tmp = PUBLISHED_AT_REGISTRY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, PUBLISHED_AT_REGISTRY)


def now_str() -> str:
    return datetime.now(TZ_UTC8).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "+0800"


def request(method: str, path: str, *, json_data=None, params=None, timeout=30, expected=None):
    r = SESSION.request(method, f"{BASE_URL}{path}", headers=HEADERS_JSON, json=json_data, params=params, timeout=timeout)
    if expected and r.status_code not in expected:
        raise requests.HTTPError(f"{method} {path} -> {r.status_code}: {r.text[:300]}", response=r)
    r.raise_for_status()
    return r


def get_json(path: str, params=None):
    return request("GET", path, params=params).json()


def get_text(path: str):
    return request("GET", path).text


def patch_json(path: str, payload: dict, expected=(200, 204)):
    return request("PATCH", path, json_data=payload, expected=expected)


def post_json(path: str, payload: dict, expected=(200, 201)):
    return request("POST", path, json_data=payload, expected=expected)


def delete_path(path: str, expected=(200, 204)):
    return request("DELETE", path, expected=expected)


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def normalize_whitespace(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_label_value(text: str) -> str:
    return normalize_whitespace(str(text or ""))


def label_attrs(attrs: List[dict]) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for a in attrs or []:
        if a.get("type") != "label":
            continue
        out.setdefault(a.get("name", ""), []).append(a)
    return out


def last_label_value(attr_map: Dict[str, List[dict]], name: str, default: str = "") -> str:
    items = attr_map.get(name) or []
    if not items:
        return default
    for item in reversed(items):
        value = str(item.get("value") or "").strip()
        if value != "":
            return value
    return default


def bool_label_value(attr_map: Dict[str, List[dict]], name: str, default: bool = False) -> bool:
    items = attr_map.get(name) or []
    if not items:
        return default
    values = [str(item.get("value") or "").strip().lower() for item in items]
    if any(v in {"true", "1", "yes", "on"} for v in values):
        return True
    if any(v in {"false", "0", "no", "off"} for v in values):
        return False
    return default


def set_label(note_id: str, name: str, value: str):
    value = normalize_label_value(value)
    if name in {"publishedAt", "updatedAt"}:
        value = value.replace("T", " ", 1)
    live_attrs = (get_json(f"/etapi/notes/{note_id}").get("attributes", []) or [])
    matches = [a for a in live_attrs if a.get("type") == "label" and a.get("name") == name]
    if matches:
        primary = matches[-1]
        patch_json(f"/etapi/attributes/{primary['attributeId']}", {"value": value})
        for extra in matches[:-1]:
            try:
                delete_path(f"/etapi/attributes/{extra['attributeId']}")
            except Exception:
                pass
        return
    post_json("/etapi/attributes", {"noteId": note_id, "type": "label", "name": name, "value": value})


def dedupe_state_labels(note_id: str):
    live_attrs = (get_json(f"/etapi/notes/{note_id}").get("attributes", []) or [])
    for name in STATE_LABEL_NAMES:
        matches = [a for a in live_attrs if a.get("type") == "label" and a.get("name") == name]
        for extra in matches[:-1]:
            try:
                delete_path(f"/etapi/attributes/{extra['attributeId']}")
            except Exception:
                pass


def is_template_note(note: dict) -> bool:
    note_id = note.get("noteId", "")
    if note_id == TEMPLATE_NOTE_ID:
        return True
    title = (note.get("title") or "").lower()
    if "template" in title or "模板" in title:
        return True
    attrs = note.get("attributes", []) or []
    for a in attrs:
        if a.get("type") == "label" and a.get("name") == "template" and not str(a.get("value") or "").strip():
            return True
    return False


def should_skip_note(note: dict) -> bool:
    title = (note.get("title") or "").lower()
    return any(k.lower() in title for k in SKIP_KEYWORDS)


def walk_note_tree(note_id: str) -> List[dict]:
    results: List[dict] = []
    stack = [note_id]
    seen = set()
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        note = get_json(f"/etapi/notes/{nid}")
        results.append(note)
        for child_id in reversed(note.get("childNoteIds", []) or []):
            stack.append(child_id)
    return results


def ext_from_meta(url: str, content_type: str = "") -> str:
    base = url.split("?")[0]
    _, ext = os.path.splitext(base)
    if ext:
        return ext.lower()
    if content_type:
        e = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if e:
            return e
    return ".bin"


def to_abs_attachment_url(u: str) -> str:
    if u.startswith("http://") or u.startswith("https://"):
        return u
    if u.startswith("/api/attachments/"):
        return f"{BASE_URL}{u}"
    if u.startswith("api/attachments/"):
        return f"{BASE_URL}/{u}"
    return u


def extract_attachment_id(abs_url: str) -> str:
    m = re.search(r"/api/attachments/([^/]+)/", abs_url)
    return m.group(1) if m else ""


def download_attachment_by_etapi(attachment_id: str) -> Tuple[Optional[bytes], Optional[str]]:
    if not attachment_id:
        return None, None
    try:
        meta = get_json(f"/etapi/attachments/{attachment_id}")
        r = requests.get(f"{BASE_URL}/etapi/attachments/{attachment_id}/content", headers=HEADERS_AUTH, timeout=60)
        if not r.ok:
            return None, None
        content_type = r.headers.get("content-type", meta.get("mime", ""))
        return r.content, content_type
    except Exception:
        return None, None


def strip_manager_injections(html: str) -> Tuple[str, int]:
    """Remove host-injected asset tags from note content.

    The Trilium deployment serving this blog injects its own stylesheet and
    script tag at the top of every note body. Those tags address the Trilium
    host (`/__fnos/assets/...`), not the blog, so they must never be published:
    on the blog they only produce 404 requests and stray DOM. Removal is
    idempotent, and content without the marker is returned untouched.
    """
    if not html or "data-trilium-fnos-manager" not in html:
        return html, 0
    pattern = re.compile(
        r"<link\b[^>]*data-trilium-fnos-manager[^>]*>\s*"
        r"|<script\b[^>]*data-trilium-fnos-manager[^>]*>\s*(?:</script>)?\s*",
        re.IGNORECASE,
    )
    return pattern.subn("", html)


INCLUDE_NOTE_PATTERN = re.compile(
    r'<(section|figure)\b([^>]*\bclass="[^"]*\binclude-note\b[^"]*"[^>]*)>(.*?)</\1>',
    re.IGNORECASE | re.DOTALL,
)
NOTE_ID_ATTR = re.compile(r'data-note-id="([^"]+)"', re.IGNORECASE)


def render_include_note_card(note_id: str, target: dict) -> str:
    """Build the card for an embedded note.

    The href follows the blog's routing contract: article URLs are
    `/blog/<noteId>/`, ASCII and with a trailing slash. The exported slug and
    Trilium URL identifier use that exact same case-preserving noteId.
    """
    summary = str(target.get("summary") or "").strip()
    summary_html = (
        f'<span class="include-note-card-summary">{html_escape(summary, quote=True)}</span>'
        if summary else ""
    )
    return (
        f'<a class="include-note-card" href="/blog/{html_escape(note_id, quote=True)}/"'
        f' data-note-id="{html_escape(note_id, quote=True)}">'
        '<span class="include-note-card-label">相关文章</span>'
        f'<span class="include-note-card-title">{html_escape(str(target.get("title") or ""), quote=True)}</span>'
        f"{summary_html}</a>"
    )


def render_include_note_cards(html: str, index: Dict[str, dict]) -> Tuple[str, int]:
    """Replace each embedded-note placeholder with a link card to the published post.

    An embed stores only its target's id and is expanded by Trilium in the browser,
    so published content has to resolve it here. A target outside `index` — a note
    that is not published, or an attachment embed — is left untouched rather than
    guessed at.
    """
    if not html or "include-note" not in html:
        return html, 0
    replaced = 0

    def substitute(match: "re.Match[str]") -> str:
        nonlocal replaced
        note_id_match = NOTE_ID_ATTR.search(match.group(2))
        if not note_id_match:
            return match.group(0)
        target = index.get(note_id_match.group(1))
        if not target:
            return match.group(0)
        replaced += 1
        return render_include_note_card(note_id_match.group(1), target)

    return INCLUDE_NOTE_PATTERN.sub(substitute, html), replaced


def apply_include_note_cards(posts: List[dict]) -> int:
    """Resolve embedded notes across the whole published set.

    Runs once every post is built so a card can link to the stable ID of a note
    published in the same run, whatever order the two were processed in.
    """
    index = {str(post["id"]): post for post in posts if post.get("id")}
    total = 0
    for post in posts:
        cleaned, replaced = render_include_note_cards(str(post.get("contentHtml") or ""), index)
        if replaced:
            post["contentHtml"] = cleaned
            total += replaced
    return total


def find_attachment_urls(html: str) -> List[str]:
    pattern = re.compile(r'(https?://[^"\'\)\s]*/api/attachments/[^"\'\)\s]+|/api/attachments/[^"\'\)\s]+|api/attachments/[^"\'\)\s]+)')
    return sorted(set(pattern.findall(html or "")))


def localize_attachments(html: str, note_id: str) -> Tuple[str, List[str], bool]:
    urls = find_attachment_urls(html)
    if not urls:
        return html, [], False
    note_dir = os.path.join(ASSET_DIR, note_id)
    ensure_dir(note_dir)
    changed = False
    local_assets: List[str] = []
    for raw in urls:
        abs_url = to_abs_attachment_url(raw)
        attachment_id = extract_attachment_id(abs_url)
        content, content_type = download_attachment_by_etapi(attachment_id)
        if content is None:
            continue
        h = hashlib.md5(abs_url.encode("utf-8")).hexdigest()[:16]
        ext = ext_from_meta(abs_url, content_type or "")
        fname = f"{h}{ext}"
        fpath = os.path.join(note_dir, fname)
        local_url = f"/trilium-assets/{note_id}/{fname}"
        local_assets.append(local_url)
        old = None
        if os.path.exists(fpath):
            with open(fpath, "rb") as f:
                old = f.read()
        if old != content:
            with open(fpath, "wb") as f:
                f.write(content)
            changed = True
        html = html.replace(raw, local_url).replace(abs_url, local_url)
    return html, local_assets, changed


def build_post_record(note: dict) -> Tuple[dict, dict]:
    note_id = note_url_identifier(note["noteId"])
    attr_map = label_attrs(note.get("attributes", []) or [])
    title = (note.get("title") or "未命名").strip()
    html = get_text(f"/etapi/notes/{note_id}/content")
    html, _ = strip_manager_injections(html)
    html_localized, local_assets, assets_changed = localize_attachments(html, note_id)
    slug = note_id
    summary = summarize_html(html_localized, title)
    payload_for_hash = {"title": title, "slug": slug, "contentHtml": html_localized}
    sync_hash = hashlib.sha256(json.dumps(payload_for_hash, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    post = {
        "id": note_id,
        "slug": slug,
        "title": title,
        "updatedAt": last_label_value(attr_map, "updatedAt", "").strip() or note.get("dateModified") or note.get("utcDateModified") or now_str(),
        "publishedAt": last_label_value(attr_map, "publishedAt", "").strip(),
        "tags": [],
        "summary": summary,
        "contentHtml": html_localized,
        "syncHash": sync_hash,
        "syncStatus": last_label_value(attr_map, "syncStatus", ""),
    }
    meta = {
        "assetsChanged": assets_changed,
        "localAssets": local_assets,
        "computedSyncHash": sync_hash,
        "prevSyncHash": last_label_value(attr_map, "syncHash", ""),
        "syncRequested": bool_label_value(attr_map, "sync", False),
    }
    return post, meta


def split_post_records(posts: List[dict]) -> Tuple[List[dict], List[dict]]:
    contents = []
    metas = []
    for post in posts:
        contents.append({
            "id": post["id"],
            "title": post["title"],
            "summary": post["summary"],
            "contentHtml": post["contentHtml"],
        })
        metas.append({
            "id": post["id"],
            "slug": post["slug"],
            "updatedAt": post["updatedAt"],
            "publishedAt": post.get("publishedAt", ""),
            "tags": post.get("tags", []),
            "syncHash": post.get("syncHash", ""),
            "syncStatus": post.get("syncStatus", ""),
        })
    return contents, metas


def sort_meta_records(metas: List[dict]) -> List[dict]:
    return sorted(metas, key=lambda x: (x.get("publishedAt", x.get("updatedAt", "")), x.get("id", "")), reverse=True)


def write_generated_files(posts: List[dict]):
    contents, metas = split_post_records(posts)
    metas = sort_meta_records(metas)

    content_text = "// Auto-generated from Trilium ETAPI\n"
    content_text += "export interface TriliumPostContentRecord {\n"
    content_text += "  id: string; title: string; summary: string; contentHtml: string;\n"
    content_text += "}\n\n"
    content_text += "export const triliumPostContents: TriliumPostContentRecord[] = " + json.dumps(contents, ensure_ascii=False, indent=2) + ";\n"
    with open(OUT_CONTENT_TS, "w", encoding="utf-8") as f:
        f.write(content_text)

    meta_text = "// Auto-generated from Trilium ETAPI\n"
    meta_text += "export interface TriliumPostMetaRecord {\n"
    meta_text += "  id: string; slug: string; updatedAt: string; publishedAt?: string; tags: string[]; pinned?: boolean; syncHash?: string; syncStatus?: string;\n"
    meta_text += "}\n\n"
    meta_text += "export const triliumPostMetas: TriliumPostMetaRecord[] = " + json.dumps(metas, ensure_ascii=False, indent=2) + ";\n"
    with open(OUT_META_TS, "w", encoding="utf-8") as f:
        f.write(meta_text)


def load_existing_content_records() -> List[dict]:
    if not os.path.exists(OUT_CONTENT_TS):
        return []
    with open(OUT_CONTENT_TS, "r", encoding="utf-8") as source:
        text = source.read()
    marker = "export const triliumPostContents: TriliumPostContentRecord[] = "
    start = text.find(marker)
    if start == -1:
        return []
    start += len(marker)
    end = text.rfind(";")
    if end == -1 or end <= start:
        return []
    raw = text[start:end].strip()
    try:
        data = json.loads(raw)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def load_existing_meta_records() -> List[dict]:
    if not os.path.exists(OUT_META_TS):
        return []
    with open(OUT_META_TS, "r", encoding="utf-8") as source:
        text = source.read()
    marker = "export const triliumPostMetas: TriliumPostMetaRecord[] = "
    start = text.find(marker)
    if start == -1:
        return []
    start += len(marker)
    end = text.rfind(";")
    if end == -1 or end <= start:
        return []
    raw = text[start:end].strip()
    try:
        data = json.loads(raw)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--requestId", default="")
    p.add_argument("--event", default="")
    p.add_argument("--noteId", default="")
    p.add_argument("--noteIds", nargs="*", default=[])
    return p.parse_args()


def main():
    if not BASE_URL or not TOKEN or not ROOT_NOTE_ID:
        raise SystemExit("TRILIUM_BASE_URL / TRILIUM_ETAPI_TOKEN / TRILIUM_BLOG_ROOT_NOTE_ID is required")
    args = parse_args()
    root = get_json(f"/etapi/notes/{ROOT_NOTE_ID}")
    report = {
        "requestId": args.requestId,
        "event": args.event,
        "noteId": args.noteId,
        "rootTitle": root.get("title", ""),
        "scanned": 0,
        "publishedCandidates": 0,
        "updated": [],
        "unchanged": [],
        "failed": [],
        "removed": [],
        "removedAssets": [],
        "prunedAssets": [],
        "urlIdentifiersUpdated": 0,
        "workspaceDir": BASE_DIR,
    }

    all_notes = walk_note_tree(ROOT_NOTE_ID)
    cleanup_notes = list(all_notes)
    if TEMPLATE_NOTE_ID and not any(note.get("noteId") == TEMPLATE_NOTE_ID for note in all_notes):
        try:
            cleanup_notes.append(get_json(f"/etapi/notes/{TEMPLATE_NOTE_ID}"))
        except requests.HTTPError as error:
            if error.response is None or error.response.status_code != 404:
                raise
    report["retiredAttributesRemoved"] = cleanup_legacy_attributes(
        cleanup_notes, lambda attribute_id: delete_path(f"/etapi/attributes/{attribute_id}"),
        os.path.join(os.path.dirname(PUBLISHED_AT_REGISTRY), "retired_attributes_backup.json"),
    )
    targeted_ids = set(args.noteIds) | ({args.noteId} if args.noteId else set())
    ordered_notes = sorted(all_notes, key=lambda note: note.get("noteId") not in targeted_ids)

    candidates = []
    for note in ordered_notes:
        report["scanned"] += 1
        if note.get("noteId") == ROOT_NOTE_ID:
            continue
        if note.get("type") != "text":
            continue
        if is_template_note(note):
            continue
        if should_skip_note(note):
            continue
        try:
            dedupe_state_labels(note["noteId"])
            note = get_json(f"/etapi/notes/{note['noteId']}")
        except Exception:
            pass
        attr_map = label_attrs(note.get("attributes", []) or [])
        try:
            identifier = note_url_identifier(note["noteId"])
            slug_attributes = attr_map.get("slug") or []
            if len(slug_attributes) != 1 or str(slug_attributes[0].get("value") or "") != identifier:
                set_label(identifier, "slug", identifier)
                report["urlIdentifiersUpdated"] += 1
        except Exception as error:
            report["failed"].append({"id": note.get("noteId"), "error": str(error)[:300]})
            continue
        publish_value = "true" if bool_label_value(attr_map, "publish", False) else "false"
        if publish_value != "true":
            if last_label_value(attr_map, "syncStatus", "") == "published":
                try:
                    ts = now_str()
                    set_label(note["noteId"], "syncStatus", "removed")
                    set_label(note["noteId"], "updatedAt", ts)
                    set_label(note["noteId"], "sync", "false")
                    report["removed"].append({"id": note["noteId"], "title": note.get("title", "未命名")})
                except Exception as e:
                    report["failed"].append({"id": note.get("noteId"), "title": note.get("title"), "error": str(e)[:300]})
            continue
        candidates.append(note)

    posts: List[dict] = []
    published_at_registry = load_published_at_registry()
    existing_meta = load_existing_meta_records()
    for item in existing_meta:
        note_id = str(item.get("id") or "")
        published_at = str(item.get("publishedAt") or "")
        if note_id and published_at and note_id not in published_at_registry:
            published_at_registry[note_id] = published_at
    save_published_at_registry(published_at_registry)
    existing_content = load_existing_content_records()
    existing_meta_map = {str(item.get("id") or ""): item for item in existing_meta if str(item.get("id") or "")}
    existing_content_map = {str(item.get("id") or ""): item for item in existing_content if str(item.get("id") or "")}
    for note in candidates:
        report["publishedCandidates"] += 1
        try:
            note_id = note.get("noteId")
            attr_map = label_attrs(note.get("attributes", []) or [])
            targeted_note = note_id in targeted_ids
            sync_requested = bool_label_value(attr_map, "sync", False)
            existing_meta_item = existing_meta_map.get(note_id)
            existing_content_item = existing_content_map.get(note_id)
            can_reuse_existing = bool(existing_meta_item and existing_content_item)
            publish_requested = targeted_note and args.event == "publish_changed"
            targeted_sync_requested = targeted_note and args.event == "sync_requested"
            needs_full_sync = bool(sync_requested or publish_requested or targeted_sync_requested or not can_reuse_existing)

            if needs_full_sync:
                post, meta = build_post_record(note)
                if not post["publishedAt"]:
                    post["publishedAt"] = published_at_registry.get(note_id, "") or (existing_meta_item or {}).get("publishedAt", "")
                first_publish = not post["publishedAt"]
                targeted_event_for_note = targeted_note and args.event in {"publish_changed", "sync_requested"}
                needs_publish = first_publish or meta["syncRequested"] or targeted_event_for_note
                if needs_publish:
                    set_label(note_id, "syncStatus", "publishing")
                    ts = now_str()
                    if not post["publishedAt"]:
                        post["publishedAt"] = ts
                        set_label(note_id, "publishedAt", ts)
                    else:
                        published_at_registry[note_id] = post["publishedAt"]
                        if last_label_value(attr_map, "publishedAt", "") != post["publishedAt"]:
                            set_label(note_id, "publishedAt", post["publishedAt"])
                    published_at_registry[note_id] = post["publishedAt"]
                    save_published_at_registry(published_at_registry)
                    set_label(note_id, "syncHash", meta["computedSyncHash"])
                    set_label(note_id, "updatedAt", ts)
                    set_label(note_id, "syncStatus", "published")
                    set_label(note_id, "sync", "false")
                    post["syncStatus"] = "published"
                    post["updatedAt"] = ts
                    report["updated"].append({"id": note_id, "title": post["title"]})
                else:
                    set_label(note_id, "syncStatus", "published")
                    post["syncStatus"] = "published"
                    report["unchanged"].append({"id": note_id, "title": post["title"]})
            else:
                post = {
                    "id": note_id,
                    "slug": note_id,
                    "title": existing_content_item.get("title") or note.get("title") or "未命名",
                    "updatedAt": existing_meta_item.get("updatedAt") or now_str(),
                    "publishedAt": existing_meta_item.get("publishedAt") or "",
                    "tags": [],
                    "summary": existing_content_item.get("summary") or "",
                    "contentHtml": existing_content_item.get("contentHtml") or "",
                    "syncHash": existing_meta_item.get("syncHash") or "",
                    "syncStatus": "published",
                }
                report["unchanged"].append({"id": note_id, "title": post["title"]})
            posts.append(normalize_post_metadata(post))
        except Exception as e:
            try:
                set_label(note.get("noteId"), "syncStatus", "error")
            except Exception:
                pass
            report["failed"].append({"id": note.get("noteId"), "title": note.get("title"), "error": str(e)[:300]})

    if report["failed"]:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    report["includeCards"] = apply_include_note_cards(posts)
    write_generated_files(posts)
    # Reclaim old URL-hash variants only after a complete, usable snapshot.
    # A failed download may leave a remote attachment URL without raising above.
    if any(find_attachment_urls(post["contentHtml"]) for post in posts):
        report["assetCleanupSkipped"] = "snapshot contains unresolved attachment URLs"
    else:
        try:
            report["prunedAssets"] = prune_unused_assets(posts + [{"id": item["id"], "contentHtml": ""} for item in report["removed"]], ASSET_DIR)
        except (ValueError, OSError) as error:
            report["assetCleanupSkipped"] = str(error)[:300]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
