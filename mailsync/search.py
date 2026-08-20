"""Local full-text index over Apple Mail's message store."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from collections.abc import Callable, Iterable

from . import emlx, index, paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    rowid           INTEGER PRIMARY KEY,
    account         TEXT NOT NULL,
    mailbox         TEXT NOT NULL,
    subject         TEXT,
    sender          TEXT,
    sender_name     TEXT,
    recipients      TEXT,
    date_received   INTEGER,
    date_sent       INTEGER,
    is_read         INTEGER,
    is_flagged      INTEGER,
    conversation_id INTEGER,
    size            INTEGER,
    path            TEXT,
    partial         INTEGER DEFAULT 0,
    mtime           REAL,
    has_attachments INTEGER DEFAULT 0,
    body_len        INTEGER DEFAULT 0,
    rfc_message_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_account   ON messages(account);
CREATE INDEX IF NOT EXISTS idx_received  ON messages(date_received);
CREATE INDEX IF NOT EXISTS idx_conv      ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_sender    ON messages(sender);
CREATE INDEX IF NOT EXISTS idx_rfcid     ON messages(rfc_message_id);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    subject, sender, recipients, body,
    tokenize = "unicode61 remove_diacritics 2"
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS accounts (
    uuid        TEXT PRIMARY KEY,
    address     TEXT,
    description TEXT
);
"""


@dataclass
class Hit:
    rowid: int
    account: str
    mailbox: str
    subject: str
    sender: str
    sender_name: str
    date_received: int
    is_read: bool
    is_flagged: bool
    conversation_id: int
    has_attachments: bool
    partial: bool
    rfc_message_id: str = ""
    thread_size: int = 1
    snippet: str = ""


def open_index(readonly: bool = False) -> sqlite3.Connection:
    paths.ensure_cache()
    conn = sqlite3.connect(paths.INDEX_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def build(progress: Callable[[str], None] | None = None, full: bool = False) -> dict:
    """Sync the local index with Mail's current state.

    Only re-parses a message when its file is new or its mtime moved, so the
    common case after the first build is a few seconds.
    """
    started = time.time()
    say = progress or (lambda _m: None)

    ok, why = paths.mail_readable()
    if not ok:
        raise PermissionError(why)

    conn = open_index()
    if full:
        conn.executescript("DELETE FROM messages; DELETE FROM messages_fts;")

    say("snapshotting Envelope Index")
    snap = index.snapshot_envelope_index()
    accounts = index.load_accounts()

    if accounts:
        conn.executemany(
            "INSERT INTO accounts(uuid,address,description) VALUES (?,?,?) "
            "ON CONFLICT(uuid) DO UPDATE SET address=excluded.address, description=excluded.description",
            [(a.uuid, a.address, a.description) for a in accounts.values()],
        )
        conn.commit()

    say("scanning message files")
    files = index.scan_message_files()

    say("reading message metadata")
    src = index.connect(snap)
    rows = list(index.iter_messages(src, accounts))
    src.close()

    known = {r["rowid"]: (r["mtime"], r["path"]) for r in conn.execute("SELECT rowid, mtime, path FROM messages")}

    added = updated = skipped = missing = failed = 0
    live: set[int] = set()
    batch: list[tuple] = []

    for row in rows:
        live.add(row.rowid)
        path = files.get(row.rowid)
        if path is None:
            missing += 1
        mtime = 0.0
        if path is not None:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                path = None

        prior = known.get(row.rowid)
        if prior:
            if path and abs((prior[0] or 0) - mtime) < 1e-6 and prior[1] == str(path):
                skipped += 1
                continue
            if path is None and prior[1] is None:
                # Mail has never stored this one locally; nothing to re-read.
                skipped += 1
                continue

        body = ""
        recipients = ""
        rfc_id = ""
        partial = False
        has_att = False
        if path is not None:
            try:
                parsed = emlx.parse(path)
                body = parsed.body
                partial = parsed.partial
                has_att = bool(parsed.attachments)
                recipients = " ".join(parsed.to + parsed.cc)
                rfc_id = parsed.message_id
            except Exception:
                failed += 1
        if not body:
            # Falls back to Mail's own preview so header-only messages stay searchable.
            body = row.preview

        batch.append(
            (
                row.rowid, row.account, row.mailbox, row.subject, row.sender, row.sender_name,
                recipients, row.date_received, row.date_sent, int(row.read), int(row.flagged),
                row.conversation_id, row.size, str(path) if path else None, int(partial),
                mtime, int(has_att), len(body), rfc_id, body,
            )
        )
        if prior:
            updated += 1
        else:
            added += 1

        if len(batch) >= 500:
            _flush(conn, batch)
            say(f"indexed {added + updated} messages")
            batch = []

    if batch:
        _flush(conn, batch)

    removed = 0
    stale = set(known) - live
    if stale:
        conn.executemany("DELETE FROM messages WHERE rowid=?", [(r,) for r in stale])
        conn.executemany("DELETE FROM messages_fts WHERE rowid=?", [(r,) for r in stale])
        removed = len(stale)

    conn.execute(
        "INSERT INTO meta(key,value) VALUES('last_build',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(int(time.time())),),
    )
    conn.commit()

    stats = {
        "added": added, "updated": updated, "unchanged": skipped, "removed": removed,
        "no_file_on_disk": missing, "parse_failures": failed,
        "total": conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
        "accounts": len([a for a in accounts.values()]),
        "seconds": round(time.time() - started, 1),
    }
    conn.close()
    return stats


def _flush(conn: sqlite3.Connection, batch: Iterable[tuple]) -> None:
    batch = list(batch)
    ids = [(b[0],) for b in batch]
    conn.executemany("DELETE FROM messages WHERE rowid=?", ids)
    conn.executemany("DELETE FROM messages_fts WHERE rowid=?", ids)
    conn.executemany(
        """INSERT INTO messages (rowid,account,mailbox,subject,sender,sender_name,recipients,
             date_received,date_sent,is_read,is_flagged,conversation_id,size,path,partial,
             mtime,has_attachments,body_len,rfc_message_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [b[:19] for b in batch],
    )
    conn.executemany(
        "INSERT INTO messages_fts(rowid,subject,sender,recipients,body) VALUES (?,?,?,?,?)",
        [(b[0], b[3], f"{b[5]} {b[4]}", b[6], b[19]) for b in batch],
    )
    conn.commit()


def mail_link(rfc_message_id: str) -> str:
    """A message:// URL that opens this message in Apple Mail.

    Verified against Mail on macOS 26: the URL opens the message in its own
    window. Returns "" when the Message-ID is unknown, which happens for the
    few messages Mail has not stored locally.
    """
    if not rfc_message_id:
        return ""
    import urllib.parse

    return "message://%3C" + urllib.parse.quote(rfc_message_id, safe="") + "%3E"


_FTS_SPECIAL = str.maketrans({c: " " for c in '"*():^-'})


def _fts_query(text: str) -> str:
    """Turn user text into a safe FTS5 MATCH expression.

    Bare user input is a syntax minefield in FTS5, so each term is quoted and
    the operators are dropped rather than escaped. Quoted phrases survive.
    """
    text = (text or "").strip()
    if not text:
        return ""
    phrases: list[str] = []
    rest = text
    while '"' in rest:
        first = rest.index('"')
        end = rest.find('"', first + 1)
        if end == -1:
            break
        phrase = rest[first + 1 : end].translate(_FTS_SPECIAL).strip()
        if phrase:
            phrases.append('"' + phrase + '"')
        rest = rest[:first] + " " + rest[end + 1 :]
    terms = [t for t in rest.translate(_FTS_SPECIAL).split() if t]
    phrases.extend('"' + t + '"' for t in terms)
    return " AND ".join(phrases)


def query(
    text: str = "",
    account: str | None = None,
    mailbox: str | None = None,
    sender: str | None = None,
    since: int | None = None,
    until: int | None = None,
    unread_only: bool = False,
    flagged_only: bool = False,
    has_attachments: bool | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[Hit], int]:
    conn = open_index()
    where: list[str] = []
    args: list = []

    if account:
        where.append("m.account = ?")
        args.append(account)
    if mailbox:
        where.append("m.mailbox LIKE ?")
        args.append(f"%{mailbox}%")
    if sender:
        where.append("(m.sender LIKE ? OR m.sender_name LIKE ?)")
        args += [f"%{sender}%", f"%{sender}%"]
    if since:
        where.append("m.date_received >= ?")
        args.append(since)
    if until:
        where.append("m.date_received <= ?")
        args.append(until)
    if unread_only:
        where.append("m.is_read = 0")
    if flagged_only:
        where.append("m.is_flagged = 1")
    if has_attachments is not None:
        where.append("m.has_attachments = ?")
        args.append(int(bool(has_attachments)))

    match = _fts_query(text)
    if match:
        base = """FROM messages_fts f JOIN messages m ON m.rowid = f.rowid
                  WHERE messages_fts MATCH ?"""
        args = [match] + args
        order = "ORDER BY bm25(messages_fts, 8.0, 4.0, 2.0, 1.0), m.date_received DESC"
        snippet = ", snippet(messages_fts, 3, '<<', '>>', ' … ', 18) AS snip"
    else:
        base = "FROM messages m WHERE 1=1"
        order = "ORDER BY m.date_received DESC"
        snippet = ", '' AS snip"

    clause = (" AND " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) {base}{clause}", args).fetchone()[0]

    sql = f"""SELECT m.rowid, m.account, m.mailbox, m.subject, m.sender, m.sender_name,
                     m.date_received, m.is_read, m.is_flagged, m.conversation_id,
                     m.has_attachments, m.partial, m.rfc_message_id,
                     (SELECT COUNT(*) FROM messages t WHERE t.conversation_id = m.conversation_id
                       AND m.conversation_id <> 0) AS thread_size{snippet}
              {base}{clause} {order} LIMIT ? OFFSET ?"""
    rows = conn.execute(sql, args + [limit, offset]).fetchall()
    conn.close()

    hits = [
        Hit(
            rowid=r["rowid"], account=r["account"], mailbox=r["mailbox"],
            subject=r["subject"] or "", sender=r["sender"] or "",
            sender_name=r["sender_name"] or "", date_received=r["date_received"] or 0,
            is_read=bool(r["is_read"]), is_flagged=bool(r["is_flagged"]),
            conversation_id=r["conversation_id"] or 0,
            has_attachments=bool(r["has_attachments"]), partial=bool(r["partial"]),
            rfc_message_id=r["rfc_message_id"] or "",
            thread_size=r["thread_size"] or 1,
            snippet=(r["snip"] or "").replace("\n", " ").strip(),
        )
        for r in rows
    ]
    return hits, total


def get_row(rowid: int) -> sqlite3.Row | None:
    conn = open_index()
    r = conn.execute("SELECT * FROM messages WHERE rowid=?", (rowid,)).fetchone()
    conn.close()
    return r


def thread(conversation_id: int, limit: int = 100) -> list[sqlite3.Row]:
    conn = open_index()
    rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY date_received ASC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
    conn.close()
    return rows


def cached_accounts() -> dict[str, tuple[str, str]]:
    """Account identities recorded at index time.

    Read tools use this so they keep working when the process cannot reach
    ~/Library/Accounts, which needs Full Disk Access.
    """
    try:
        conn = open_index()
        rows = conn.execute("SELECT uuid, address, description FROM accounts").fetchall()
        conn.close()
    except sqlite3.Error:
        return {}
    return {r["uuid"]: (r["address"] or "", r["description"] or "") for r in rows}
