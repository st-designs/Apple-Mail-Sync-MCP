"""Filesystem locations for Apple Mail data and our own cache."""

from __future__ import annotations

import os
from pathlib import Path

MAIL_ROOT = Path(os.path.expanduser("~/Library/Mail"))
ACCOUNTS_DB = Path(os.path.expanduser("~/Library/Accounts/Accounts4.sqlite"))

CACHE_DIR = Path(os.environ.get("MAILSYNC_HOME", os.path.expanduser("~/.local/share/mailsync")))
SNAPSHOT_DIR = CACHE_DIR / "snapshot"
INDEX_DB = CACHE_DIR / "mailsync.db"
AUDIT_LOG = CACHE_DIR / "audit.jsonl"


def mail_version_dir() -> Path:
    """Newest V<n> directory. Apple bumps this between major macOS releases."""
    candidates = sorted(
        (p for p in MAIL_ROOT.glob("V*") if p.is_dir() and p.name[1:].isdigit()),
        key=lambda p: int(p.name[1:]),
    )
    if not candidates:
        raise FileNotFoundError(f"No Apple Mail data directory under {MAIL_ROOT}")
    return candidates[-1]


def envelope_index(version_dir: Path | None = None) -> Path:
    return (version_dir or mail_version_dir()) / "MailData" / "Envelope Index"


def ensure_cache() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def mail_readable() -> tuple[bool, str]:
    """Whether this process can read Apple Mail's store.

    macOS gates ~/Library/Mail behind Full Disk Access, and an MCP server
    inherits the grant of whichever app launched it. Claude Code and Claude
    Desktop are separate TCC clients, so one can work while the other cannot.
    """
    try:
        idx = envelope_index()
    except FileNotFoundError:
        return False, (
            f"No Apple Mail data found under {MAIL_ROOT}. Is Mail set up for this user?"
        )
    try:
        with open(idx, "rb") as fh:
            fh.read(16)
    except PermissionError:
        return False, (
            "This app does not have Full Disk Access, so it cannot read ~/Library/Mail.\n"
            "Open System Settings > Privacy & Security > Full Disk Access, switch on the "
            "app you are using (Claude for Desktop and/or Claude Code), then quit and "
            "reopen it completely."
        )
    except OSError as exc:
        return False, f"Could not read Apple Mail's index: {exc}"
    return True, ""
