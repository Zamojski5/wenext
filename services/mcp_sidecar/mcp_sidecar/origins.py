"""Exact-origin allowlist for `open_allowed_url` (L2486, D13).

There is one rule and it is hard: we compare the CANONICAL ORIGIN (scheme + host + port),
never a domain suffix. eTLD+1 does not serve to authorize — `zly-fixture-site.example.com`
and `fixture-site.example.com` share the same eTLD+1 and have different origins, so allowing
by suffix would mean allowing any subdomain someone registers.

In F0 the only allowed origin is the fixture site — the `capsulenet` network is
`internal: true`, so anything else has no route anyway. The allowlist is here regardless,
because the boundary is to be code, not a property of a network topology someone will
one day loosen.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: The only origin allowed in F0.
FIXTURE_SITE_ORIGIN = "http://fixture-site:8000"

#: Default ports per scheme — needed so that `http://host` and `http://host:80` are
#: the same origin, and not two different allowlist entries.
DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}

#: Schemes that `open_allowed_url` will never open, even if someone put them into the
#: allowlist. `file:` and `data:` read the capsule's disk or inject content from the prompt.
FORBIDDEN_SCHEMES: frozenset[str] = frozenset(
    {"file", "data", "javascript", "blob", "about", "chrome", "devtools", "view-source", "ftp"}
)


class OriginRejected(Exception):
    """A URL outside the exact-origin allowlist. Fail-closed, with no domain-suffix fallback."""


def canonical_origin(url: str) -> str:
    """Returns `scheme://host[:port]` in a comparable form.

    Canonicalization BEFORE comparison: scheme and host lowercased, the default port
    dropped, IDN normalized to its ASCII form (so that a homograph does not pass as
    "another spelling of the same host").
    """
    parts = urlsplit(url.strip())
    schemat = parts.scheme.lower()
    if not schemat:
        raise OriginRejected(f"URL {url!r} nie ma schematu — origin jest nieokreślony")
    if schemat in FORBIDDEN_SCHEMES:
        raise OriginRejected(f"schemat {schemat!r} jest zakazany w każdej konfiguracji")
    if schemat not in DEFAULT_PORTS:
        raise OriginRejected(f"schemat {schemat!r} nie jest obsługiwany (dozwolone: http, https)")

    host = (parts.hostname or "").lower()
    if not host:
        raise OriginRejected(f"URL {url!r} nie ma hosta")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        # A host that cannot be normalized to IDN also cannot be compared against the
        # allowlist in a way one could trust.
        raise OriginRejected(f"host {host!r} nie jest poprawnym IDN") from None

    try:
        port = parts.port
    except ValueError as exc:
        raise OriginRejected(f"URL {url!r} ma niepoprawny port") from exc
    if port is None or port == DEFAULT_PORTS[schemat]:
        return f"{schemat}://{host}"
    return f"{schemat}://{host}:{port}"


def is_allowed(url: str, allowlist: tuple[str, ...] | list[str]) -> bool:
    """Whether the URL's origin is EXACTLY on the allowlist. No suffix matching."""
    try:
        origin = canonical_origin(url)
    except OriginRejected:
        return False
    canonical_origins = {canonical_origin(a) for a in allowlist}
    return origin in canonical_origins


def require_allowed(url: str, allowlist: tuple[str, ...] | list[str]) -> str:
    """Returns the URL if its origin is on the allowlist. Otherwise raises `OriginRejected`."""
    origin = canonical_origin(url)
    canonical_origins = sorted({canonical_origin(a) for a in allowlist})
    if origin not in canonical_origins:
        raise OriginRejected(
            f"origin {origin!r} nie jest na allowliście. Dozwolone dokładnie: "
            f"{', '.join(canonical_origins)}. Zgodność eTLD+1 NIE autoryzuje niczego."
        )
    return url
