"""The signed header the edge puts on every request it forwards: who the person is, vouched for.

WHO DOES WHAT. A Cloudflare Worker (`infra/cloudflare/mcp-edge/`) is the public face of hosted
Wenext and the OAuth 2.1 authorization server towards Codex and Claude Code: it runs the
browser sign-in (GitHub upstream), issues the agent its tokens, and verifies them on every
request. THIS process never sees an OAuth token and verifies none. What the Worker forwards to
`POST /mcp` here carries one extra header, `X-Wenext-Edge`, a short-lived HS256 JWT signed with
the secret both sides hold (`WENEXT_EDGE_SECRET`):

    claims: provider, subject, email, login, iat, exp (60 s), jti

and that header is the whole of the identity this side trusts. A request without it, with a
signature that does not match, with an expired or not-yet-valid window, or with a `jti` already
seen inside its lifetime, is refused with a bare 401.

WHY THIS IS ENOUGH, AND WHAT IT RELIES ON. The secret never leaves the Worker's secrets store and
this server's environment, so only the Worker can mint a header; a person cannot forge "I am
somebody else". Because the header is signed over its own claims and lives a minute, a captured
request cannot be replayed later (`exp`) and not twice inside the minute (`jti`, kept in memory
until `exp`; one process serves the endpoint, and with more than one the same `jti` would need a
shared store). It does NOT bind the header to a body: anybody who can reach the origin AND
obtained a header in its minute could pair it with another body. That is why the origin should
accept `/mcp` only from Cloudflare (a firewall or a Cloudflare-only rule on the VPS), and why the
header is short.

THE ALGORITHM IS FIXED to HS256 and checked before anything else: a header naming `none` or any
other algorithm is refused, never "tried". Standard library only (`hmac`, `hashlib`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

#: The header the Worker sets, and the environment variable holding the shared secret.
EDGE_HEADER = "x-wenext-edge"
ENV_EDGE_SECRET = "WENEXT_EDGE_SECRET"

#: A shorter secret is a guessable one: the Worker's secret is generated, not chosen.
MIN_SECRET_CHARS = 32

#: What the Worker must send, and what this side accepts. `exp - iat` above the cap means somebody
#: minted a header that lives longer than a minute's worth of replay; it is refused.
MAX_LIFETIME_S = 120
CLOCK_LEEWAY_S = 30.0
MAX_HEADER_CHARS = 4096
MAX_FIELD_CHARS = 255


class EdgeRejected(Exception):
    """The header is absent or not genuine. `code` is for the diagnostic line, never a token."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EdgeIdentity:
    """Who the edge says is calling. `provider` is the upstream login (`github`)."""

    provider: str
    subject: str
    email: str | None = None
    login: str | None = None


def _b64url_decode(segment: str) -> bytes:
    if not segment or any(ch not in _B64URL for ch in segment):
        raise EdgeRejected("malformed")
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


_B64URL = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_edge_header(
    secret: str,
    *,
    provider: str,
    subject: str,
    email: str | None = None,
    login: str | None = None,
    now: float | None = None,
    ttl: int = 60,
    jti: str = "",
) -> str:
    """The header value the Worker produces. Lives here so the tests mint exactly what it does."""
    issued = int(now if now is not None else time.time())
    claims = {
        "provider": provider,
        "subject": subject,
        "email": email,
        "login": login,
        "iat": issued,
        "exp": issued + ttl,
        "jti": jti or _b64url_encode(os.urandom(16)),
    }
    head = _b64url_encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode()
    )
    body = _b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    mac = hmac.new(secret.encode("utf-8"), f"{head}.{body}".encode("ascii"), hashlib.sha256)
    return f"{head}.{body}.{_b64url_encode(mac.digest())}"


class EdgeVerifier:
    """Checks `X-Wenext-Edge` headers against the shared secret. One per process (it holds the
    replay memory)."""

    def __init__(
        self,
        secret: str,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(secret) < MIN_SECRET_CHARS:
            raise ValueError(f"{ENV_EDGE_SECRET} ma mieć co najmniej {MIN_SECRET_CHARS} znaków")
        self._secret = secret.encode("utf-8")
        self._clock = clock
        #: jti -> exp. Pruned on every verification, so it holds about a minute of traffic.
        self._seen: dict[str, float] = {}

    @staticmethod
    def from_env(env: dict[str, str] | None = None) -> EdgeVerifier | None:
        source = os.environ if env is None else env
        secret = (source.get(ENV_EDGE_SECRET) or "").strip()
        return EdgeVerifier(secret) if secret else None

    def verify(self, header_value: str | None) -> EdgeIdentity:
        if not header_value:
            raise EdgeRejected("absent")
        if len(header_value) > MAX_HEADER_CHARS:
            raise EdgeRejected("size")
        parts = header_value.strip().split(".")
        if len(parts) != 3:
            raise EdgeRejected("malformed")
        try:
            header = json.loads(_b64url_decode(parts[0]))
        except (ValueError, UnicodeDecodeError):
            raise EdgeRejected("malformed") from None
        # BEFORE the signature, and a hard equality: no "none", no other MAC, no negotiation.
        if not isinstance(header, dict) or header.get("alg") != "HS256":
            raise EdgeRejected("alg")
        expected = hmac.new(
            self._secret, f"{parts[0]}.{parts[1]}".encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64url_decode(parts[2])):
            raise EdgeRejected("signature")
        try:
            claims = json.loads(_b64url_decode(parts[1]))
        except (ValueError, UnicodeDecodeError):
            raise EdgeRejected("malformed") from None
        if not isinstance(claims, dict):
            raise EdgeRejected("malformed")

        now = self._clock()
        issued, expires = claims.get("iat"), claims.get("exp")
        if not _is_number(issued) or not _is_number(expires):
            raise EdgeRejected("claims")
        if now > float(expires) + CLOCK_LEEWAY_S:
            raise EdgeRejected("expired")
        if now < float(issued) - CLOCK_LEEWAY_S:
            raise EdgeRejected("not-yet")
        if float(expires) - float(issued) > MAX_LIFETIME_S:
            raise EdgeRejected("lifetime")

        jti = claims.get("jti")
        if not isinstance(jti, str) or not jti or len(jti) > MAX_FIELD_CHARS:
            raise EdgeRejected("jti")
        self._forget_expired(now)
        if jti in self._seen:
            raise EdgeRejected("replay")
        self._seen[jti] = float(expires) + CLOCK_LEEWAY_S

        provider, subject = claims.get("provider"), claims.get("subject")
        if not _is_text(provider) or not _is_text(subject):
            raise EdgeRejected("identity")
        return EdgeIdentity(
            provider=str(provider).strip(),
            subject=str(subject).strip(),
            email=_optional_text(claims.get("email"), 320),
            login=_optional_text(claims.get("login"), 200),
        )

    def _forget_expired(self, now: float) -> None:
        for jti, until in list(self._seen.items()):
            if until < now:
                del self._seen[jti]


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_text(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= MAX_FIELD_CHARS


def _optional_text(value: object, limit: int) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()[:limit]
    return None
