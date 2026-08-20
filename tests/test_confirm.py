import time
import pytest
from mailsync.confirm import PendingStore, ConfirmError


def test_token_is_single_use():
    s = PendingStore()
    tok = s.add("send", {"to": ["a@b.com"]}, "preview").token
    assert s.consume(tok).payload["to"] == ["a@b.com"]
    with pytest.raises(ConfirmError):
        s.consume(tok)


def test_forged_token_rejected():
    s = PendingStore()
    s.add("send", {"to": ["a@b.com"]}, "preview")
    for forged in ("", "not-a-token", "x" * 24):
        with pytest.raises(ConfirmError):
            s.consume(forged)


def test_expired_token_rejected():
    s = PendingStore(ttl=0.05)
    tok = s.add("send", {"to": ["a@b.com"]}, "preview").token
    time.sleep(0.1)
    with pytest.raises(ConfirmError):
        s.consume(tok)


def test_payload_cannot_be_swapped():
    # commit takes only a token, so the executed payload is always the shown one
    s = PendingStore()
    a = s.add("send", {"to": ["real@x.com"]}, "p")
    b = s.add("send", {"to": ["attacker@evil.com"]}, "p")
    assert s.consume(a.token).payload["to"] == ["real@x.com"]
    assert s.consume(b.token).payload["to"] == ["attacker@evil.com"]


def test_store_is_bounded():
    s = PendingStore()
    for i in range(60):
        s.add("send", {"i": i}, "p")
    assert len(s.pending()) <= 32
