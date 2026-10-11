"""`control_plane.knowledge.fetch` - what the fetcher refuses, and what it says while refusing.

THE SUBJECT IS THE REFUSALS. A fetcher that downloads a page is trivial; a fetcher that takes an
address from a form and does NOT reach the cloud metadata service, the container next door or the
database on localhost is the whole of this module. So the cases below are mostly addresses that
must not be visited, and each one names the thing it protects.

NO EXTERNAL NETWORK. Every case either refuses before sending anything, or talks to a local HTTP
server started by the test and named in `WENEXT_KNOWLEDGE_FIXTURE_ORIGINS`. That variable is the
only reason a plaintext loopback address is reachable at all, which is itself one of the cases.
"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from control_plane.knowledge.fetch import (
    FIXTURE_ORIGINS_VARIABLE,
    FetchRefused,
    check_address,
    extract_text,
    fetch_url,
    fixture_origins,
)

PAGE = b"""<!doctype html><html><head><title>Kolejki bez ack</title></head>
<body><script>var a = 1;</script><h1>Kolejki</h1><p>Jedna liczba: 12 tysiecy.</p></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    """Four paths: a page, a redirect to a refused address, a 404 and an endless body."""

    def log_message(self, *args):
        return

    def do_GET(self):
        if self.path == "/page":
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)
        elif self.path == "/redirect-to-metadata":
            self.send_response(302)
            self.send_header("location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
        elif self.path == "/redirect-to-page":
            self.send_response(302)
            self.send_header("location", "/page")
            self.end_headers()
        elif self.path == "/huge":
            body = b"x" * 50_000
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("content-length", "0")
            self.end_headers()


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def allow(server):
    return {FIXTURE_ORIGINS_VARIABLE: server}


def test_plaintext_is_refused_because_the_scheme_list_has_one_entry():
    with pytest.raises(FetchRefused) as refusal:
        check_address("http://example.com/artykul", env={})
    assert "https" in str(refusal.value)


@pytest.mark.parametrize(
    ("url", "what"),
    [
        ("https://metadata.test/latest/meta-data/", "169.254.169.254"),
        ("https://internal.test/admin", "10.0.0.5"),
        ("https://localhost.test/", "127.0.0.1"),
        ("https://cgnat.test/", "100.64.0.1"),
    ],
)
def test_an_address_that_is_not_publicly_routable_is_refused_whatever_the_name_says(url, what):
    """The NAME is public and the ANSWER is not. That is the whole shape of an SSRF.

    `169.254.169.254` is the one that costs real money: on a cloud instance it returns credentials
    to anybody who can make the server fetch a URL.
    """
    with pytest.raises(FetchRefused):
        check_address(url, env={}, resolver=lambda host: [what])


def test_one_bad_address_among_good_ones_is_still_a_refusal():
    """The connection picks which address to use and this check does not.

    A host answering [public, loopback] would be a coin flip, so `require_public_addresses`
    demands EVERY address be routable. This test is here so nobody "fixes" it into `any`.
    """
    with pytest.raises(FetchRefused):
        check_address(
            "https://mixed.test/", env={}, resolver=lambda host: ["93.184.216.34", "127.0.0.1"]
        )


def test_a_name_that_does_not_resolve_is_a_refusal_with_the_name_in_it():
    def explode(host):
        import socket

        raise socket.gaierror(-2, "Name or service not known")

    with pytest.raises(FetchRefused) as refusal:
        check_address("https://nie-ma-takiej-nazwy.test/", env={}, resolver=explode)
    assert "nie-ma-takiej-nazwy.test" in str(refusal.value)


def test_the_fixture_list_is_empty_unless_the_deployment_sets_it():
    """Production sets nothing, and then there is no exception to either rule."""
    assert fixture_origins({}) == frozenset()
    assert fixture_origins({FIXTURE_ORIGINS_VARIABLE: ""}) == frozenset()


def test_a_named_fixture_origin_is_allowed_over_plaintext_and_nothing_else_is(server, allow):
    assert check_address(f"{server}/page", env=allow) == "127.0.0.1"
    with pytest.raises(FetchRefused):
        check_address("http://127.0.0.1:9/page", env=allow)


def test_a_page_comes_back_with_its_bytes_its_hash_and_its_title(server, allow):
    fetched = asyncio.run(fetch_url(f"{server}/page", env=allow))
    assert fetched.ok and fetched.status == 200
    assert fetched.content == PAGE
    assert fetched.content_hash and fetched.content_hash.startswith("sha256:")
    assert fetched.title == "Kolejki bez ack"
    assert "12 tysiecy" in fetched.text
    # The script's source is not text of the page. A model reading it would be reading code it
    # was never shown, and a search would match on it.
    assert "var a = 1" not in fetched.text


def test_a_404_is_a_state_with_a_reason_and_never_an_empty_page(server, allow):
    fetched = asyncio.run(fetch_url(f"{server}/nie-ma", env=allow))
    assert not fetched.ok
    assert fetched.status == 404
    assert fetched.content == b"" and fetched.content_hash is None
    assert "404" in (fetched.reason or "")


def test_a_redirect_into_a_refused_address_does_not_follow_through(server, allow):
    """The check at the first hop is worth nothing if the third hop is not checked too."""
    fetched = asyncio.run(fetch_url(f"{server}/redirect-to-metadata", env=allow))
    assert not fetched.ok
    assert "przekierowanie" in (fetched.reason or "")
    assert fetched.content == b""


def test_an_ordinary_redirect_is_still_followed(server, allow):
    """Refusing every redirect would be safe and useless. An allowed hop is taken."""
    fetched = asyncio.run(fetch_url(f"{server}/redirect-to-page", env=allow))
    assert fetched.ok
    assert fetched.url.endswith("/page")
    assert fetched.title == "Kolejki bez ack"


def test_a_body_over_the_ceiling_is_cut_and_says_so(server, allow):
    fetched = asyncio.run(fetch_url(f"{server}/huge", env=allow, max_bytes=1000))
    assert fetched.ok and fetched.truncated
    assert len(fetched.content) == 1000


def test_a_refused_address_sends_nothing_at_all():
    """`fetch_url` raises BEFORE the request. A source whose address we may not read is not a
    source whose content is temporarily unknown."""
    with pytest.raises(FetchRefused):
        asyncio.run(fetch_url("http://169.254.169.254/latest/meta-data/", env={}))


def test_text_extraction_keeps_the_words_and_drops_the_markup():
    text, title = extract_text(PAGE, "text/html; charset=utf-8")
    assert title == "Kolejki bez ack"
    assert "Kolejki" in text and "<h1>" not in text


def test_a_plain_text_body_is_not_treated_as_markup():
    text, title = extract_text(b"liczba 5 < 7 i to wszystko", "text/plain")
    assert "5 < 7" in text
    assert title == ""
