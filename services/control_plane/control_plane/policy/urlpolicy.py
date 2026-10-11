"""URL policy of the control plane — section 19.4, L3929-3946, scope settled by D-019.

D-019 IS THE REASON THIS FILE IS IN F1 AT ALL. The roadmap puts URL/egress policy in F1
(L5197); `docs/threat-model/controls.yaml` assigned C-06 to F2. The roadmap wins, because
`STAN-PROJEKTU.md` calls it the only source of truth about the product and a derived document
does not narrow a phase. What lands here is the part that closes WITHOUT a browser.

REUSE, NOT A COPY. `services/mcp_sidecar/mcp_sidecar/origins.py` already implements
canonicalization, exact-origin comparison and `FORBIDDEN_SCHEMES` covering all five schemes of
L3944. It is imported, never reimplemented: `test_the_canonicalizer_is_the_sidecar_object_
itself_not_a_second_copy` asserts object identity, so a copy-paste fails a test instead of
drifting quietly. The sidecar package is NOT modified — its F0 suite has to stay green.

WHAT THIS MODULE ADDS, read out of that file rather than assumed:

1.  **HTTPS only, outside an EXPLICITLY marked fixture (L3936).** In the sidecar
    `DEFAULT_PORTS = {"http": 80, "https": 443}` accepts `http` for any host whatsoever, and
    `FIXTURE_SITE_ORIGIN` is a constant no branch of the logic ever reads — so the fixture is
    in no way distinguished from a production host. Here plaintext needs a SECOND, separate
    list: an `http://` entry on the allowlist that is not in `plaintext_fixture_origins` is
    refused at construction. Marking it is a deliberate act that shows up in a diff.

2.  **An empty allowlist and an unloaded policy are refusals.** Not because the comparison
    would pass — `origin in frozenset()` is False — but because an object that exists while
    authorizing nothing is a configuration bug wearing the costume of a working state, and the
    next person widens the list to "fix" it. The precedent in this repository is
    `TaskScopedCapability.__post_init__`, which refuses an empty `allowed_origins` for exactly
    this reason. `all([])` is `True` and has caused three false passes here.

3.  **An IP-literal host is refused in F1.** Two measured reasons, not taste. (a) The reused
    `canonical_origin` renders an IPv6 literal as a bare, ambiguous string —
    `https://[2606:4700::1111]:8443` becomes `https://2606:4700::1111:8443`, in which host and
    port can no longer be told apart — so exact-origin comparison is not trustworthy there,
    and the sidecar must not be edited. (b) A literal skips DNS, so the "checked after DNS
    resolution" of L3943 would have nothing to check. Refusing is the fail-closed answer and
    costs nothing: the fixture and every real target are names.

4.  **A URL that two conforming parsers read as two different hosts is refused.** Added
    2026-08-31 after it was measured, not designed in: `https://evil.com\\@api.example.com/x`
    was AUTHORIZED here with `host='api.example.com'`, and every WHATWG parser — browser,
    `fetch`, Chromium over CDP — reads `evil.com` out of that same string, because `\\` ends
    the authority for them and is userinfo for `urllib`. `AuthorizedUrl.url` is the field E1.F2
    hands to the browser, so this was one name authorized and another name connected to. See
    `_reject_ambiguous_url` for the two rules and for what they explicitly do NOT claim.

WHAT IS NOT HERE: redirects, popups, iframes, WebSockets, service workers and downloads
(L3937-3942). F1 has no navigation and no proxy, so there is no surface on which those could
be enforced; they are named in `registry.py` as six NOT_ENFORCED entries with their target
phase. A function returning False is not enforcement, and a test named after enforcement it
does not perform is the overclaim `contracts/tests/test_invariants.py` already committed once.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

# --------------------------------------------------------------------------------------------
# The single canonicalizer of this repository, imported from the sidecar.
#
# The plain import is the intended path and it works once `wenext-mcp-sidecar` is installed in
# this environment. MEASURED TODAY: it is NOT installed in `services/control_plane/.venv`, and
# `services/control_plane/Dockerfile` copies only `control_plane/` and `contracts/`, so the
# image has no `mcp_sidecar` either. Neither file is in this package's remit, so the gap is
# reported to the orchestrator rather than patched from here — and the fallback below keeps the
# development checkout honest in the meantime.
#
# The fallback is a path bootstrap, never a copy of the code, and when it cannot find the
# sidecar it RAISES. A silent local reimplementation is how two definitions of "origin" start
# drifting apart; a loud ImportError names the fix instead.
# --------------------------------------------------------------------------------------------
try:
    from mcp_sidecar.origins import (
        DEFAULT_PORTS,
        FORBIDDEN_SCHEMES,
        OriginRejected,
        canonical_origin,
    )
except ModuleNotFoundError:
    _SIDECAR_ROOT = Path(__file__).resolve().parents[3] / "mcp_sidecar"
    if not (_SIDECAR_ROOT / "mcp_sidecar" / "origins.py").is_file():
        raise ImportError(
            "control_plane.policy wymaga mcp_sidecar.origins jako JEDYNEJ kanonikalizacji "
            f"originów w repozytorium, a nie ma go ani w środowisku, ani w {_SIDECAR_ROOT}. "
            "Napraw to jedną z dwóch dróg: dodaj `wenext-mcp-sidecar` do zależności "
            "services/control_plane/pyproject.toml i skopiuj pakiet w Dockerfile, albo "
            "przenieś origins.py do `contracts` (dwie usługi już go potrzebują). "
            "NIE kopiuj tego pliku — dwie kanonikalizacje to dwie definicje originu"
        ) from None
    sys.path.insert(0, str(_SIDECAR_ROOT))
    from mcp_sidecar.origins import (
        DEFAULT_PORTS,
        FORBIDDEN_SCHEMES,
        OriginRejected,
        canonical_origin,
    )

#: The only scheme allowed outside an explicitly marked fixture (L3936).
HTTPS = "https"

#: Re-exported deliberately, and `FORBIDDEN_SCHEMES` is re-exported even though nothing here
#: reads it: it is the L3944 list, and a test asserts it is the sidecar's own OBJECT. Anyone
#: needing the scheme block imports it from here and gets the one list, not a second one.
__all__ = [
    "DEFAULT_PORTS",
    "FORBIDDEN_SCHEMES",
    "HTTPS",
    "AuthorizedUrl",
    "OriginRejected",
    "PolicyNotLoaded",
    "PolicyRefusal",
    "UrlPolicy",
    "UrlRejected",
    "canonical_origin",
    "require_url",
]


class PolicyRefusal(OriginRejected):
    """Base of every refusal this control plane makes.

    Derived from the sidecar's `OriginRejected` on purpose: a caller that writes
    `except OriginRejected` around a navigation attempt stays fail-closed against the rules
    added here, instead of letting a control-plane refusal escape as an unhandled error.
    """


class UrlRejected(PolicyRefusal):
    """The URL is not authorized: wrong origin, wrong scheme, or an unusable host."""


class PolicyNotLoaded(PolicyRefusal):
    """There is no policy — or it authorizes nothing. A refusal, never a permissive default.

    Separate from `UrlRejected` because the operator's fix is different: this one is a
    configuration fault, not a caller passing a bad URL.
    """


@dataclass(frozen=True)
class AuthorizedUrl:
    """The result of an authorization, carrying the values later layers must not re-derive.

    `host` is the canonicalized name — lowercased and IDN-normalized — and it exists as a
    field precisely so the egress layer resolves THE SAME string the allowlist matched.
    Re-parsing the raw URL downstream is how a system authorizes one name and connects to
    another, which would make the L3943 check measure something the connection never uses.

    THIS DOCSTRING WAS AN OVERCLAIM UNTIL 2026-08-31 and the correction belongs here rather
    than in a footnote. `url` is returned VERBATIM, and E1.F2 hands exactly this field to a
    browser — a WHATWG parser. Measured: `https://evil.com\\@api.example.com/x` was authorized
    with `host='api.example.com'` while any browser reads `evil.com` out of the same string.
    So the class of bug named in the paragraph above was open in the very field that would
    suffer it. `_reject_ambiguous_url` now runs first in `require_url` and refuses the two
    measured divergences (a backslash in the authority; tab, LF and CR anywhere).

    WHAT IS STILL TRUE AND WHAT IS NOT. `host` remains the field a downstream layer must use —
    that has not changed and is not negotiable. What `url` now carries is a string in which the
    two divergences we measured cannot hide; it is NOT a proof of parser equivalence with
    WHATWG, and no code may treat it as one. When F2 introduces the actual browser, this field
    gets re-measured against that browser rather than against a model of it.
    """

    url: str
    origin: str
    scheme: str
    host: str
    port: int


#: Characters that `urllib.parse` and every WHATWG parser DELETE from a URL before parsing it.
#: Measured, not remembered: `urllib.parse._UNSAFE_URL_BYTES_TO_REMOVE == ['\t', '\r', '\n']`
#: (the bpo-43882 fix), and the WHATWG algorithm begins with "remove all ASCII tab or newline
#: from input". Named individually so the refusal says which one was found.
_DELETED_BEFORE_PARSING: tuple[tuple[str, str], ...] = (
    ("\t", "tabulacja (U+0009)"),
    ("\n", "znak nowej linii (U+000A)"),
    ("\r", "powrót karetki (U+000D)"),
)

#: WHATWG treats `\` exactly like `/` for a special scheme (`http`, `https`), so a backslash
#: ENDS the authority. `urllib` does not, and keeps it inside the netloc.
_WHATWG_ENDS_AUTHORITY = "\\"


def _reject_ambiguous_url(url: str, what: str) -> None:
    """Refuses a URL that two conforming parsers read as two DIFFERENT hosts.

    THIS FUNCTION EXISTS BECAUSE THE `AuthorizedUrl` DOCSTRING PROMISED IT AND THE CODE DID
    NOT DELIVER IT. `AuthorizedUrl.url` is handed on verbatim and in E1.F2 it is handed to a
    browser; browsers, `fetch` and Chromium over CDP all parse by the WHATWG rules, `urllib`
    does not. Where the two disagree, this policy authorizes one name and the consumer connects
    to another — the failure mode that docstring names in its own last sentence.

    Two rules, both measured on this interpreter (CPython 3.12.4) rather than recalled:

    1.  **A backslash in the authority.** `https://evil.com\\@api.example.com/x` was
        AUTHORIZED with `host='api.example.com'`: `urllib` reads `evil.com\\` as userinfo. A
        browser stops the authority at the backslash and navigates to `evil.com`. It cuts the
        other way too — `https://api.example.com:443\\@evil.com/x` is `evil.com` to `urllib`
        and `api.example.com` to a browser. Refusing the character refuses both directions.
        A backslash AFTER the authority is deliberately left alone: WHATWG turns it into `/`,
        so the path differs and the ORIGIN — the thing F1 authorizes — does not.

    2.  **Tab, LF and CR anywhere in the URL.** Here the two parsers AGREE, and this is not
        claimed as a host-confusion bypass. What breaks is the other half of the same promise:
        both parsers DELETE these characters, so the verbatim string in `AuthorizedUrl.url` is
        not the string that was checked (`https://api.example.\\tcom/x` canonicalized to the
        allowlisted `https://api.example.com` and carried the tab onward), and a URL holding a
        CR or an LF is a request-splitting and log-splitting payload for whatever writes it
        into a header or a log line.

    WHAT THIS DOES NOT CLAIM. It is not parser equivalence with WHATWG — nobody should read it
    as "the string is now safe for any parser". It closes the two divergences that were
    measured in this repository. Others (UTS-46 versus Python's `idna` codec, percent-encoding
    in the authority) were probed in the same session and came out either in agreement or in
    the refusing direction; when F2 adds a real browser, that surface gets re-measured against
    the browser instead of against a model of it.
    """
    for character, name in _DELETED_BEFORE_PARSING:
        if character in url:
            raise UrlRejected(
                f"{what} {url!r} zawiera {name}. Parsery URL-i — i urllib, i każda "
                "przeglądarka — USUWAJĄ ten znak przed parsowaniem, więc autoryzowany byłby "
                "inny string niż ten przekazany dalej; w nagłówku albo w logu ten sam znak "
                "rozcina wiersz. Odmowa"
            )

    # After the loop above the string has no characters urlsplit would delete, so `netloc` is a
    # VERBATIM slice of it — which is what makes the backslash check below trustworthy.
    try:
        authority = urlsplit(url.strip()).netloc
    except ValueError as exc:
        # `urlsplit` raises a BARE ValueError on at least two authority shapes (measured: an
        # authority holding U+FF0F FULLWIDTH SOLIDUS, from `_checknetloc`'s NFKC rule, and
        # `https://[nazwa]/x`, from the IPv6 parse of a bracketed name — the test writes that
        # character as the escape `\uff0f` rather than pasting it, because ruff RUF001/RUF003
        # flags it as a confusable and it IS one on purpose, that being the whole point).
        # `canonical_origin` does not catch it and `require_url` catches only
        # `OriginRejected`, so before this line a ValueError escaped the policy entirely.
        # Everything here is built so that ONE `except PolicyRefusal` is fail-closed; an
        # exception outside that hierarchy becomes an unhandled 500 and teaches the next caller
        # to write `except Exception`.
        raise UrlRejected(f"{what} {url!r} jest nieparsowalny dla urllib: {exc}") from exc

    if _WHATWG_ENDS_AUTHORITY in authority:
        raise UrlRejected(
            f"{what} {url!r} ma odwrotny ukośnik w części authority ({authority!r}). Parser "
            "WHATWG — każda przeglądarka, fetch w JS, Chromium po CDP — traktuje '\\' jak '/', "
            "więc kończy na nim authority i czyta INNEGO hosta niż urllib. Autoryzowano by "
            "jedną nazwę, a połączenie poszłoby do drugiej. Odmowa"
        )


def _split_origin(origin: str) -> tuple[str, str, int]:
    """Takes a canonical origin apart into (scheme, host, port).

    Parsed back out of `canonical_origin`'s output rather than out of the URL, so that this
    module has NO normalization of its own — one normalizer, one definition of an origin.
    """
    scheme, _, rest = origin.partition("://")
    if rest.count(":") > 1:
        # An IPv6 literal: `canonical_origin` drops the brackets, so host and port are no
        # longer separable (measured: `https://[2606:4700::1111]:8443` →
        # `https://2606:4700::1111:8443`). Refused rather than guessed — see the module
        # docstring. Fixing it inside the sidecar is out of scope: its F0 suite stays green.
        raise UrlRejected(
            f"origin {origin!r} zawiera literał IPv6. Kanonikalizacja gubi nawiasy, więc host "
            "i port przestają być rozróżnialne, a dosłowny adres i tak omija DNS, czyli "
            "sprawdzenie z L3943 nie miałoby czego sprawdzać. W F1: odmowa"
        )
    host, _, port_text = rest.partition(":")
    if not host:
        raise UrlRejected(f"origin {origin!r} nie ma hosta")
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        raise UrlRejected(
            f"host {host!r} jest dosłownym adresem IP. Dosłowny adres omija DNS, więc kontrola "
            "adresów prywatnych i metadata po rozwiązaniu nazwy (L3943) nie ma czego "
            "sprawdzać. W F1: odmowa"
        )
    port = int(port_text) if port_text else DEFAULT_PORTS[scheme]
    return scheme, host, port


def _canonical_entry(text: str, what: str) -> str:
    """Canonicalizes a CONFIGURATION entry, turning garbage into a loud construction error."""
    # Before canonicalization, like at request time: an operator who pastes an ambiguous string
    # into configuration gets a loud error while somebody is still looking, rather than an entry
    # whose canonical form is not the host they read in the file.
    _reject_ambiguous_url(text, what)
    try:
        origin = canonical_origin(text)
    except OriginRejected as exc:
        raise UrlRejected(f"{what} {text!r} nie jest poprawnym originem: {exc}") from exc
    _split_origin(origin)  # rejects IP literals in the allowlist too, not only in requests
    return origin


@dataclass(frozen=True)
class UrlPolicy:
    """The exact-origin allowlist of the control plane, plus the two rules L3936 needs.

    `plaintext_fixture_origins` is the "jawnie testowy fixture" of L3936 made explicit. It is a
    SECOND list rather than a flag on an entry, because the question "which origins may be
    reached over plaintext?" then has one greppable answer, and the empty default — HTTPS only
    — is what you get by writing nothing.
    """

    allowed_origins: tuple[str, ...]
    plaintext_fixture_origins: tuple[str, ...] = ()
    #: Derived in `__post_init__`; not constructor arguments and not part of equality.
    canonical_allowed: frozenset[str] = field(
        init=False, repr=False, compare=False, default=frozenset()
    )
    canonical_fixtures: frozenset[str] = field(
        init=False, repr=False, compare=False, default=frozenset()
    )

    def __post_init__(self) -> None:
        if not self.allowed_origins:
            raise PolicyNotLoaded(
                "allowlista originów jest pusta. To błąd konfiguracji, nie stan pracy: "
                "polityka, która nie autoryzuje niczego, przy pierwszej poprawce zostanie "
                "przeczytana jako 'bez ograniczeń'. Ten sam warunek ma "
                "TaskScopedCapability.__post_init__ w sidecarze"
            )
        allowed = frozenset(_canonical_entry(o, "wpis allowlisty") for o in self.allowed_origins)
        fixtures = frozenset(
            _canonical_entry(o, "wpis fixture") for o in self.plaintext_fixture_origins
        )

        stray = sorted(fixtures - allowed)
        if stray:
            raise UrlRejected(
                f"originy oznaczone jako fixture, których nie ma na allowliście: {stray}. "
                "Oznaczenie fixture nie autoryzuje — ono tylko zdejmuje wymóg HTTPS"
            )

        insecure = sorted(o for o in allowed - fixtures if o.partition("://")[0] != HTTPS)
        if insecure:
            raise UrlRejected(
                f"wpisy bez HTTPS i bez oznaczenia fixture: {insecure}. L3936 dopuszcza "
                "plaintext WYŁĄCZNIE dla jawnie testowego fixture — dopisz je do "
                "plaintext_fixture_origins, jeżeli naprawdę o to chodzi"
            )

        object.__setattr__(self, "canonical_allowed", allowed)
        object.__setattr__(self, "canonical_fixtures", fixtures)

    def require_url(self, url: str) -> AuthorizedUrl:
        """Authorizes the URL or raises. Never returns None, never returns a bool.

        Order: reject a URL two parsers read differently, canonicalize (which is where the
        L3944 schemes die), then exact origin, then the plaintext rule. The scheme is part of
        the origin, so `http://allowed-host` already fails the allowlist when the entry is
        `https://allowed-host`; the explicit check below is defense in depth for the day
        somebody edits `__post_init__`.

        THE FIRST STEP IS FIRST FOR A REASON. It must run before the allowlist match, because
        the string it refuses is a string whose allowlist match is meaningless: the host that
        matched is not the host the consumer of `AuthorizedUrl.url` will connect to.
        """
        _reject_ambiguous_url(url, "URL")
        try:
            origin = canonical_origin(url)
        except OriginRejected as exc:
            raise UrlRejected(f"URL {url!r} odrzucony: {exc}") from exc

        scheme, host, port = _split_origin(origin)

        if origin not in self.canonical_allowed:
            raise UrlRejected(
                f"origin {origin!r} nie jest na allowliście control plane'u. Dozwolone "
                f"dokładnie: {', '.join(sorted(self.canonical_allowed))}. Zgodność eTLD+1 "
                "NIE autoryzuje niczego (L3935)"
            )

        if scheme != HTTPS and origin not in self.canonical_fixtures:
            raise UrlRejected(
                f"origin {origin!r} nie jest HTTPS, a nie jest oznaczony jako jawnie testowy "
                "fixture (L3936)"
            )

        return AuthorizedUrl(url=url, origin=origin, scheme=scheme, host=host, port=port)

    def is_authorized(self, url: str) -> bool:
        """Boolean form, for logging and diagnostics only.

        Nothing in the enforcement path may branch on this: a boolean loses the reason, and a
        reason is what an operator needs when a URL is refused. `require_url` is the gate.
        """
        try:
            self.require_url(url)
        except PolicyRefusal:
            return False
        return True


def require_url(url: str, policy: UrlPolicy | None) -> AuthorizedUrl:
    """The entry point that treats a MISSING policy as a refusal.

    `policy` is deliberately allowed to be None in the signature and deliberately fatal at
    runtime. Callers that hold an optional policy — everything wired from configuration —
    then cannot forget the case: there is no code path through this function that authorizes
    anything without a loaded policy.
    """
    if policy is None:
        raise PolicyNotLoaded(
            "polityka URL nie jest wczytana — odmowa. Niewczytana polityka nie jest brakiem "
            "ograniczeń; to stan, w którym nie wiadomo, co byłoby dozwolone"
        )
    return policy.require_url(url)
