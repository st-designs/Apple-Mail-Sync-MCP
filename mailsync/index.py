"""Read-only access to Apple Mail's Envelope Index and message files.

Everything Apple owns is treated as immutable: the live database is never opened
writable and never locked. We copy it aside and read the copy.
"""

from __future__ import annotations

import shutil
import sqlite3
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from . import paths


@dataclass(frozen=True)
class Account:
    uuid: str
    address: str
    description: str

    @property
    def label(self) -> str:
        return self.address or self.description or self.uuid


@dataclass
class MessageRow:
    rowid: int
    account: str
    mailbox: str
    subject: str
    sender: str
    sender_name: str
    date_received: int
    date_sent: int
    read: bool
    flagged: bool
    conversation_id: int
    size: int
    preview: str
    path: Path | None = None


def snapshot_envelope_index() -> Path:
    """Copy the live index aside so we never touch Mail's own files.

    The -wal holds committed pages that aren't in the main file yet, so all
    three parts must travel together or the copy reads stale.
    """
    paths.ensure_cache()
    src = paths.envelope_index()
    dst = paths.SNAPSHOT_DIR / "Envelope Index"
    for suffix in ("", "-wal", "-shm"):
        s = Path(str(src) + suffix)
        d = Path(str(dst) + suffix)
        if s.exists():
            shutil.copy2(s, d)
        elif d.exists():
            d.unlink()
    return dst


def connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{urllib.parse.quote(str(db))}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def mail_account_uuids(version_dir: Path | None = None) -> set[str]:
    """UUIDs that actually hold mail, i.e. have a directory under V<n>.

    The Accounts database also lists Calendar/Contacts/iCloud services that
    share the same username, so it can't be used unfiltered.
    """
    root = version_dir or paths.mail_version_dir()
    return {p.name for p in root.iterdir() if p.is_dir() and p.name != "MailData"}


def load_accounts(version_dir: Path | None = None) -> dict[str, Account]:
    """Map Mail's account UUIDs to addresses.

    OAuth accounts (Gmail, Microsoft) store the address on a parent row and
    leave the mail child blank, so the parent has to be joined in.
    """
    out: dict[str, Account] = {}
    try:
        wanted = mail_account_uuids(version_dir)
    except (FileNotFoundError, PermissionError, OSError):
        wanted = set()
    if not paths.ACCOUNTS_DB.exists():
        return out

    tmp = paths.SNAPSHOT_DIR / "Accounts4.sqlite"
    paths.ensure_cache()
    try:
        for suffix in ("", "-wal", "-shm"):
            s = Path(str(paths.ACCOUNTS_DB) + suffix)
            if s.exists():
                shutil.copy2(s, Path(str(tmp) + suffix))
    except (PermissionError, OSError):
        # No Full Disk Access. Callers fall back to the cached copy.
        return out

    try:
        conn = connect(tmp)
        rows = conn.execute(
            """
            SELECT a.ZIDENTIFIER AS uuid,
                   COALESCE(NULLIF(a.ZUSERNAME,''), p.ZUSERNAME, '') AS address,
                   COALESCE(NULLIF(a.ZACCOUNTDESCRIPTION,''), p.ZACCOUNTDESCRIPTION, '') AS descr
            FROM ZACCOUNT a
            LEFT JOIN ZACCOUNT p ON a.ZPARENTACCOUNT = p.Z_PK
            WHERE a.ZIDENTIFIER IS NOT NULL
            """
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return out

    # The Accounts database reuses descriptions across sibling service rows, so
    # a mail account can pick up a mailbox-ish label that means nothing here.
    noise = {"junk", "personal", "icloud", "inbox", "drafts", "sent", "trash", "archive"}

    for r in rows:
        uuid = r["uuid"]
        if not uuid or (wanted and uuid not in wanted):
            continue
        descr = (r["descr"] or "").strip()
        if descr.lower() in noise or descr.lower() == (r["address"] or "").lower():
            descr = ""
        out[uuid] = Account(uuid, r["address"] or "", descr)

    # A local "On My Mac" store has no Accounts row but still holds mail.
    for uuid in wanted - out.keys():
        out[uuid] = Account(uuid, "", "On My Mac")
    return out


def scan_message_files(version_dir: Path | None = None) -> dict[int, Path]:
    """Map Envelope Index ROWID to the .emlx file holding that message.

    Mail names each file after the row's ROWID. Reconstructing paths from
    mailbox URLs instead would be fragile: real mailbox names contain glob
    metacharacters ("[Gmail]"), so one filesystem walk is both safer and faster.
    """
    root = version_dir or paths.mail_version_dir()
    found: dict[int, Path] = {}
    for path in root.rglob("*.emlx"):
        stem = path.name.split(".", 1)[0]
        if not stem.isdigit():
            continue
        rowid = int(stem)
        prev = found.get(rowid)
        # Prefer the complete copy when a partial exists alongside it.
        if prev is None or (prev.name.endswith(".partial.emlx") and not path.name.endswith(".partial.emlx")):
            found[rowid] = path
    return found


def mailbox_display_name(url: str) -> tuple[str, str]:
    """Split a mailbox URL into (account uuid, human-readable mailbox path)."""
    parsed = urllib.parse.urlparse(url)
    account = parsed.netloc or ""
    parts = [urllib.parse.unquote(p) for p in parsed.path.strip("/").split("/") if p]
    return account, "/".join(parts) or "INBOX"


def iter_messages(conn: sqlite3.Connection, accounts: dict[str, Account]):
    query = """
    SELECT m.ROWID AS rowid, mb.url AS mailbox_url,
           COALESCE(m.subject_prefix,'') AS prefix,
           COALESCE(s.subject,'') AS subject,
           COALESCE(a.address,'') AS sender,
           COALESCE(a.comment,'') AS sender_name,
           m.date_received, m.date_sent, m.read, m.flagged,
           m.conversation_id, m.size,
           COALESCE(sm.summary,'') AS preview
    FROM messages m
    JOIN mailboxes mb ON m.mailbox = mb.ROWID
    LEFT JOIN subjects  s  ON m.subject = s.ROWID
    LEFT JOIN addresses a  ON m.sender  = a.ROWID
    LEFT JOIN summaries sm ON m.summary = sm.ROWID
    WHERE m.deleted = 0
    """
    for r in conn.execute(query):
        acct_uuid, mailbox = mailbox_display_name(r["mailbox_url"])
        yield MessageRow(
            rowid=r["rowid"],
            account=acct_uuid,
            mailbox=mailbox,
            subject=(r["prefix"] or "") + (r["subject"] or ""),
            sender=r["sender"],
            sender_name=r["sender_name"],
            date_received=r["date_received"] or 0,
            date_sent=r["date_sent"] or 0,
            read=bool(r["read"]),
            flagged=bool(r["flagged"]),
            conversation_id=r["conversation_id"] or 0,
            size=r["size"] or 0,
            preview=r["preview"],
        )
