"""Fetching one address, with the refusals that make it safe to point at a user's URL.

THIS IS THE FIRST PRODUCTION USER OF `control_plane.policy.egress`. That module was written for
L3943, tested with 9 cases, and until P3 nothing imported it — a boundary nobody crossed. A
fetcher that takes a URL from a form is exactly what it is for: without it
`http://169.254.169.254/latest/meta-data/` is an ordinary-looking address that returns cloud
credentials, and `http://10.0.0.5/` reaches whatever the deployment sits next to.

WHAT IS CHECKED, IN ORDER, BEFORE ANY BYTE LEAVES:

  1. the scheme is `https`. Not "not obviously bad" — one scheme, and `file:`, `gopher:`,
     `data:` and the rest are refused by not being it;
  2. the host resolves, and EVERY address it resolves to is publicly routable
     (`require_public_addresses`). Every, not any: the connection picks the address and the
     check does not, so a host answering [routable, 169.254.169.254] is a coin flip;
  3. a redirect is NOT followed by the HTTP client. Each `Location` goes through 1 and 2 before
     the next request is sent, because a check made after the client already followed the chain
     objects to a request that has happened;
  4. the response is read with a byte ceiling and a deadline, so a slow or endless body is a
     refusal rather than a worker held for an hour.

WHAT IS *NOT* DONE, AND SAYING SO IS THE POINT. `EgressPolicy.verify_connected_address` — the
rebinding half — is NOT wired in. Two measurements from 2026-09-19: httpx 0.28.1 reports
`network_stream.get_extra_info("server_addr")` as `None` once the response has been read, so
there is nothing to verify afterwards; and connecting to a pinned address with `Host` and
`sni_hostname` set timed out against a real CDN (example.com behind Cloudflare), so pinning the
socket is not a change to make blind. The window between resolution and connect therefore stays
open here. It is narrow, the class check above is the defence that matters, and this paragraph
exists so nobody reads this module as closing something it does not.

THE FIXTURE ESCAPE HATCH IS EXPLICIT AND EMPTY BY DEFAULT. `WENEXT_KNOWLEDGE_FIXTURE_ORIGINS`
lists origins that may be reached over plaintext and on a non-routable address — the "jawnie
testowy fixture" of L3936, the same idea `UrlPolicy.plaintext_fixture_origins` already encodes.
The end-to-end suite sets it to its own local server; production sets nothing, and an origin
that is not on the list is refused on both counts.
"""

from __future__ import annotations

import hashlib
import html
import os
import re
import socket
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from control_plane.policy.egress import EgressRejected, require_public_addresses

#: Ceiling on one fetched body. Large enough for an article or a feed, small enough that a
#: knowledge source cannot become the reason the process runs out of memory.
MAX_BYTES = 2_000_000

#: Whole-request deadline. A reader that waits longer than this is a worker nobody gets back.
TIMEOUT_S = 15.0

#: The one scheme. Anything else is refused by not being on the list rather than by a blocklist,
#: because a blocklist of schemes is a list somebody has to keep complete.
ALLOWED_SCHEMES: tuple[str, ...] = ("https",)

#: How many hops a chain may have. Low on purpose: a legitimate article is one or two redirects
#: away, and a long chain is a way to spend a worker's deadline.
MAX_REDIRECTS = 3

FIXTURE_ORIGINS_VARIABLE = "WENEXT_KNOWLEDGE_FIXTURE_ORIGINS"

_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WHITESPACE = re.compile("[ \t\u00a0]+")
_BLANK_LINES = re.compile(r"\n{3,}")


class FetchRefused(Exception):
    """The address was refused before anything was sent. Carries the sentence to show."""


@dataclass(frozen=True)
class Fetched:
    """One read of one address. `ok=False` is a STATE with a reason, never an empty body."""

    ok: bool
    url: str
    status: int | None
    content: bytes
    content_type: str
    title: str
    text: str
    reason: str | None
    truncated: bool

    @property
    def content_hash(self) -> str | None:
        """sha256 of the bytes that really arrived, or None when nothing did."""
        if not self.ok or not self.content:
            return None
        return "sha256:" + hashlib.sha256(self.content).hexdigest()


def fixture_origins(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """Origins allowed over plaintext and on a non-routable address. Empty unless set.

    Comma separated, compared as lowercase `scheme://host:port`. An empty value means an empty
    set and therefore no exception to either rule — which is what production runs with.
    """
    raw = (os.environ if env is None else env).get(FIXTURE_ORIGINS_VARIABLE, "")
    return frozenset(entry.strip().lower() for entry in raw.split(",") if entry.strip())


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def _resolver(host: str) -> Sequence[str]:
    """Every address the name answers with. See `egress.Resolver` for why every, not the first."""
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def check_address(url: str, *, env: Mapping[str, str] | None = None, resolver=None) -> str:
    """Refuse the URL, or return its host. Raises `FetchRefused` with a sentence.

    Separated from the request so a caller — and a test — can ask "would this be allowed?"
    without sending anything.
    """
    parts = urlsplit(url)
    if not parts.hostname:
        raise FetchRefused(f"{url!r} nie ma hosta, więc nie ma czego sprawdzić ani odwiedzić")

    origin = _origin(url)
    is_fixture = origin in fixture_origins(env)

    if parts.scheme.lower() not in ALLOWED_SCHEMES and not is_fixture:
        raise FetchRefused(
            f"schemat {parts.scheme!r} nie jest dozwolony. Pobieramy wyłącznie po "
            f"{', '.join(ALLOWED_SCHEMES)}; plaintext tylko dla jawnie oznaczonego fixture "
            f"({FIXTURE_ORIGINS_VARIABLE})"
        )
    if is_fixture:
        # An origin the deployment named on purpose. The address check is skipped WITH the scheme
        # check, because a local fixture is on a loopback address by construction and refusing it
        # there would make the exception useless.
        return parts.hostname

    try:
        require_public_addresses(parts.hostname, resolver or _resolver)
    except EgressRejected as exc:
        raise FetchRefused(str(exc)) from exc
    except socket.gaierror as exc:
        raise FetchRefused(f"nie udało się rozwiązać nazwy {parts.hostname!r}: {exc}") from exc
    return parts.hostname


async def fetch_url(
    url: str,
    *,
    client=None,
    env: Mapping[str, str] | None = None,
    resolver=None,
    max_bytes: int = MAX_BYTES,
    timeout_s: float = TIMEOUT_S,
    max_redirects: int = MAX_REDIRECTS,
) -> Fetched:
    """One GET, with the checks above, a byte ceiling and a deadline.

    REDIRECTS ARE FOLLOWED BY HAND, and that is not a style choice. Measured on httpx 0.28.1:
    with `follow_redirects=True` the client sends the next request ITSELF, so a check made
    afterwards against `response.history` runs when the connection to the redirect target has
    already happened. `https://ok.example/x` redirecting to `http://169.254.169.254/` would be
    fetched and only then objected to, which is no defence at all. So the client is told NOT to
    follow, and every `Location` goes through `check_address` BEFORE the next request is sent.
    The test that caught this is
    `test_a_redirect_into_a_refused_address_does_not_follow_through`.

    Returns `Fetched(ok=False, reason=…)` for everything that went wrong AFTER the address was
    allowed — a 404, a timeout, a body that would not stop. Those are states of the source, and
    `knowledge_sources.status = 'UNAVAILABLE'` is where they land with their reason. A refusal
    BEFORE the request raises `FetchRefused`, because a URL nobody may fetch is not a source
    whose content is temporarily unknown; it is a URL this product will never read.
    """
    check_address(url, env=env, resolver=resolver)

    import httpx

    owned = client is None
    client = client or httpx.AsyncClient(
        timeout=timeout_s,
        follow_redirects=False,
        headers={"user-agent": "wenext-knowledge/0.1 (+kontakt w profilu)"},
    )

    def failed(reason: str, *, status: int | None = None) -> Fetched:
        return Fetched(
            ok=False,
            url=url,
            status=status,
            content=b"",
            content_type="",
            title="",
            text="",
            reason=reason,
            truncated=False,
        )

    try:
        current = url
        for _hop in range(max_redirects + 1):
            try:
                response = await client.get(current)
            except Exception as exc:  # every transport failure is the same state here
                return failed(f"{type(exc).__name__}: {str(exc)[:200]}")

            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "").strip()
                if not location:
                    return failed(
                        f"przekierowanie {response.status_code} bez adresu docelowego",
                        status=response.status_code,
                    )
                target = str(httpx.URL(current).join(location))
                try:
                    check_address(target, env=env, resolver=resolver)
                except FetchRefused as exc:
                    # Nothing was sent to the target. This is the refusal happening in time.
                    return failed(f"przekierowanie do {target}: {exc}", status=response.status_code)
                current = target
                continue

            if response.status_code >= 400:
                return failed(
                    f"serwer odpowiedział {response.status_code}", status=response.status_code
                )

            content = response.content
            truncated = len(content) > max_bytes
            if truncated:
                content = content[:max_bytes]
            content_type = response.headers.get("content-type", "")
            text, title = extract_text(content, content_type)
            return Fetched(
                ok=True,
                url=current,
                status=response.status_code,
                content=content,
                content_type=content_type,
                title=title,
                text=text,
                reason=None,
                truncated=truncated,
            )
        return failed(f"więcej niż {max_redirects} przekierowań pod adresem {url}")
    finally:
        if owned:
            await client.aclose()


def extract_text(content: bytes, content_type: str) -> tuple[str, str]:
    """Plain text and a title out of a fetched body. Returns `(text, title)`.

    A small extractor rather than a parser dependency, and the trade is deliberate: what this
    product needs from a page is enough text to search and to quote, with the RAW BYTES kept
    beside it in `artifacts` for anything better later. A source is therefore never reduced to
    what this function understood — the original is always still there.
    """
    charset = "utf-8"
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
    try:
        raw = content.decode(charset, errors="replace")
    except LookupError:
        raw = content.decode("utf-8", errors="replace")

    if "html" not in content_type.lower() and "<html" not in raw[:2000].lower():
        return _tidy(raw), ""

    title_match = _TITLE.search(raw)
    title = _tidy(html.unescape(_TAG.sub(" ", title_match.group(1)))) if title_match else ""
    body = _SCRIPT_OR_STYLE.sub(" ", raw)
    body = re.sub(r"<(br|/p|/div|/li|/h[1-6])\b[^>]*>", "\n", body, flags=re.IGNORECASE)
    body = _TAG.sub(" ", body)
    return _tidy(html.unescape(body)), title[:300]


def _tidy(text: str) -> str:
    text = _WHITESPACE.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()
