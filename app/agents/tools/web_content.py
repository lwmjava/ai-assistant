"""Extract bounded readable text without executing untrusted page content."""

from __future__ import annotations

import codecs
import json
import re
from html.parser import HTMLParser

from app.agents.tools.outcome import ToolOutput
from app.security.public_http import FetchedPage

MAX_PARSE_EVENTS = 20000
MAX_PARSE_DEPTH = 128
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "wbr"}
_DROP = {"head", "script", "style", "template", "noscript", "svg", "canvas", "iframe", "object"}
_BLOCK = {
    "p",
    "div",
    "section",
    "article",
    "main",
    "header",
    "footer",
    "li",
    "ul",
    "ol",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "table",
    "tr",
    "td",
    "blockquote",
    "pre",
    "br",
    "hr",
}

_HEAD_TAGS = {
    "base",
    "basefont",
    "bgsound",
    "link",
    "meta",
    "title",
    "noscript",
    "noframes",
    "style",
    "script",
    "template",
}
_P_END = {
    "address",
    "article",
    "aside",
    "blockquote",
    "details",
    "div",
    "dl",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hgroup",
    "hr",
    "main",
    "menu",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "ul",
    "li",
}


class ParseLimitError(ValueError):
    """The parser exhausted its deterministic event/depth budget."""


class ReadableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool, bool]] = []
        self.body: list[str] = []
        self.main: list[str] = []
        self.title: list[str] = []
        self.events = 0

    def tick(self) -> None:
        self.events += 1
        if self.events > MAX_PARSE_EVENTS:
            raise ParseLimitError()

    def parse_html_declaration(self, index: int) -> int:
        # Keep unsupported marked declarations deterministic across Python versions.
        if self.rawdata.startswith("<![", index) and not self.rawdata.startswith("<![CDATA[", index):
            raise ValueError("invalid_html")
        return super().parse_html_declaration(index)

    def emit(self, value: str, hidden: bool, main: bool) -> None:
        if not hidden:
            self.body.append(value)
            if main:
                self.main.append(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tick()
        # Browser-compatible optional endings must not extend a hidden subtree.
        if tag not in _HEAD_TAGS and not any(frame[0] in _DROP - {"head"} for frame in self.stack):
            for index, frame in enumerate(self.stack):
                if frame[0] == "head":
                    del self.stack[index:]
                    break
        closing = {"p"} if tag in _P_END else set()
        if tag in {"li", "tr", "td", "th", "dt", "dd"}:
            closing.update({"td", "th"} if tag in {"td", "th"} else {"dt", "dd"} if tag in {"dt", "dd"} else {tag})
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] in _DROP - {"head"}:
                break
            if self.stack[index][0] in closing:
                del self.stack[index:]
                break
        attributes = dict(attrs)
        parent_hidden = self.stack[-1][1] if self.stack else False
        main = tag in {"main", "article"} or (self.stack[-1][2] if self.stack else False)
        style = re.sub(r"\s+", "", attributes.get("style") or "").lower()
        explicit_hidden = (
            "hidden" in attributes
            or (attributes.get("aria-hidden") or "").lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )
        hidden = parent_hidden or tag in _DROP or explicit_hidden
        if tag in _BLOCK:
            self.emit("\n", hidden, main)
        if tag not in _VOID:
            if len(self.stack) >= MAX_PARSE_DEPTH:
                raise ParseLimitError()
            self.stack.append((tag, hidden, main, explicit_hidden))

    def handle_endtag(self, tag: str) -> None:
        self.tick()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                _, hidden, main, _ = self.stack[index]
                if tag in _BLOCK:
                    self.emit("\n", hidden, main)
                del self.stack[index:]
                break
            if self.stack[index][0] in _DROP - {"head"}:
                break

    def handle_data(self, data: str) -> None:
        self.tick()
        if (
            self.stack
            and self.stack[-1][0] == "title"
            and not any(tag in _DROP - {"head"} or explicit_hidden for tag, _, _, explicit_hidden in self.stack)
        ):
            self.title.append(data)
        hidden = self.stack[-1][1] if self.stack else False
        main = self.stack[-1][2] if self.stack else False
        self.emit(data, hidden, main)

    def handle_comment(self, data: str) -> None:
        self.tick()

    def handle_decl(self, decl: str) -> None:
        self.tick()

    def unknown_decl(self, data: str) -> None:
        self.tick()

    def handle_pi(self, data: str) -> None:
        self.tick()


def _normalize(text: str) -> str:
    return "\n".join(line for raw in text.splitlines() if (line := " ".join(raw.split())))


def readable_observation(page: FetchedPage, maximum: int) -> str:
    """Describe source and text limits separately; never infer a full page from a prefix."""
    media = page.content_type.split(";", 1)[0].strip().lower()
    title = ""
    limited = False
    title_truncated = False
    error: str | None = None
    text = ""
    try:
        decoder = codecs.getincrementaldecoder(page.encoding)(errors="strict")
        source = decoder.decode(page.content, final=not page.download_truncated)
        html = media in {"text/html", "application/xhtml+xml"} or (
            not media and re.match(r"\s*<(?:!doctype\s+html|html|head|body|main|article|p)\b", source, re.I)
        )
        if html:
            parser = ReadableHTML()
            try:
                parser.feed(source)
                parser.close()
            except ParseLimitError:
                limited = True
            except (ValueError, AssertionError):
                error = "invalid_html"
            title_text = _normalize("".join(parser.title))
            title_truncated = len(title_text) > 256
            title = title_text[:256]
            text = _normalize("".join(parser.main)) or _normalize("".join(parser.body))
        elif not media or media.startswith("text/") or media == "application/json" or media.endswith("+json"):
            text = source
        else:
            error = "unsupported_content"
        if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
            text, error = "", "unsupported_content"
    except (LookupError, UnicodeError):
        error = "invalid_encoding"
    truncated = len(text) > maximum
    text = text[:maximum]
    if not text.strip() and error is None:
        error = "empty_content"
    reasons = []
    notices = []
    if page.download_truncated:
        reasons.append("download_limit")
        notices.append("原始响应达到下载上限，正文可能未下载完整。")
    if limited:
        reasons.append("parse_limit")
        notices.append("解析达到安全上限，正文可能不完整。")
    if truncated:
        reasons.append("text_limit")
        notices.append("提取后的正文超过字符预算，已截断。")
    if title_truncated:
        reasons.append("title_limit")
        notices.append("标题超过字符预算，已截断。")
    if error:
        reasons.append(error)
        notices.append(
            "未提取到可读正文，页面可能依赖JavaScript或不是受支持的文本；未执行脚本。"
            if error == "empty_content"
            else "内容类型或编码无法安全读取。"
        )
    if page.status_code >= 400:
        reasons.append("http_error")
        notices.append("服务器返回HTTP错误，不能视作成功读取页面。")
    if not notices:
        notices.append("已读取完整响应并提取文本；未进行浏览器渲染或执行JavaScript，不代表动态页面渲染结果。")
    result = json.dumps(
        {
            "http_status": page.status_code,
            "content_type": media or "unknown",
            "title": title,
            "text": text,
            "characters": len(text),
            "download_truncated": page.download_truncated,
            "text_truncated": truncated,
            "parse_limited": limited,
            "title_truncated": title_truncated,
            "complete": not (
                page.download_truncated or truncated or title_truncated or limited or error or page.status_code >= 400
            ),
            "reasons": reasons,
            "notice": " ".join(notices),
            "untrusted": True,
        },
        ensure_ascii=False,
    )
    failure = "http_error" if page.status_code >= 400 else error
    return ToolOutput(result, status="error" if failure else "ok", error_code=failure)
