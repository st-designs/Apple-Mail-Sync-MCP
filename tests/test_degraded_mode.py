"""Without Full Disk Access the server must still answer from its own index.

Apple gates ~/Library/Mail and ~/Library/Accounts behind Full Disk Access, and an
MCP server inherits the grant of whichever app launched it. Claude Code and
Claude Desktop are separate TCC clients, so the no-access path is a normal
operating state, not an edge case.
"""


import pytest

from mailsync import index, paths


@pytest.fixture
def no_access(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    monkeypatch.setattr(paths, "MAIL_ROOT", blocked)
    monkeypatch.setattr(paths, "ACCOUNTS_DB", blocked / "Accounts4.sqlite")
    return blocked


def test_load_accounts_does_not_raise(no_access):
    assert index.load_accounts() == {}


def test_mail_readable_reports_the_problem(no_access):
    ok, why = paths.mail_readable()
    assert ok is False
    assert why


def test_uuid_scan_survives_missing_root(no_access):
    with pytest.raises(FileNotFoundError):
        paths.mail_version_dir()
    # load_accounts swallows it rather than propagating
    assert index.load_accounts() == {}


def test_permission_error_is_swallowed(tmp_path, monkeypatch):
    """A directory that exists but cannot be read must not crash the server."""
    blocked = tmp_path / "V10"
    (blocked / "MailData").mkdir(parents=True)
    (blocked / "MailData" / "Envelope Index").write_bytes(b"x")
    monkeypatch.setattr(paths, "MAIL_ROOT", tmp_path)

    def boom(*_a, **_k):
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(index.shutil, "copy2", boom)
    assert index.load_accounts() == {}
