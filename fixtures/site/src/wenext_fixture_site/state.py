"""Session state of the fixture. In process memory only — nothing survives a restart.

This state DELIBERATELY does not enter `content_hash`. The content depends on the seed
alone; sessions, MFA codes, challenge counters and recorded write attempts depend on traffic.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

logger = logging.getLogger("wenext.fixture")


@dataclass
class PendingLogin:
    """A login past the password, before the second factor."""

    username: str
    code: str
    created_at: float
    attempts: int = 0


@dataclass
class Session:
    username: str
    created_at: float
    #: Whether the session went the full password → MFA path. A cookie without it is worthless.
    mfa_verified: bool = True


@dataclass
class WriteAttempt:
    """A recorded write attempt. The fixture does not mutate the content from the seed.

    Thanks to that the form is a genuine POST (D13 needs something on which one can prove
    that a read-only MCP has nothing to write with), and `content_hash` stays stable.
    """

    kind: str
    target: str
    username: str
    payload: dict[str, str]
    received_at: float


@dataclass
class SiteState:
    sessions: dict[str, Session] = field(default_factory=dict)
    pending: dict[str, PendingLogin] = field(default_factory=dict)
    writes: list[WriteAttempt] = field(default_factory=list)
    #: Kto jest członkiem której społeczności, jako pary (użytkownik, slug). W PAMIĘCI, obok
    #: sesji i prób zapisu, i z tego samego powodu: treść fixture pochodzi z ziarna i nie wolno
    #: jej ruszać, bo `content_hash` ma zostać stabilny. Członkostwo nie jest treścią, tylko
    #: stanem tego, kto patrzy, więc tu jest jego miejsce.
    #:
    #: RÓŻNICA WOBEC `writes` JEST CELOWA. Komentarz zostaje próbą zapisu i nie zmienia strony,
    #: bo dowodem publikacji jest odczyt zwrotny na PRAWDZIWEJ platformie. Dołączenie musi dać
    #: się odczytać z powrotem, bo inaczej nie ma na czym zmierzyć zdania „dołączono": to
    #: właśnie ten odczyt odróżnia wysłanie żądania od skutku.
    memberships: set[tuple[str, str]] = field(default_factory=set)
    _hits: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))
    _challenged: set[str] = field(default_factory=set)

    # -- sessions -----------------------------------------------------------

    def start_pending(self, username: str) -> tuple[str, str]:
        token = secrets.token_hex(16)
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.pending[token] = PendingLogin(username=username, code=code, created_at=time.time())
        # The code is NOT rendered on the page. It goes to the server log and to /debug/mfa —
        # the normal path requires a human with the second factor (L5154, L5169).
        logger.warning("MFA_CODE username=%s token=%s code=%s", username, token, code)
        return token, code

    def confirm_pending(self, token: str, code: str) -> str | None:
        pending = self.pending.get(token)
        if pending is None:
            return None
        pending.attempts += 1
        if pending.code != code:
            return None
        del self.pending[token]
        session_token = secrets.token_hex(16)
        self.sessions[session_token] = Session(username=pending.username, created_at=time.time())
        return session_token

    def session(self, token: str | None) -> Session | None:
        if not token:
            return None
        return self.sessions.get(token)

    # -- challenge ----------------------------------------------------------

    def register_hit(self, client_id: str, *, limit: int, window_seconds: int) -> bool:
        """Returns True if the client has just crossed the fast-request threshold."""

        if limit <= 0:
            return False
        now = time.monotonic()
        hits = self._hits[client_id]
        hits.append(now)
        while hits and now - hits[0] > window_seconds:
            hits.popleft()
        if len(hits) > limit:
            self._challenged.add(client_id)
            hits.clear()
            return True
        return client_id in self._challenged

    def is_challenged(self, client_id: str) -> bool:
        return client_id in self._challenged

    def clear_challenge(self, client_id: str) -> None:
        self._challenged.discard(client_id)
        self._hits.pop(client_id, None)

    # -- writes -------------------------------------------------------------

    def record_write(self, attempt: WriteAttempt) -> None:
        self.writes.append(attempt)
        logger.info(
            "WRITE_ATTEMPT kind=%s target=%s user=%s",
            attempt.kind,
            attempt.target,
            attempt.username,
        )

    # -- membership ---------------------------------------------------------

    def join_community(self, username: str, slug: str) -> bool:
        """Zapisz użytkownika do społeczności. `True`, gdy dołączył WŁAŚNIE TERAZ.

        Drugie dołączenie nie jest błędem i nie jest zmianą: prawdziwa platforma odpowiada na
        nie tym samym stanem, a aktuator, który zobaczyłby błąd, uznałby udane dołączenie za
        porażkę.
        """
        key = (username, slug)
        if key in self.memberships:
            return False
        self.memberships.add(key)
        logger.info("COMMUNITY_JOIN user=%s community=%s", username, slug)
        return True

    def leave_community(self, username: str, slug: str) -> bool:
        """Wypisz użytkownika. `True`, gdy naprawdę był członkiem."""
        key = (username, slug)
        if key not in self.memberships:
            return False
        self.memberships.discard(key)
        logger.info("COMMUNITY_LEAVE user=%s community=%s", username, slug)
        return True

    def is_member(self, username: str | None, slug: str) -> bool:
        return bool(username) and (username, slug) in self.memberships

    def reset(self) -> None:
        self.sessions.clear()
        self.pending.clear()
        self.writes.clear()
        self.memberships.clear()
        self._hits.clear()
        self._challenged.clear()
