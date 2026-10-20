"""Deterministic summaries from published HTML; no editor fields or model calls."""

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


def normalize_post_metadata(post):
    """Use the cached public body, never an unrequested draft, during migration."""
    result = {**post, "summary": summarize_html(post.get("contentHtml"), post.get("title")), "tags": []}
    result.pop("pinned", None)
    return result
