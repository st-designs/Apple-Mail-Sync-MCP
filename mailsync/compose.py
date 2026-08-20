"""Building, previewing and executing outgoing mail."""

from __future__ import annotations

import datetime as dt
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from . import emlx, osa, search, security


@dataclass
class Outgoing:
    account: str
    sender: str
    to: list[str]
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    subject: str = ""
    body: str = ""
    attachments: list[str] = field(default_factory=list)
    mode: str = "send"          # send | draft
    in_reply_to: int | None = None

    def to_payload(self) -> dict:
        return {
            "account": self.account, "sender": self.sender, "to": self.to,
            "cc": self.cc, "bcc": self.bcc, "subject": self.subject, "body": self.body,
            "attachments": self.attachments, "mode": self.mode,
            "in_reply_to": self.in_reply_to,
        }

    @classmethod
    def from_payload(cls, d: dict) -> Outgoing:
        return cls(**d)


def preview(msg: Outgoing) -> str:
    """The text the user reads before approving. Show everything that will go out."""
    lines = [
        "The following message is ready but has NOT been sent.",
        "",
        f"  Action  : {'SEND NOW' if msg.mode == 'send' else 'SAVE TO DRAFTS'}",
        f"  From    : {msg.sender}   (account: {msg.account})",
        f"  To      : {', '.join(msg.to)}",
    ]
    if msg.cc:
        lines.append(f"  Cc      : {', '.join(msg.cc)}")
    if msg.bcc:
        lines.append(f"  Bcc     : {', '.join(msg.bcc)}  << blind copy")
    lines.append(f"  Subject : {msg.subject or '(no subject)'}")
    if msg.attachments:
        for a in msg.attachments:
            p = Path(a)
            size = p.stat().st_size if p.is_file() else 0
            lines.append(f"  Attach  : {p.name} ({size // 1024} KB)")

    body = msg.body or "(empty body)"
    shown = body if len(body) <= 4000 else body[:4000] + f"\n[... {len(body) - 4000} more characters ...]"
    lines += ["", "  " + "-" * 66, ""]
    lines += [textwrap.indent(shown, "  ")]
    lines += ["", "  " + "-" * 66, ""]
    total = len(msg.to) + len(msg.cc) + len(msg.bcc)
    verb = "send" if msg.mode == "send" else "save"
    lines.append(f"Confirm to {verb} this message to {total} recipient(s).")
    return "\n".join(lines)


def execute(msg: Outgoing) -> str:
    osa.ensure_mail_running()
    args = [
        msg.mode,
        msg.sender,
        msg.subject,
        msg.body,
        "no",
        str(len(msg.to)),
        str(len(msg.cc)),
        str(len(msg.bcc)),
        str(len(msg.attachments)),
        *msg.to,
        *msg.cc,
        *msg.bcc,
        *msg.attachments,
    ]
    return osa.run("compose", *args, timeout=90)


def _quote(body: str, limit: int = 6000) -> str:
    body = (body or "").strip()
    if len(body) > limit:
        body = body[:limit] + "\n[... quoted text truncated ...]"
    return "\n".join("> " + ln for ln in body.split("\n"))


def _fmt_date(ts: int) -> str:
    if not ts:
        return "an earlier date"
    return dt.datetime.fromtimestamp(ts).strftime("%a, %d %b %Y at %H:%M")


def load_body(rowid: int, allow_fetch: bool = False) -> tuple[str, bool]:
    """Body text for a stored message, plus whether the copy is partial.

    Mail keeps header-only copies of messages it has not fully downloaded. With
    allow_fetch, ask Mail for the full source, which makes it pull the body from
    the server. That needs Mail running, so it is opt-in rather than automatic.
    """
    row = search.get_row(rowid)
    if row is None:
        raise ValueError(f"No message with id {rowid}")

    path = row["path"]
    if path and Path(path).is_file():
        try:
            parsed = emlx.parse(Path(path))
            if parsed.body:
                return parsed.body, parsed.partial
        except Exception:
            pass

    conn = search.open_index()
    r = conn.execute("SELECT body FROM messages_fts WHERE rowid=?", (rowid,)).fetchone()
    conn.close()
    cached = r["body"] if r else ""
    partial = bool(row["partial"])

    if allow_fetch and row["rfc_message_id"]:
        fetched = fetch_from_mail(row)
        if fetched and len(fetched) > len(cached):
            return fetched, False
    return cached, partial


def fetch_from_mail(row) -> str:
    """Pull a message's full source through Mail and return its body text."""
    from . import index

    acct = index.load_accounts().get(row["account"])
    if not acct or not acct.address:
        return ""
    try:
        osa.ensure_mail_running()
        raw = osa.run(
            "fetch_source", acct.address,
            (row["mailbox"] or "INBOX").split("/")[-1], row["rfc_message_id"],
            timeout=60,
        )
    except osa.AppleScriptError:
        return ""
    if not raw.strip():
        return ""

    import email
    import email.policy

    try:
        msg = email.message_from_string(raw, policy=email.policy.default)
        body, _ = emlx._extract_body(msg)
        return body
    except Exception:
        return ""


def build_reply(rowid: int, body: str, reply_all: bool, sender: str, account: str) -> Outgoing:
    row = search.get_row(rowid)
    if row is None:
        raise ValueError(f"No message with id {rowid}")
    original, _ = load_body(rowid)

    to = [row["sender"]] if row["sender"] else []
    cc: list[str] = []
    if reply_all:
        others = [a for a in (row["recipients"] or "").split() if a and a.lower() != (sender or "").lower()]
        cc = [a for a in dict.fromkeys(others) if a not in to]

    subject = row["subject"] or ""
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject

    attribution = f"On {_fmt_date(row['date_received'])}, {row['sender_name'] or row['sender']} wrote:"
    quoted = f"{body.rstrip()}\n\n{attribution}\n{_quote(original)}\n"
    return Outgoing(account=account, sender=sender, to=to, cc=cc, subject=subject,
                    body=quoted, in_reply_to=rowid)


def build_forward(rowid: int, to: list[str], note: str, sender: str, account: str) -> Outgoing:
    row = search.get_row(rowid)
    if row is None:
        raise ValueError(f"No message with id {rowid}")
    original, _ = load_body(rowid)

    subject = row["subject"] or ""
    if not subject.lower().startswith(("fwd:", "fw:")):
        subject = "Fwd: " + subject

    header = (
        "---------- Forwarded message ----------\n"
        f"From: {row['sender_name'] or ''} <{row['sender']}>\n"
        f"Date: {_fmt_date(row['date_received'])}\n"
        f"Subject: {row['subject'] or ''}\n"
        f"To: {row['recipients'] or ''}\n"
    )
    body = f"{note.rstrip()}\n\n{header}\n{original}" if note else f"{header}\n{original}"
    return Outgoing(account=account, sender=sender, to=to, subject=subject,
                    body=security.validate_body(body), in_reply_to=rowid)
