"""Author-written mascot summaries and deterministic SEO excerpts; no model calls."""

import re
from html.parser import HTMLParser


class ProseParser(HTMLParser):
    ignored = {"pre", "table", "blockquote", "script", "style", "svg", "figure", "aside", "nav", *[f"h{i}" for i in range(1, 7)]}
    boundaries = {"p", "div", "li", "br", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skipped = []
        self.parts = []
        self.blocks = []

    def flush(self):
        text = re.sub(r"\s+", " ", "".join(self.parts)).strip()
        if text:
            self.blocks.append(text)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.ignored:
            if not self.skipped:
                self.flush()
            self.skipped.append(tag)
        elif not self.skipped and tag in self.boundaries:
            self.flush()

    def handle_endtag(self, tag):
        if tag in self.skipped:
            index = len(self.skipped) - 1 - self.skipped[::-1].index(tag)
            del self.skipped[index:]
        elif not self.skipped and tag in self.boundaries:
            self.flush()

    def handle_data(self, data):
        if not self.skipped:
            self.parts.append(data)


def summarize_html(content_html, title="", limit=80):
    parser = ProseParser()
    parser.feed(content_html or "")
    parser.flush()
    text = next((block for block in parser.blocks if len(block) >= 16), "")
    if not text:
        text = f"一起读读《{title or '这篇文章'}》。"
    if len(text) <= limit:
        return text
    prefix = text[:limit - 1]
    sentence = max(prefix.rfind(mark) for mark in "。！？")
    if sentence >= 24:
        return prefix[:sentence + 1]
    clause = max(prefix.rfind(mark) for mark in "，；、")
    if clause >= 24:
        return prefix[:clause] + "。"
    return prefix.rstrip("，、；：,;: ") + "…"


def note_url_identifier(note_id):
    """Match the blog's case-preserving ASCII noteId route contract."""
    if not isinstance(note_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", note_id):
        raise ValueError("Article URLs require a non-empty ASCII Trilium noteId")
    return note_id


def manual_summary(note):
    """The Chinese 摘要 label is the only source of mascot replies."""
    values = [attr.get("value") or "" for attr in note.get("attributes") or []
              if attr.get("type") == "label" and attr.get("name") == "摘要"]
    return str(values[-1]).replace("\r\n", "\n").replace("\r", "\n").strip() if values else ""


def normalize_post_metadata(post):
    """Use the published body for fallback SEO text, without inventing mascot replies."""
    handwritten = str(post.get("manualSummary") or "").strip()
    result = {**post, "slug": note_url_identifier(post.get("id")),
              "manualSummary": handwritten,
              "summary": handwritten or summarize_html(post.get("contentHtml"), post.get("title")), "tags": []}
    result.pop("pinned", None)
    return result
