"""Two-phase commit for every operation that changes mail state.

Nothing in this server both decides and acts. A prepare_* tool validates a
request, renders a preview and parks it here; commit_action executes it. The
commit tool accepts a token and nothing else, so the payload that runs is always
the payload that was shown, and an instruction buried in an email body cannot
reach Mail.app without a human having seen the preview first.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

DEFAULT_TTL = 300.0
MAX_PENDING = 32


class ConfirmError(RuntimeError):
    pass


@dataclass
class Pending:
    token: str
    kind: str
    payload: dict[str, Any]
    preview: str
    account: str
    created: float = field(default_factory=time.time)
    ttl: float = DEFAULT_TTL

    @property
    def expires_in(self) -> float:
        return (self.created + self.ttl) - time.time()

    def digest(self) -> str:
        blob = json.dumps(self.payload, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


class PendingStore:
    """In-memory only. Restarting the server voids every pending action."""

    def __init__(self, ttl: float = DEFAULT_TTL) -> None:
        self._items: dict[str, Pending] = {}
        self._lock = threading.Lock()
        self._ttl = ttl

    def _reap(self) -> None:
        now = time.time()
        for tok in [t for t, p in self._items.items() if p.created + p.ttl <= now]:
            self._items.pop(tok, None)

    def add(self, kind: str, payload: dict[str, Any], preview: str, account: str = "") -> Pending:
        with self._lock:
            self._reap()
            if len(self._items) >= MAX_PENDING:
                oldest = min(self._items.values(), key=lambda p: p.created)
                self._items.pop(oldest.token, None)
            item = Pending(
                token=secrets.token_urlsafe(18),
                kind=kind,
                payload=payload,
                preview=preview,
                account=account,
                ttl=self._ttl,
            )
            self._items[item.token] = item
            return item

    def peek(self, token: str) -> Pending | None:
        with self._lock:
            self._reap()
            return self._items.get(token)

    def consume(self, token: str) -> Pending:
        """Single use. A token that has run cannot run again."""
        with self._lock:
            self._reap()
            item = self._items.pop(token, None)
        if item is None:
            raise ConfirmError(
                "No pending action matches that token. It was already used, it expired "
                f"(actions live {int(self._ttl)}s), or the server restarted. "
                "Run the matching prepare_* tool again to get a fresh preview."
            )
        return item

    def pending(self) -> list[Pending]:
        with self._lock:
            self._reap()
            return sorted(self._items.values(), key=lambda p: p.created)


STORE = PendingStore()
