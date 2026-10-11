"""Anonymous public-page research, isolated from the capsule's social account.

Every HTTP request is fulfilled by a bounded HTTPS reader that connects to a validated,
pinned public address. Chrome never receives permission to send a page's requests directly.
Only GET is forwarded; cookies, credentials, service workers and WebSockets are absent.
This is visual research, not a general browser actuator or a way around platform blocks.
"""

from __future__ import annotations

import base64
import contextlib
import http.client
import ipaddress
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

MAX_RESPONSE_BYTES = 3_000_000
MAX_TOTAL_BYTES = 15_000_000
MAX_REQUESTS = 80
COMMENT_ID = re.compile(r"t1_[a-z0-9]{1,12}\Z")
COMMENT_PERMALINK = re.compile(
    r"/r/[A-Za-z0-9_]{2,21}/comments/[a-z0-9]{1,12}/comment/([a-z0-9]{1,12})/?\Z"
)
#: An X post's own address (27.09): its author and numeric id. Used for the post-only screenshot.
X_STATUS = re.compile(r"/[A-Za-z0-9_]{1,15}/status/([0-9]{1,25})/?\Z")
X_HOSTS = frozenset({"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com"})


def x_status_id(url: str) -> str | None:
    """The post id when `url` is one X post's page, `None` for anything else."""
    parsed = urlsplit(url)
    if (parsed.hostname or "").lower() not in X_HOSTS:
        return None
    match = X_STATUS.fullmatch(parsed.path)
    return match.group(1) if match else None


class ResearchRefused(Exception):
    pass


def public_target(url: str, *, resolver=socket.getaddrinfo) -> tuple[str, str, str]:
    """Return host, pinned IP and request path before any connection is made."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ResearchRefused("Nieprawidłowy adres strony.") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or len(url) > 4000
    ):
        raise ResearchRefused(
            "Przeglądarka badawcza czyta publiczne HTTPS bez logowania, na porcie 443."
        )
    host = parsed.hostname.encode("idna").decode("ascii")
    try:
        addresses = sorted({info[4][0] for info in resolver(host, 443, type=socket.SOCK_STREAM)})
        if not addresses or any(
            not ipaddress.ip_address(address).is_global for address in addresses
        ):
            raise ResearchRefused("Adres wskazuje sieć prywatną lub lokalną.")
    except (OSError, ValueError) as exc:
        raise ResearchRefused("Nie udało się ustalić publicznego adresu strony.") from exc
    return host, addresses[0], (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS still authenticates the original host; DNS is not repeated at connect time."""

    def __init__(self, host: str, address: str, *, timeout: float):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self) -> None:
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


@dataclass
class PublicResponse:
    url: str
    status: int
    content_type: str
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


class PublicReader:
    def __init__(self, *, timeout_s: float):
        self.deadline = time.monotonic() + timeout_s
        self.requests = 0
        self.bytes = 0

    def get(self, url: str) -> PublicResponse:
        for _ in range(4):
            self.requests += 1
            remaining = self.deadline - time.monotonic()
            if remaining <= 0 or self.requests > MAX_REQUESTS or self.bytes >= MAX_TOTAL_BYTES:
                raise ResearchRefused(
                    "Osiągnięto limit odczytu strony; część zasobów może być niedostępna."
                )
            host, address, path = public_target(url)
            connection = PinnedHTTPSConnection(host, address, timeout=min(remaining, 8))
            try:
                connection.request(
                    "GET",
                    path,
                    headers={
                        "Host": host,
                        "Accept-Encoding": "identity",
                        "User-Agent": "Mozilla/5.0 (compatible; WenextResearch/1.0)",
                    },
                )
                response = connection.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader("location")
                    if not location:
                        raise ResearchRefused("Przekierowanie bez adresu docelowego.")
                    url = urljoin(url, location)
                    continue
                cap = min(MAX_RESPONSE_BYTES, MAX_TOTAL_BYTES - self.bytes)
                chunks = []
                size = 0
                while size <= cap:
                    remaining = self.deadline - time.monotonic()
                    if remaining <= 0:
                        raise ResearchRefused("Przekroczono czas pobierania strony.")
                    if connection.sock is not None:
                        connection.sock.settimeout(min(remaining, 8))
                    chunk = response.read1(min(65536, cap + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                body = b"".join(chunks)
                self.bytes += len(body)
                if len(body) > cap:
                    raise ResearchRefused("Zasób strony przekracza limit rozmiaru.")
                return PublicResponse(
                    url,
                    response.status,
                    response.getheader("content-type", "application/octet-stream"),
                    body,
                    {
                        key: value
                        for key in (
                            "access-control-allow-origin",
                            "cross-origin-resource-policy",
                            "content-encoding",
                            "content-language",
                            "content-security-policy",
                            "referrer-policy",
                        )
                        if (value := response.getheader(key)) is not None
                    },
                )
            finally:
                connection.close()
        raise ResearchRefused("Zbyt wiele przekierowań strony.")


def inspect_public_page(
    fetcher,
    url: str,
    *,
    timeout_s: float = 25,
    javascript: bool = False,
    target_comment_id: str | None = None,
) -> dict:
    """Use a fresh context and close it on success, refusal and timeout alike."""
    if target_comment_id is not None:
        parsed = urlsplit(url)
        match = COMMENT_PERMALINK.fullmatch(parsed.path)
        if (
            parsed.hostname != "www.reddit.com"
            or parsed.query
            or not COMMENT_ID.fullmatch(target_comment_id)
            or match is None
            or match.group(1) != target_comment_id[3:]
        ):
            raise ResearchRefused("Sprawdzenie komentarza wymaga jego dokładnego permalinku.")
    public_target(url)
    reader = PublicReader(timeout_s=timeout_s)
    # Resolve redirects before navigation so relative assets and the reported URL agree.
    main = reader.get(url)
    if "html" not in main.content_type.lower() and not main.content_type.startswith("text/"):
        raise ResearchRefused(
            "Ten adres nie jest stroną tekstową. Dokument pobierz przez źródła projektu."
        )
    browser = fetcher.driver().chromium.connect_over_cdp(fetcher._endpoint(), timeout=5000)
    context = None
    blocked = 0
    errors = []
    try:
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            service_workers="block",
            accept_downloads=False,
            java_script_enabled=javascript,
        )
        context.route_web_socket("**/*", lambda route: route.close())
        initial_used = False

        def intercept(route) -> None:
            nonlocal blocked, initial_used
            request = route.request
            if request.method != "GET" or request.resource_type in ("media", "other"):
                blocked += 1
                route.abort()
                return
            try:
                if request.url == main.url and not initial_used:
                    initial_used = True
                    result = main
                else:
                    result = reader.get(request.url)
                # Do not forward Set-Cookie, Location or external connection hints. Redirects
                # of subresources are resolved by the public reader before fulfilling bytes.
                # Cross-origin styles with integrity checks need the real CORS headers;
                # dropping them produced a readable but unstyled GitHub screenshot.
                route.fulfill(
                    status=result.status,
                    content_type=result.content_type,
                    headers=result.headers,
                    body=result.body,
                )
            except (ResearchRefused, OSError, http.client.HTTPException) as exc:
                blocked += 1
                if len(errors) < 3:
                    errors.append(str(exc)[:160])
                route.abort()

        context.route("**/*", intercept)
        page = context.new_page()
        page.set_default_timeout(3000)
        partial = False
        try:
            page.goto(main.url, wait_until="load", timeout=int(timeout_s * 1000))
        except Exception:
            partial = True
        text = page.locator("body").inner_text(timeout=3000)
        target_comment = None
        if target_comment_id is not None:
            element = page.locator(f'shreddit-comment[thingid="{target_comment_id}"]')
            if element.count() == 1:
                target_comment = {
                    "external_id": target_comment_id,
                    "author": element.get_attribute("author"),
                    "score": element.get_attribute("score"),
                    "permalink": element.get_attribute("permalink"),
                    "text": element.inner_text(timeout=3000)[:10000],
                }
        links = []
        for link in page.get_by_role("link").all()[:40]:
            href = link.get_attribute("href") or ""
            if href:
                links.append({"label": link.inner_text()[:160], "url": urljoin(page.url, href)})
        image = page.screenshot(type="png", timeout=5000, animations="disabled")
        # THE POST ALONE, the way a screenshot of a post looks under a post of ours (27.09): the
        # article that links to this id. A reply's page shows its ancestors above it, and those
        # link to their own ids, so "the first article" alone would be somebody else's post. Two
        # markups: the logged-in one (`data-testid="tweet"` with a timestamp link) and the one X
        # serves to this anonymous browser (plain `article`, no `<time>`; measured 27.09 on a
        # live post). Best effort: no match, or a failed element shot, means no post image, and
        # the viewport shot above still stands.
        post_image = None
        status_id = x_status_id(page.url) or x_status_id(url)
        if status_id is not None:
            link = f'a[href$="/status/{status_id}"]'
            for selector in (
                f'article[data-testid="tweet"]:has({link} time)',
                f"article:has({link})",
            ):
                article = page.locator(selector)
                with contextlib.suppress(Exception):
                    if article.count() >= 1:
                        post_image = article.first.screenshot(
                            type="png", timeout=5000, animations="disabled"
                        )
                if post_image is not None:
                    break
        result = {
            "url": page.url,
            "requested_url": url,
            "title": page.title(),
            "status": main.status,
            "partial": partial or blocked > 0,
            "blocked_requests": blocked,
            "resource_requests": reader.requests,
            "limitations": errors,
            "viewer": "anonymous",
            "javascript": javascript,
            "viewport": {"width": 1280, "height": 900},
            "text": text[:40000],
            "truncated": len(text) > 40000,
            "links": links,
            "screenshot": base64.b64encode(image).decode("ascii"),
            "mime_type": "image/png",
        }
        if target_comment_id is not None:
            result["target_comment"] = target_comment
        if post_image is not None:
            result["post_screenshot"] = base64.b64encode(post_image).decode("ascii")
            result["post_id"] = status_id
        return result
    finally:
        if context is not None:
            with contextlib.suppress(Exception):
                context.close()
        browser.close()
