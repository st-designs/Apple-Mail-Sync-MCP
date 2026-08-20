"""Parsing for Apple Mail's .emlx message files."""

from __future__ import annotations

import email
import email.policy
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

# Mail truncates messages it hasn't fully downloaded into *.partial.emlx, which
# hold headers and usually a stub body. Worth indexing, but never treat the body
# as complete.
PARTIAL_SUFFIX = ".partial.emlx"

_SKIP_TAGS = {"script", "style", "head", "title"}
_BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:
        # Malformed markup is common in bulk mail; keep whatever was parsed.
        pass
    return collapse_whitespace(p.text())


def collapse_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


@dataclass
class ParsedMessage:
    path: Path
    partial: bool
    message_id: str = ""
    subject: str = ""
    from_addr: str = ""
    from_name: str = ""
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    date: str = ""
    body: str = ""
    body_is_html: bool = False
    attachments: list[tuple[str, str, int]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)


def read_rfc822(path: Path) -> bytes:
    """Strip the .emlx envelope and return the raw RFC-822 message.

    The format is a byte count on its own line, exactly that many bytes of
    message, then an Apple plist of local metadata that we ignore.
    """
    raw = path.read_bytes()
    nl = raw.find(b"\n")
    if nl == -1:
        return raw
    try:
        length = int(raw[:nl].strip())
    except ValueError:
        # No count prefix: some files are plain RFC-822.
        return raw
    return raw[nl + 1 : nl + 1 + length]


def _addresses(msg, header: str) -> list[str]:
    out = []
    for addr in msg.get_all(header, []):
        try:
            for a in getattr(addr, "addresses", ()) or ():
                if a.addr_spec:
                    out.append(a.addr_spec)
        except Exception:
            continue
    return out


def parse(path: Path) -> ParsedMessage:
    path = Path(path)
    result = ParsedMessage(path=path, partial=path.name.endswith(PARTIAL_SUFFIX))

    msg = email.message_from_bytes(read_rfc822(path), policy=email.policy.default)

    for key in ("Message-Id", "Subject", "Date", "In-Reply-To", "References", "List-Id"):
        val = msg.get(key)
        if val:
            result.headers[key] = str(val)

    result.message_id = result.headers.get("Message-Id", "").strip("<> ")
    result.subject = str(msg.get("Subject") or "")
    result.date = result.headers.get("Date", "")
    result.to = _addresses(msg, "To")
    result.cc = _addresses(msg, "Cc")

    sender = _addresses(msg, "From")
    if sender:
        result.from_addr = sender[0]
    try:
        f = msg.get("From")
        if f and getattr(f, "addresses", None):
            result.from_name = f.addresses[0].display_name or ""
    except Exception:
        pass

    result.body, result.body_is_html = _extract_body(msg)

    for part in msg.iter_attachments() if msg.is_multipart() else ():
        name = part.get_filename()
        if name:
            payload = part.get_payload(decode=True) or b""
            result.attachments.append((name, part.get_content_type(), len(payload)))

    return result


def _part_text(part) -> tuple[str, bool]:
    try:
        content = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        content = payload.decode(charset, errors="replace")
    if not isinstance(content, str):
        return "", False
    if part.get_content_type() == "text/html":
        return html_to_text(content), True
    return collapse_whitespace(content), False


def _extract_body(msg) -> tuple[str, bool]:
    """Pick the best readable body.

    Two traps in multipart/alternative mail. The text/plain part is often empty,
    and it is often a stub like "Plain text version not available" while the real
    content sits in the HTML sibling. Preferring plain unconditionally throws the
    message away in both cases, so compare the candidates and take the better one.
    """
    plain = html = ""
    for pref, target in (("plain", "plain"), ("html", "html")):
        try:
            part = msg.get_body(preferencelist=(pref,))
        except Exception:
            part = None
        if part is None:
            continue
        text, _ = _part_text(part)
        if target == "plain":
            plain = text
        else:
            html = text

    if not plain and not html:
        # Last resort: walk every text part and take the longest.
        best, best_html = "", False
        try:
            for part in (msg.walk() if msg.is_multipart() else [msg]):
                if part.get_content_maintype() != "text" or part.get_filename():
                    continue
                text, is_html = _part_text(part)
                if len(text) > len(best):
                    best, best_html = text, is_html
        except Exception:
            pass
        return best, best_html

    # A short plain part next to a much larger HTML one is a placeholder.
    if html and len(plain) < 500 and len(html) > len(plain) * 2:
        return html, True
    if plain:
        return plain, False
    return html, True
