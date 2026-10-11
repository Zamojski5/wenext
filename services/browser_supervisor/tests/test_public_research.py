"""The research browser may see public pages, never account state or internal services."""

import base64
import socket
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from browser_supervisor.public_research import (
    PinnedHTTPSConnection,
    PublicReader,
    ResearchRefused,
    public_target,
)


def addresses(*values):
    return lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", (value, 443)) for value in values
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "file:///etc/passwd",
        "https://user:pass@example.com",
        "https://example.com:8443",
        "https://example.com:broken",
    ],
)
def test_non_public_browser_inputs_are_refused(url):
    with pytest.raises(ResearchRefused):
        public_target(url, resolver=addresses("93.184.216.34"))


@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "169.254.169.254", "10.0.0.4", "::1", "fc00::1", "::ffff:127.0.0.1"]
)
def test_every_dns_address_must_be_public(ip):
    with pytest.raises(ResearchRefused):
        public_target("https://example.com", resolver=addresses("93.184.216.34", ip))


def test_public_request_uses_validated_ip_but_authenticates_original_tls_host(monkeypatch):
    calls = []
    sock = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda address, timeout: calls.append((address, timeout)) or sock,
    )
    connection = PinnedHTTPSConnection("example.com", "93.184.216.34", timeout=2)
    connection._context = SimpleNamespace(
        wrap_socket=lambda raw, server_hostname: calls.append(server_hostname) or raw
    )
    connection.connect()
    assert calls == [(("93.184.216.34", 443), 2), "example.com"]


def test_redirect_target_is_rechecked_before_connecting(monkeypatch):
    import browser_supervisor.public_research as research

    checked, connections = [], []

    def target(url):
        checked.append(url)
        if "169.254" in url:
            raise ResearchRefused("private")
        return "example.com", "93.184.216.34", "/"

    class Connection:
        def __init__(self, *args, **kwargs):
            connections.append(args)

        def request(self, method, path, headers):
            assert method == "GET" and "Cookie" not in headers

        def getresponse(self):
            return SimpleNamespace(
                status=302, getheader=lambda name: "https://169.254.169.254/metadata"
            )

        def close(self):
            pass

    monkeypatch.setattr(research, "public_target", target)
    monkeypatch.setattr(research, "PinnedHTTPSConnection", Connection)
    with pytest.raises(ResearchRefused):
        PublicReader(timeout_s=3).get("https://example.com")
    assert len(connections) == 1
    assert checked[-1] == "https://169.254.169.254/metadata"


@pytest.mark.parametrize("body_fails", [False, True])
@pytest.mark.parametrize("javascript", [False, True])
def test_inspection_uses_fresh_context_blocks_writes_and_always_closes(
    monkeypatch, body_fails, javascript
):
    import browser_supervisor.public_research as research

    monkeypatch.setattr(
        research, "public_target", lambda url: ("example.com", "93.184.216.34", "/")
    )
    fetch = MagicMock(
        return_value=research.PublicResponse(
            "https://example.com/",
            200,
            "text/html",
            b"<body>Hello</body>",
            {"access-control-allow-origin": "*"},
        )
    )
    monkeypatch.setattr(research.PublicReader, "get", fetch)
    browser, context, page = MagicMock(), MagicMock(), MagicMock()
    browser.new_context.return_value = context
    context.new_page.return_value = page
    fetcher = MagicMock()
    fetcher.driver.return_value.chromium.connect_over_cdp.return_value = browser
    page.url = "https://example.com/"
    page.title.return_value = "Example"
    page.get_by_role.return_value.all.return_value = []
    page.screenshot.return_value = b"\x89PNG\r\n\x1a\nimage"
    page.locator.return_value.inner_text.return_value = "Hello"
    if body_fails:
        page.locator.return_value.inner_text.side_effect = RuntimeError("closed tab")

    post = MagicMock()
    post.request.method = "POST"
    document = MagicMock()
    document.request.method = "GET"
    document.request.resource_type = "document"
    document.request.url = "https://example.com/"

    def navigate(*_args, **_kwargs):
        intercept = context.route.call_args.args[1]
        intercept(post)
        intercept(document)

    page.goto.side_effect = navigate

    if body_fails:
        with pytest.raises(RuntimeError):
            research.inspect_public_page(fetcher, "https://example.com/", javascript=javascript)
    else:
        result = research.inspect_public_page(
            fetcher, "https://example.com/", javascript=javascript
        )
        assert result["viewer"] == "anonymous" and result["text"] == "Hello"
        assert result["blocked_requests"] == 1 and result["partial"]
        assert result["screenshot"].startswith("iVBOR")
    post.abort.assert_called_once()
    assert document.fulfill.call_args.kwargs["headers"] == {"access-control-allow-origin": "*"}
    assert fetch.call_count == 1
    assert browser.new_context.call_args.kwargs["service_workers"] == "block"
    assert browser.new_context.call_args.kwargs["accept_downloads"] is False
    assert browser.new_context.call_args.kwargs["java_script_enabled"] is javascript
    assert not browser.contexts.__getitem__.called
    context.close.assert_called_once()
    browser.close.assert_called_once()


def test_exact_reddit_comment_is_read_from_its_own_element(monkeypatch):
    import browser_supervisor.public_research as research

    url = "https://www.reddit.com/r/LLMDevs/comments/1woa28h/comment/pblmb3d/"
    monkeypatch.setattr(research, "public_target", lambda _: ("www.reddit.com", "1.1.1.1", "/"))
    monkeypatch.setattr(
        research.PublicReader,
        "get",
        lambda _self, _: research.PublicResponse(url, 200, "text/html", b"<body>ok</body>"),
    )
    browser, context, page = MagicMock(), MagicMock(), MagicMock()
    browser.new_context.return_value = context
    context.new_page.return_value = page
    page.url = url
    page.title.return_value = "Thread"
    page.get_by_role.return_value.all.return_value = []
    page.screenshot.return_value = b"\x89PNG\r\n\x1a\nimage"
    body, comment = MagicMock(), MagicMock()
    body.inner_text.return_value = "Comment removed by moderator"
    comment.count.return_value = 1
    comment.get_attribute.side_effect = {
        "author": "[deleted]",
        "score": "1",
        "permalink": "/r/LLMDevs/comments/1woa28h/comment/pblmb3d/",
    }.get
    comment.inner_text.return_value = "[deleted]\nComment removed by moderator"
    page.locator.side_effect = lambda selector: body if selector == "body" else comment
    fetcher = MagicMock()
    fetcher.driver.return_value.chromium.connect_over_cdp.return_value = browser

    result = research.inspect_public_page(
        fetcher, url, javascript=True, target_comment_id="t1_pblmb3d"
    )

    assert result["viewer"] == "anonymous"
    assert result["target_comment"] == {
        "external_id": "t1_pblmb3d",
        "author": "[deleted]",
        "score": "1",
        "permalink": "/r/LLMDevs/comments/1woa28h/comment/pblmb3d/",
        "text": "[deleted]\nComment removed by moderator",
    }
    context.close.assert_called_once()


def test_comment_inspection_rejects_a_mismatched_permalink_before_network(monkeypatch):
    import browser_supervisor.public_research as research

    monkeypatch.setattr(research, "public_target", lambda _: pytest.fail("should not resolve"))
    with pytest.raises(ResearchRefused):
        research.inspect_public_page(
            MagicMock(),
            "https://www.reddit.com/r/LLMDevs/comments/1woa28h/comment/other/",
            target_comment_id="t1_pblmb3d",
        )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://x.com/demo_user/status/2103939888511451337", "2103939888511451337"),
        ("https://twitter.com/demo_user/status/2103939888511451337/", "2103939888511451337"),
        ("https://x.com/demo_user", None),
        ("https://x.com/demo_user/status/2103939888511451337/analytics", None),
        ("https://example.com/demo_user/status/2103939888511451337", None),
    ],
)
def test_only_one_x_posts_own_page_names_a_post(url, expected):
    import browser_supervisor.public_research as research

    assert research.x_status_id(url) == expected


def test_an_x_post_page_also_gives_the_post_alone(monkeypatch):
    """27.09: a screenshot under an own post shows the post, not X's sidebar and replies. The
    element is the article whose own timestamp links to the id, never simply the first one."""
    import browser_supervisor.public_research as research

    url = "https://x.com/demo_user/status/2103939888511451337"
    monkeypatch.setattr(research, "public_target", lambda _: ("x.com", "1.1.1.1", "/"))
    monkeypatch.setattr(
        research.PublicReader,
        "get",
        lambda _self, _: research.PublicResponse(url, 200, "text/html", b"<body>ok</body>"),
    )
    browser, context, page = MagicMock(), MagicMock(), MagicMock()
    browser.new_context.return_value = context
    context.new_page.return_value = page
    page.url = url
    page.title.return_value = "Post"
    page.get_by_role.return_value.all.return_value = []
    page.screenshot.return_value = b"\x89PNG\r\n\x1a\nviewport"
    body, logged_in, anonymous = MagicMock(), MagicMock(), MagicMock()
    body.inner_text.return_value = "Reject non-understanding."
    # The anonymous markup X serves this browser (measured 27.09): no `data-testid="tweet"`, no
    # `<time>`, a plain article linking to the id.
    logged_in.count.return_value = 0
    anonymous.count.return_value = 1
    anonymous.first.screenshot.return_value = b"\x89PNG\r\n\x1a\npost"
    asked: list[str] = []

    def locate(selector):
        asked.append(selector)
        if selector == "body":
            return body
        return logged_in if 'data-testid="tweet"' in selector else anonymous

    page.locator.side_effect = locate
    fetcher = MagicMock()
    fetcher.driver.return_value.chromium.connect_over_cdp.return_value = browser

    result = research.inspect_public_page(fetcher, url, javascript=True)

    assert result["post_id"] == "2103939888511451337"
    assert base64.b64decode(result["post_screenshot"]) == b"\x89PNG\r\n\x1a\npost"
    assert base64.b64decode(result["screenshot"]) == b"\x89PNG\r\n\x1a\nviewport"
    assert asked[-2:] == [
        'article[data-testid="tweet"]:has(a[href$="/status/2103939888511451337"] time)',
        'article:has(a[href$="/status/2103939888511451337"])',
    ], "najpierw widok zalogowany, potem zwykły artykuł z linkiem do tego posta"

    anonymous.count.return_value = 0
    missing = research.inspect_public_page(fetcher, url, javascript=True)
    assert "post_screenshot" not in missing, "brak posta na stronie to brak obrazu posta"
    context.close.assert_called()
