"""The X read boundary: what may be opened, and what can honestly be read off the page.

WHY THIS EXISTS. X has no JSON entrance without paid keys, so it is read by rendering in the
capsule's Chrome, the SAME Chrome that holds the account's session. That raises the stakes of a
navigation: a Reddit thread read could break nothing, while here a visit to `/logout` would log
the account out and `/intent/post` would open a composer. The allowlist is a boundary of
behaviour, and these tests are its evidence.

The pure functions are tested directly. The glue (`read_x_page` over a page) runs on the DUMB fake
of `x_fake_browser.py`: literal selectors, no CSS, strict single-value reads. What these tests
cannot say is whether X's live DOM still matches the selectors; only `SEL_ARTICLE` and the
logged-out facts were measured on a live page (26.09, logged out).
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest
from browser_supervisor.x_reader import (
    CAPABILITY_EXPLORE,
    CAPABILITY_HOME,
    CAPABILITY_NOTIFICATIONS,
    CAPABILITY_POST,
    CAPABILITY_PROFILE,
    CAPABILITY_PROFILE_REPLIES,
    CAPABILITY_SEARCH,
    MAX_POSTS,
    SEL_FOLLOWERS_LINK,
    SEL_FOLLOWING_LINK,
    SEL_PROFILE_BIO,
    SEL_PROFILE_NAME,
    X_ORIGIN,
    XPlatformRefused,
    XReadRefused,
    assign_relations,
    author_name_from_user_name,
    authorize_x_url,
    choose_post_text,
    detect_unavailable,
    handle_from_profile_href,
    handle_from_switcher_text,
    is_account_locked,
    is_login_wall,
    leading_count,
    normalize_page_text,
    parse_count,
    parse_metrics,
    parse_status_path,
    post_from_fields,
    read_x_page,
)
from x_fake_browser import (
    Browser,
    Clock,
    Driver,
    Element,
    FakeTimeoutError,
    XPage,
    cell,
    post_article,
)

# -- what may be opened -----------------------------------------------------------------------------

ACCEPTED: list[tuple[str, str, str]] = [
    ("https://x.com/home", "https://x.com/home", CAPABILITY_HOME),
    ("https://x.com/explore", "https://x.com/explore", CAPABILITY_EXPLORE),
    ("https://x.com/notifications", "https://x.com/notifications", CAPABILITY_NOTIFICATIONS),
    ("https://x.com/demo_agent", "https://x.com/demo_agent", CAPABILITY_PROFILE),
    (
        "https://x.com/jack/with_replies",
        "https://x.com/jack/with_replies",
        CAPABILITY_PROFILE_REPLIES,
    ),
    (
        "https://x.com/jack/status/1790000000000000000",
        "https://x.com/jack/status/1790000000000000000",
        CAPABILITY_POST,
    ),
    (
        "https://x.com/search?q=agent+commerce",
        "https://x.com/search?q=agent+commerce",
        CAPABILITY_SEARCH,
    ),
    (
        "https://x.com/search?q=cart&f=live&src=typed_query&lang=en",
        "https://x.com/search?q=cart&f=live&src=typed_query&lang=en",
        CAPABILITY_SEARCH,
    ),
    # A trailing slash and the host's case do not make a second address.
    ("https://X.com/home/", "https://x.com/home", CAPABILITY_HOME),
]


@pytest.mark.parametrize(("url", "canonical", "capability"), ACCEPTED)
def test_an_allowed_address_comes_back_in_one_canonical_form(
    url: str, canonical: str, capability: str
) -> None:
    """Chrome receives exactly what was checked, not a differently parsed twin."""
    assert authorize_x_url(url) == (canonical, capability)


#: Each is refused for its own reason, so each is its own case. The first ones are ACTIONS, not
#: reads, which is the difference the Reddit allowlist never had to make.
REFUSED: list[str] = [
    "https://x.com/logout",
    "https://x.com/settings",
    "https://x.com/compose/post",
    "https://x.com/intent/post?text=anything",
    "https://x.com/i/flow/login",
    "https://x.com/messages",
    "https://x.com/bookmarks",
    # A reserved first segment is not a profile even in a post shape.
    "https://x.com/home/status/1790000000000000000",
    "https://x.com/i/status/1790000000000000000",
    # Another origin, including the one X itself redirects from.
    "https://twitter.com/jack",
    "https://mobile.x.com/home",
    # Origin spoofing: the hostname is `evil.example`, not `x.com`; an explicit port.
    "https://x.com@evil.example/home",
    "https://x.com:8443/home",
    "http://x.com/home",
    "https://x.com/jack#top",
    "https://x.com/",
    "https://x.com",
    # A search without a query is not a search; a parameter outside the list; a query anywhere
    # but the search.
    "https://x.com/search",
    "https://x.com/search?q=a&f=unknown",
    "https://x.com/search?q=a&foo=1",
    "https://x.com/home?foo=1",
    "https://x.com/jack?lang=en",
    # A handle longer than the platform allows; a post id that is not a number.
    "https://x.com/sixteencharacter",
    "https://x.com/jack/status/abc",
    "https://x.com/jack/status/1/photo/1",
    "",
]


@pytest.mark.parametrize("url", REFUSED)
def test_everything_else_is_refused_before_chrome_is_asked(url: str) -> None:
    with pytest.raises(XReadRefused):
        authorize_x_url(url)


def test_a_repeated_parameter_is_a_refusal_not_the_last_value() -> None:
    """Two values of one key are two different queries, depending on who parses them."""
    with pytest.raises(XReadRefused):
        authorize_x_url("https://x.com/search?q=a&q=b")


def test_a_search_keeps_its_query_text() -> None:
    canonical, capability = authorize_x_url("https://x.com/search?q=%22cart+sync%22&f=live")
    assert capability == CAPABILITY_SEARCH
    assert "cart" in canonical and "f=live" in canonical


# -- pure parsers -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("/jack/status/20", ("jack", "20")),
        ("https://x.com/jack/status/20", ("jack", "20")),
        ("/jack/status/20/", ("jack", "20")),
        ("/jack/status/20?s=46", ("jack", "20")),
        ("/jack/status/20/photo/1", None),
        ("/jack/status/20/analytics", None),
        ("/i/status/20", None),
        ("https://twitter.com/jack/status/20", None),
        ("https://evil.example/jack/status/20", None),
        ("/jack", None),
        (None, None),
        ("", None),
    ],
)
def test_only_an_exact_permalink_names_an_author_and_an_id(
    href: str | None, expected: tuple[str, str] | None
) -> None:
    assert parse_status_path(href) == expected


def test_author_and_id_come_from_the_permalink() -> None:
    """A display name changes and is not an identity; the permalink is."""
    post = post_from_fields(
        permalink_href="/demo_agent/status/1790000000000000001",
        text="body",
        text_source="post",
        published_at="2026-09-26T10:00:00.000Z",
    )
    assert post.author == "demo_agent"
    assert post.id == "1790000000000000001"
    assert post.permalink == f"{X_ORIGIN}/demo_agent/status/1790000000000000001"


def test_a_post_without_a_permalink_invents_no_author() -> None:
    """A missing read stays missing; a guessed author would enter memory as a fact."""
    post = post_from_fields(permalink_href=None, text="body", text_source="card")
    assert (post.author, post.id, post.permalink) == (None, None, None)
    assert post.to_json()["metrics"] == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", 0),
        ("12", 12),
        ("1,234", 1234),
        ("12,345,678", 12345678),
        ("1.2K", 1200),
        ("12.5K", 12500),
        ("3M", 3_000_000),
        ("1.5B", 1_500_000_000),
        ("4.5 M", 4_500_000),
        ("1.2", None),
        ("1,23", None),
        ("K", None),
        ("", None),
        (None, None),
    ],
)
def test_counts_are_read_in_every_spelling_x_uses_and_nothing_is_guessed(
    raw: str | None, expected: int | None
) -> None:
    assert parse_count(raw) == expected


def test_the_action_bar_label_becomes_integers() -> None:
    assert parse_metrics("12 replies, 3 reposts, 1,234 likes, 5 bookmarks, 6.7K views") == {
        "replies": 12,
        "reposts": 3,
        "likes": 1234,
        "bookmarks": 5,
        "views": 6700,
    }


def test_an_absent_counter_stays_absent_and_a_shown_zero_stays_zero() -> None:
    """Zero is a measurement, absence is the lack of one; the two must not merge."""
    metrics = parse_metrics("1 reply, 0 reposts, 7 views")
    assert metrics == {"replies": 1, "reposts": 0, "views": 7}
    assert "likes" not in metrics and "bookmarks" not in metrics
    assert parse_metrics("Like") == {}
    assert parse_metrics(None) == {}


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("12 Replies. Reply", {"replies": 12}),
        ("1.2K Likes. Like", {"likes": 1200}),
        ("34 reposts. Repost", {"reposts": 34}),
        ("6,789 views. View post analytics", {"views": 6789}),
    ],
)
def test_button_labels_are_read_the_same_way(label: str, expected: dict[str, int]) -> None:
    assert parse_metrics(label) == expected


def test_profile_counts_are_read_from_the_front_of_the_link_text() -> None:
    assert leading_count("1,234 Following") == 1234
    assert leading_count("4.5M Followers") == 4_500_000
    assert leading_count("Followers") is None


def test_relations_hang_on_the_focal_post() -> None:
    ids = ["1", "2", "focal", "4", "5", "6"]
    assert assign_relations(ids, "focal", conversation_end=5) == [
        "ancestor",
        "ancestor",
        "focal",
        "reply",
        "reply",
        None,
    ]


def test_without_the_focal_post_no_relation_is_claimed() -> None:
    assert assign_relations(["1", "2"], "focal") == [None, None]
    assert assign_relations([], "focal") == []


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("/demo_agent", "demo_agent"),
        ("https://x.com/jack", "jack"),
        ("/jack/status/1", None),
        ("/settings", None),
        ("/i", None),
        ("", None),
        (None, None),
        ("/sixteencharacter", None),
    ],
)
def test_the_account_handle_is_read_only_from_the_profile_link(
    href: str | None, expected: str | None
) -> None:
    """The identity has to be CHECKED, so guessing it is a failure."""
    assert handle_from_profile_href(href) == expected


def test_the_account_switcher_is_the_fallback_for_the_handle() -> None:
    assert handle_from_switcher_text("Demo Maintainer\n@Demo_Maint") == "Demo_Maint"
    assert handle_from_switcher_text("mail me: a@b.com") is None, "an e-mail is not a handle"
    assert handle_from_switcher_text("no handle here") is None
    assert handle_from_switcher_text(None) is None


@pytest.mark.parametrize(
    ("url", "wall"),
    [
        ("https://x.com/i/flow/login?redirect_after_login=%2Fhome", True),
        ("https://x.com/login", True),
        ("https://x.com/i/flow/signup", True),
        # MEASURED on 26.09 on the live capsule: where X sent `/home` without a session. The list
        # from before the measurement did not know this prefix and called a login wall "a session
        # whose handle was not read", the exact opposite of the truth.
        ("https://x.com/i/jf/onboarding/web?redirect_after_login=%2Fhome&mode=login", True),
        # The return parameter alone is enough: X has changed the path before, the parameter
        # survived the change.
        ("https://x.com/anything?mode=login", True),
        ("https://x.com/account/access", True),
        ("https://x.com/home", False),
        ("https://x.com/jack", False),
        ("https://x.com/loginhelp", False),
        ("https://x.com/search?q=login", False),
        # The query is parsed, not searched.
        ("https://x.com/search?q=mode%3Dlogin", False),
    ],
)
def test_a_login_wall_is_an_answer_about_the_session_not_a_failure(url: str, wall: bool) -> None:
    assert is_login_wall(url) is wall


def test_the_locked_account_screen_is_told_apart() -> None:
    assert is_account_locked("https://x.com/account/access")
    assert not is_account_locked("https://x.com/home")


def test_the_display_name_is_the_first_line_of_the_name_block() -> None:
    assert author_name_from_user_name("Jack\n@jack\n·\n3h") == "Jack"
    assert author_name_from_user_name("@jack") is None
    assert author_name_from_user_name(None) is None


def test_a_quoted_post_makes_a_single_body_text_ambiguous() -> None:
    """Two name blocks (the post and its quote) and ONE body text: nobody can tell whose text it
    is, so the whole card is served and labelled as such, instead of a quote under the wrong
    author."""
    assert choose_post_text(["main"], name_blocks=1, card_text="card") == ("main", "post")
    assert choose_post_text(["main", "quoted"], name_blocks=2, card_text="card") == (
        "main",
        "post",
    )
    assert choose_post_text(["quoted?"], name_blocks=2, card_text="card") == ("card", "card")
    assert choose_post_text([], name_blocks=0, card_text="card") == ("card", "card")
    assert choose_post_text([], name_blocks=0, card_text=None) == (None, None)


def test_page_text_is_normalised_for_typographic_apostrophes() -> None:
    """X writes a typographic apostrophe; a marker with a plain one would never match."""
    assert normalize_page_text("Hmm...this page doesn\u2019t   exist.") == (
        "hmm...this page doesn't exist."
    )


@pytest.mark.parametrize(
    ("body", "capability", "focal_found", "posts", "expected"),
    [
        (
            "Hmm...this page doesn\u2019t exist. Try searching",
            CAPABILITY_POST,
            False,
            [],
            "not_found",
        ),
        ("This account doesn't exist", CAPABILITY_PROFILE, False, [], "not_found"),
        ("Account suspended\nX suspends accounts", CAPABILITY_PROFILE, False, [], "suspended"),
        ("These posts are protected", CAPABILITY_PROFILE, False, [], "protected"),
        ("This post was deleted by the post author.", CAPABILITY_POST, False, [], "deleted"),
        ("Something went wrong. Try reloading.\nRetry", CAPABILITY_HOME, False, [], "error"),
        # A tombstone of a PARENT does not make the found focal post unavailable.
        ("This post was deleted by the post author.", CAPABILITY_POST, True, ["x"], None),
        # A marker inside somebody's post is their words, not the page's state.
        (
            "Home\nmy friend: account suspended again, unbelievable",
            CAPABILITY_PROFILE,
            False,
            ["my friend: account suspended again, unbelievable"],
            None,
        ),
        ("Home\nall good", CAPABILITY_HOME, False, ["all good"], None),
    ],
)
def test_what_the_page_says_instead_of_content(
    body: str, capability: str, focal_found: bool, posts: list[str], expected: str | None
) -> None:
    unavailable, _ = detect_unavailable(
        body, post_texts=posts, capability=capability, focal_found=focal_found
    )
    assert unavailable == expected


def test_an_error_panel_next_to_posts_is_a_limitation_not_unavailability() -> None:
    unavailable, limitations = detect_unavailable(
        "Home\nfirst post\nSomething went wrong. Try reloading.",
        post_texts=["first post"],
        capability=CAPABILITY_HOME,
        focal_found=False,
    )
    assert unavailable is None
    assert len(limitations) == 1 and "Something went wrong" in limitations[0]


# -- the glue: `read_x_page` over the dumb fake -----------------------------------------------------

FOCAL = "1790000000000000100"
POST_URL = f"https://x.com/jack/status/{FOCAL}"


def _read(page: XPage, url: str, *, anonymous: bool = False, fresh: list[XPage] | None = None):
    browser = Browser([page], fresh_pages=fresh)
    driver = Driver(browser)
    clock = Clock()
    result = read_x_page(
        url,
        driver=lambda: driver,
        endpoint=lambda: "http://127.0.0.1:9222",
        anonymous=anonymous,
        render_wait_s=3.0,
        clock=clock,
        sleep=clock.sleep,
    )
    return result, browser, driver


def _conversation_page() -> XPage:
    ancestor = post_article("alice", "1790000000000000090", "the question", name="Alice")
    focal = post_article(
        "jack",
        FOCAL,
        "the answer",
        name="Jack",
        published_at="2026-09-26T10:00:00.000Z",
        metrics_label="12 replies, 3 reposts, 1,234 likes, 5 bookmarks, 6.7K views",
    )
    reply_one = post_article("bob", "1790000000000000110", "agreed", metrics_label="1 like")
    reply_two = post_article("carol", "1790000000000000120", "why?")
    unrelated = post_article("dave", "1790000000000000999", "unrelated promo")
    posts = [ancestor, focal, reply_one, reply_two, unrelated]
    cells = [
        cell(ancestor),
        cell(focal),
        cell(reply_one),
        cell(reply_two),
        cell(heading="Discover more\nSourced from across X"),
        cell(unrelated),
    ]
    return XPage(posts, viewer="Demo_Maint", cells=cells)


def test_a_post_page_marks_focal_ancestors_and_replies_and_stops_at_discover_more() -> None:
    result, browser, _ = _read(_conversation_page(), POST_URL)

    assert result.capability == CAPABILITY_POST
    assert [post.relation for post in result.posts] == [
        "ancestor",
        "focal",
        "reply",
        "reply",
        None,
    ], "posts under 'Discover more' are not replies of this conversation"
    focal = result.posts[1]
    assert (focal.id, focal.author, focal.author_name) == (FOCAL, "jack", "Jack")
    assert focal.text == "the answer" and focal.text_source == "post"
    assert focal.published_at == "2026-09-26T10:00:00.000Z"
    assert focal.metrics == {
        "replies": 12,
        "reposts": 3,
        "likes": 1234,
        "bookmarks": 5,
        "views": 6700,
    }
    assert result.posts[2].metrics == {"likes": 1}, "a counter the bar does not name stays absent"
    assert result.viewer == "Demo_Maint" and result.logged_in is True
    assert result.login_wall is False and result.unavailable is None
    assert result.anonymous is False and result.profile is None
    assert any("Discover more" in one for one in result.limitations)
    assert browser.contexts[0].opened[0].closed, "the tab is closed after the read"
    assert browser.closed, "the CDP connection is released"


def test_the_json_carries_exactly_the_contract_fields() -> None:
    result, _, _ = _read(_conversation_page(), POST_URL)
    payload = result.to_json()
    assert list(payload) == [
        "requested_url",
        "final_url",
        "capability",
        "anonymous",
        "viewer",
        "login_wall",
        "logged_in",
        "unavailable",
        "posts",
        "profile",
        "text",
        "limitations",
    ]
    assert list(payload["posts"][0]) == [
        "id",
        "author",
        "author_name",
        "text",
        "text_source",
        "permalink",
        "published_at",
        "relation",
        "metrics",
    ]
    assert payload["posts"][1]["permalink"] == f"https://x.com/jack/status/{FOCAL}"


def test_the_logged_out_view_is_read_as_cards_and_says_so() -> None:
    """MEASURED on 26.09: logged out, `x.com/OpenAI` renders plain `article` elements without
    `tweetText`, `User-Name` or `time[datetime]`. Posts are still there for every human, so they
    must be there for the reader too, labelled as whole cards."""
    posts = [
        post_article("OpenAI", "1790000000000000301", "launch", logged_in=False),
        post_article("OpenAI", "1790000000000000302", "update", logged_in=False),
    ]
    page = XPage(posts)
    page.elements[SEL_PROFILE_NAME] = [Element("OpenAI\n@OpenAI")]
    page.elements[SEL_PROFILE_BIO] = [Element("We build things.")]
    page.elements[SEL_FOLLOWING_LINK] = [Element("12 Following")]
    page.elements[SEL_FOLLOWERS_LINK] = [Element("4.5M Followers")]

    result, _, _ = _read(page, "https://x.com/OpenAI")

    assert [post.text_source for post in result.posts] == ["card", "card"]
    assert [post.id for post in result.posts] == ["1790000000000000301", "1790000000000000302"]
    assert all(post.author == "OpenAI" for post in result.posts)
    assert all(post.published_at is None and post.metrics == {} for post in result.posts)
    assert result.viewer is None and result.logged_in is False and result.login_wall is False
    assert "Nie odczytano uchwytu konta z nawigacji." in result.limitations
    assert result.profile is not None
    assert result.profile.to_json() == {
        "handle": "OpenAI",
        "name": "OpenAI",
        "bio": "We build things.",
        "followers": 4_500_000,
        "following": 12,
    }


def test_a_login_wall_is_reported_as_such_with_no_posts() -> None:
    page = XPage(
        [post_article("x", "1")],
        redirect_to="https://x.com/i/jf/onboarding/web?redirect_after_login=%2Fhome&mode=login",
    )
    result, _, _ = _read(page, "https://x.com/home")
    assert result.login_wall is True and result.logged_in is False
    assert result.posts == () and result.viewer is None and result.unavailable is None
    assert result.final_url.startswith("https://x.com/i/jf/")


def test_an_anonymous_read_uses_a_fresh_context_and_never_the_profile() -> None:
    fresh_page = XPage([post_article("OpenAI", "1790000000000000301", "launch", logged_in=False)])
    profile_page = XPage([], viewer="Demo_Maint")
    result, browser, _ = _read(
        profile_page, "https://x.com/OpenAI", anonymous=True, fresh=[fresh_page]
    )
    assert result.anonymous is True and result.viewer is None and result.logged_in is False
    assert browser.new_contexts == 1
    assert browser.contexts[0].opened == [], "the profile context was not used at all"
    assert browser.fresh.closed, "the fresh context is destroyed after its one page"
    assert fresh_page.closed and len(result.posts) == 1
    assert "Nie odczytano uchwytu konta z nawigacji." not in result.limitations


def test_a_refused_navigation_keeps_the_platform_status() -> None:
    """The measured trap: 4xx with an empty body makes Chrome raise instead of returning."""
    page = XPage(
        captured_status=429, goto_error=RuntimeError("net::ERR_HTTP_RESPONSE_CODE_FAILURE")
    )
    with pytest.raises(XPlatformRefused) as refused:
        _read(page, "https://x.com/home")
    assert refused.value.status == 429
    assert page.closed


def test_a_rendered_refusal_page_is_a_refusal_not_an_empty_read() -> None:
    page = XPage([], status=403)
    with pytest.raises(XPlatformRefused) as refused:
        _read(page, "https://x.com/OpenAI")
    assert refused.value.status == 403


def test_a_navigation_timeout_is_told_apart_from_a_failure() -> None:
    from browser_supervisor.x_reader import XReadFailed, XReadTimedOut

    with pytest.raises(XReadTimedOut):
        _read(XPage(goto_error=FakeTimeoutError("goto timed out")), "https://x.com/home")
    with pytest.raises(XReadFailed):
        _read(XPage(goto_error=RuntimeError("net::ERR_NAME_NOT_RESOLVED")), "https://x.com/home")


def test_a_missing_focal_post_is_unavailable_with_the_pages_reason() -> None:
    page = XPage([], viewer="Demo_Maint", page_text="Hmm...this page doesn\u2019t exist.")
    result, _, _ = _read(page, POST_URL)
    assert result.unavailable == "not_found" and result.posts == ()


def test_a_404_without_any_marker_is_still_not_found() -> None:
    page = XPage([], viewer="Demo_Maint", status=404)
    result, _, _ = _read(page, POST_URL)
    assert result.unavailable == "not_found"
    assert "X odpowiedział statusem 404; stronę odczytano mimo to." in result.limitations


def test_an_empty_page_says_the_wait_ran_out() -> None:
    result, _, _ = _read(XPage([], viewer="Demo_Maint"), "https://x.com/search?q=nothing")
    assert result.posts == () and result.unavailable is None
    assert any("Nie pojawił się żaden post" in one for one in result.limitations)


def test_the_number_of_posts_is_bounded() -> None:
    posts = [post_article("jack", str(1790000000000000000 + n), f"p{n}") for n in range(45)]
    result, _, _ = _read(XPage(posts, viewer="Demo_Maint"), "https://x.com/jack")
    assert len(result.posts) == MAX_POSTS
    assert any(f"z 45 widocznych (limit {MAX_POSTS})" in one for one in result.limitations)


def test_the_reader_source_has_no_input_vocabulary() -> None:
    """Stricter than the repository-wide scanner, for this one file: no typing, no clicking, no
    script, and no scrolling or pointer input either. Reading is navigation plus `inner_text` and
    `get_attribute`, and the proof is that `goto` is the only way this file moves a page."""
    path = pathlib.Path(__file__).resolve().parents[1] / "browser_supervisor" / "x_reader.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden = {
        "post",
        "put",
        "patch",
        "delete",
        "click",
        "dblclick",
        "tap",
        "fill",
        "press",
        "press_sequentially",
        "type",
        "insert_text",
        "select_option",
        "set_input_files",
        "set_checked",
        "check",
        "uncheck",
        "submit",
        "evaluate",
        "evaluate_all",
        "evaluate_handle",
        "dispatch_event",
        "hover",
        "focus",
        "drag_to",
        "scroll_into_view_if_needed",
        "wheel",
    }
    writing_cdp = re.compile(r"^(Input\.[A-Za-z]+|Runtime\.(evaluate|callFunctionOn))$")
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            calls.append(node.func.attr)
            assert node.func.attr not in forbidden, (
                f"x_reader.py:{node.lineno} calls .{node.func.attr}(): that is input, not a read"
            )
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not writing_cdp.match(node.value), f"x_reader.py:{node.lineno} {node.value!r}"
    assert "goto" in calls and "inner_text" in calls and "get_attribute" in calls


# -- an X article (long-form post), measured logged in on 27.09 --------------------------------------

ARTICLE = "2102827031166189870"
EMBEDDED = "2102444357507350829"


def _article_page() -> XPage:
    """The shape measured on x.com/demo_author/status/2102827031166189870: the article's own
    timestamp link comes after an embedded post, whose author, time and `tweetText` come first."""
    from browser_supervisor.x_reader import (
        SEL_ACTION_BAR,
        SEL_ARTICLE_BODY,
        SEL_ARTICLE_TITLE,
        SEL_POST_TEXT,
        SEL_POST_USER_NAME,
        SEL_STATUS_LINK,
        SEL_TIME,
    )

    links = [
        f"/demo_author/status/{ARTICLE}/analytics",
        f"https://x.com/demo_embedded/status/{EMBEDDED}",
        f"/demo_embedded/status/{EMBEDDED}",
        f"/demo_author/status/{ARTICLE}",
    ]
    article = Element(
        "Demo Author\n@demo_author\nAdd agent tools to your store's search and cart this weekend",
        children={
            SEL_STATUS_LINK: [Element(attrs={"href": href}) for href in links],
            SEL_POST_USER_NAME: [
                Element("Demo Author\n@demo_author"),
                Element("Demo Embedded\n@demo_embedded\n·\nSep 22"),
            ],
            SEL_POST_TEXT: [Element("Hi internet! We went ahead and upgraded all checkout pages")],
            SEL_TIME: [
                Element(attrs={"datetime": "2026-09-22T17:06:11.000Z"}),
                Element(attrs={"datetime": "2026-09-23T18:26:47.000Z"}),
            ],
            SEL_ARTICLE_TITLE: [
                Element("Add agent tools to your store's search and cart this weekend")
            ],
            SEL_ARTICLE_BODY: [Element("An agent is shopping your store for a customer.")],
            SEL_ACTION_BAR: [
                Element(
                    attrs={"aria-label": "6 replies, 2 reposts, 4 likes, 1 bookmark, 1200 views"}
                )
            ],
        },
    )
    return XPage([article], viewer="demo_founder", cells=[cell(article)])


def test_an_x_article_is_the_focal_post_with_its_own_author_text_and_time() -> None:
    result, _, _ = _read(_article_page(), f"https://x.com/demo_author/status/{ARTICLE}")
    (post,) = result.posts
    assert (post.id, post.author, post.author_name) == (ARTICLE, "demo_author", "Demo Author")
    assert post.relation == "focal", "the article IS the post of the address"
    assert post.text_source == "article"
    assert post.text.startswith("Add agent tools") and "shopping your store" in post.text
    assert "Hi internet" not in post.text, "the embedded post's text is not the article's"
    # The first `time` is the embedded post's; the id says when the article was made.
    assert post.published_at is not None and post.published_at.startswith("2026-09-23T18:26:47")
    assert post.metrics["replies"] == 6
    assert not any("Nie znaleziono" in one for one in result.limitations)
    # Its replies are below the whole body; the read says so and names the query that reaches them.
    assert any(f"conversation_id:{ARTICLE}" in one for one in result.limitations)


def test_the_snowflake_epoch_is_the_writers() -> None:
    from browser_supervisor import x_reader, x_writer

    assert x_reader.SNOWFLAKE_EPOCH_MS == x_writer.SNOWFLAKE_EPOCH_MS
