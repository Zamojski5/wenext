"""The X actuator on a DUMB fake page: the glue between the checked parts, where this repository's
most expensive bugs lived (docs/KONWENCJE.md, "Klej między sprawdzonymi częściami").

The fake knows literal selectors only (`x_fake_browser.py`): no attribute matching, no case
folding. So these tests pass only for code that compares handles IN PYTHON and case-insensitively,
reads the evidence off the page, and decides refused / blocked / unknown by the one fact it owns:
whether the button was clicked.

What they cannot say: whether X's live composer takes `fill`, and whether the toast and the
selectors still look like this. The capsule's profile has no X session yet; the first live reply
is the measurement of that part.
"""

from __future__ import annotations

import pytest
from browser_supervisor.writer import WriteBlocked, WriteRefused, WriteUnknown
from browser_supervisor.x_reader import SEL_ACCOUNT_SWITCHER, SEL_NAV_PROFILE_LINK
from browser_supervisor.x_writer import (
    HOME_URL,
    MAX_POST_CHARS,
    URL_WEIGHT,
    XActuator,
    authorize_reply_target,
    check_text,
    composer_holds,
    posted_text_matches,
    snowflake_ms,
    x_weighted_length,
)
from x_fake_browser import (
    NOW_MS,
    Browser,
    Clock,
    Composer,
    Driver,
    Element,
    XPage,
    post_article,
    show_toast,
    snowflake_at,
)

ACCOUNT = "Demo_Maint"
TARGET_ID = snowflake_at(NOW_MS - 3_600_000)
TARGET_URL = f"https://x.com/jack/status/{TARGET_ID}"
TEXT = "A schema fixes argument hallucination, not authority."
FRESH_ID = snowflake_at(NOW_MS + 800)


def _actuator(driver: Driver, clock: Clock) -> XActuator:
    return XActuator(
        "http://127.0.0.1:9222",
        driver=lambda: driver,
        clock=clock,
        sleep=clock.sleep,
        now_ms=lambda: NOW_MS,
    )


def _reply_page(*, viewer: str | None = ACCOUNT, extra_posts=(), **kwargs) -> XPage:
    focal = post_article("jack", TARGET_ID, "the question")
    return XPage([focal, *extra_posts], viewer=viewer, **kwargs)


def _reply(page: XPage, *, account: str = ACCOUNT, text: str = TEXT, pages=None):
    browser = Browser(pages or [page])
    driver = Driver(browser)
    clock = Clock()
    outcome = _actuator(driver, clock).reply(
        post_url=TARGET_URL, post_id=TARGET_ID, text=text, account=account, timeout_s=45.0
    )
    return outcome, browser, driver, clock


def _typed_or_clicked(page: XPage) -> list[tuple[str, str]]:
    return [entry for entry in page.log if entry[0] in {"fill", "press", "type", "click"}]


def _our_reply_appears(page: XPage, post_id: str = FRESH_ID, text: str = TEXT):
    def on_submit(_state: str) -> None:
        page.posts.append(post_article(ACCOUNT, post_id, text))

    return on_submit


# -- identity: the hard condition -------------------------------------------------------------------


def test_another_account_in_the_session_blocks_before_anything_is_typed() -> None:
    page = _reply_page(viewer="SomebodyElse")
    Composer(page)
    with pytest.raises(WriteBlocked) as blocked:
        _reply(page)
    assert blocked.value.submit_started is False
    assert "@SomebodyElse" in str(blocked.value) and "kolejka stoi" in str(blocked.value)
    assert _typed_or_clicked(page) == []


def test_no_account_on_the_page_is_a_refusal() -> None:
    page = _reply_page(viewer=None)
    Composer(page)
    with pytest.raises(WriteRefused, match="strona nie pokazuje zalogowanego konta") as refused:
        _reply(page)
    assert refused.value.submit_started is False
    assert _typed_or_clicked(page) == []


def test_the_account_switcher_is_the_fallback_for_the_identity() -> None:
    page = _reply_page(viewer=None, switcher_text="Demo\n@Demo_Maint")
    Composer(page, on_submit=_our_reply_appears(page))
    outcome, *_ = _reply(page)
    assert outcome.post_id == FRESH_ID


def test_the_account_is_compared_case_insensitively_and_reported_as_the_consent_names_it() -> None:
    """X handles are case-insensitive. The page spells `Demo_Maint`; the consent may carry
    another case. A case-sensitive comparison would call our own session a stranger's (a block),
    and the evidence must keep the account as the consent names it."""
    page = _reply_page()
    Composer(page, on_submit=_our_reply_appears(page))
    outcome, *_ = _reply(page, account="@demo_maint")
    assert outcome.account == "@demo_maint"
    assert outcome.permalink == f"https://x.com/{ACCOUNT}/status/{FRESH_ID}", (
        "the permalink uses the handle as the PAGE spells it"
    )


def test_a_login_wall_is_a_refusal() -> None:
    page = _reply_page(
        viewer=None,
        redirect_to="https://x.com/i/flow/login?redirect_after_login=%2Fjack%2Fstatus%2F1",
    )
    with pytest.raises(WriteRefused, match="strona nie pokazuje zalogowanego konta"):
        _reply(page)


def test_a_locked_account_blocks_the_queue() -> None:
    page = _reply_page(viewer=None, redirect_to="https://x.com/account/access")
    with pytest.raises(WriteBlocked, match="blokady konta"):
        _reply(page)


# -- the text, refused before any input -------------------------------------------------------------


def test_a_text_over_the_limit_is_refused_before_the_browser_is_touched() -> None:
    page = _reply_page()
    Composer(page)
    browser = Browser([page])
    driver = Driver(browser)
    clock = Clock()
    with pytest.raises(WriteRefused, match="limit X to 280") as refused:
        _actuator(driver, clock).reply(
            post_url=TARGET_URL,
            post_id=TARGET_ID,
            text="a" * (MAX_POST_CHARS + 1),
            account=ACCOUNT,
            timeout_s=45.0,
        )
    assert refused.value.submit_started is False
    assert driver.connects == [], "nothing was asked of Chrome"


def test_a_short_link_counts_as_23_and_can_push_a_text_over_the_limit() -> None:
    """270 plain characters with `x.co` inside: 270 by `len`, 289 as X counts it. The composer
    would keep its button disabled; refusing here keeps that out of the 'unknown' bucket."""
    text = "a" * 262 + " x.co " + "b" * 2
    assert len(text) == 270
    assert x_weighted_length(text) == 264 + URL_WEIGHT + 2
    with pytest.raises(WriteRefused, match="zasadach X"):
        check_text(text)


@pytest.mark.parametrize(
    ("text", "weighted"),
    [
        ("a" * 280, 280),
        ("https://example.com/" + "p" * 200, URL_WEIGHT),
        ("see example.com now", 4 + URL_WEIGHT + 4),
        ("\u4f60\u597d", 4),
        ("e.g. 3.14 is fine", len("e.g. 3.14 is fine")),
    ],
)
def test_the_weighted_length_follows_x(text: str, weighted: int) -> None:
    assert x_weighted_length(text) == weighted


def test_an_empty_text_is_refused() -> None:
    with pytest.raises(WriteRefused, match="pusta"):
        check_text("   ")


# -- the click and what follows it ------------------------------------------------------------------


def test_a_click_without_evidence_is_unknown_and_says_the_button_was_clicked() -> None:
    page = _reply_page()
    composer = Composer(page)
    with pytest.raises(WriteUnknown, match="nie widać") as unknown:
        _reply(page)
    assert unknown.value.submit_started is True
    assert "nie publikuj ponownie" in str(unknown.value)
    assert composer.submitted == [TEXT], "exactly one click, with the consented text"
    assert [entry[0] for entry in _typed_or_clicked(page)] == ["fill", "click"]
    assert page.closed


def test_the_toasts_view_link_is_the_evidence() -> None:
    page = _reply_page()
    Composer(
        page,
        on_submit=lambda _state: show_toast(page, href=f"/{ACCOUNT}/status/{FRESH_ID}"),
    )
    outcome, browser, _, _ = _reply(page)
    assert outcome.post_id == FRESH_ID
    assert outcome.permalink == f"https://x.com/{ACCOUNT}/status/{FRESH_ID}"
    assert outcome.raw["source"] == "toast" and outcome.raw["entry"] == "fill"
    assert outcome.to_json()["submit_started"] is True
    assert page.closed and browser.closed


def test_a_toast_pointing_at_another_account_is_not_our_evidence() -> None:
    page = _reply_page()
    Composer(page, on_submit=lambda _state: show_toast(page, href=f"/jack/status/{FRESH_ID}"))
    with pytest.raises(WriteUnknown, match="nie widać"):
        _reply(page)


def test_a_new_post_of_our_account_on_the_page_is_the_evidence() -> None:
    page = _reply_page()
    Composer(page, on_submit=_our_reply_appears(page))
    outcome, *_ = _reply(page)
    assert outcome.post_id == FRESH_ID and outcome.raw["source"] == "dom"
    assert outcome.raw["minted_at"] is not None


def test_an_earlier_reply_of_ours_is_not_taken_for_the_new_one() -> None:
    earlier = post_article(ACCOUNT, snowflake_at(NOW_MS - 1_000), "said before")
    page = _reply_page(extra_posts=[earlier])
    Composer(page, on_submit=_our_reply_appears(page))
    outcome, *_ = _reply(page)
    assert outcome.post_id == FRESH_ID, "the evidence is the post that was not there before"


def test_an_old_post_of_ours_that_renders_only_after_the_click_is_not_the_new_one() -> None:
    """X loads a conversation in parts, so our reply from last week can appear after the click.
    It was not in the before-set, but its id says when it was minted."""
    page = _reply_page()
    week_ago = snowflake_at(NOW_MS - 7 * 86_400_000)
    Composer(page, on_submit=_our_reply_appears(page, post_id=week_ago))
    with pytest.raises(WriteUnknown, match="nie widać"):
        _reply(page)


def test_a_failure_during_the_click_is_unknown_because_the_flag_was_set_before_it() -> None:
    page = _reply_page()

    def cdp_drops(_state: str) -> None:
        raise RuntimeError("Target page, context or browser has been closed")

    Composer(page, on_submit=cdp_drops)
    with pytest.raises(WriteUnknown) as unknown:
        _reply(page)
    assert unknown.value.submit_started is True


@pytest.mark.parametrize(
    ("toast", "outcome_class"),
    [
        ("You are over the daily limit for sending posts.", WriteBlocked),
        ("Something went wrong, but don't fret. Let's give it another shot.", WriteUnknown),
    ],
)
def test_an_error_toast_after_the_click_ends_the_wait(toast: str, outcome_class: type) -> None:
    page = _reply_page()
    Composer(page, on_submit=lambda _state: show_toast(page, href=None, text=toast))
    with pytest.raises(outcome_class) as raised:
        _reply(page)
    assert raised.value.submit_started is True
    assert toast[:20] in str(raised.value)


# -- the composer -----------------------------------------------------------------------------------


def test_a_fill_the_editor_did_not_register_falls_back_to_the_keyboard() -> None:
    """DraftJS can hold the text in the DOM while its own state stays empty: the button stays
    disabled. The documented fallback clears with the keyboard and types key by key."""
    page = _reply_page()
    composer = Composer(page, fill_registers=False, on_submit=_our_reply_appears(page))
    outcome, *_ = _reply(page)
    assert [entry[0] for entry in _typed_or_clicked(page)] == [
        "fill",
        "press",
        "press",
        "type",
        "click",
    ]
    assert outcome.raw["entry"] == "keyboard"
    assert composer.submitted == [TEXT]


def test_a_composer_that_takes_no_text_is_refused_and_never_clicked() -> None:
    page = _reply_page()
    Composer(page, fill_registers=False, typing_registers=False)
    with pytest.raises(WriteRefused, match="nic nie wysłano") as refused:
        _reply(page)
    assert refused.value.submit_started is False
    assert ("click", '[data-testid="tweetButtonInline"]') not in page.log


def test_a_typeahead_that_rewrote_the_text_is_refused_before_the_click() -> None:
    page = _reply_page()
    Composer(
        page,
        fill_registers=False,
        typed_as=lambda text: text.replace("schema", "@schemaorg"),
    )
    with pytest.raises(WriteRefused):
        _reply(page)
    assert not [entry for entry in page.log if entry[0] == "click"]


def test_a_button_that_never_turns_live_is_a_refusal_not_a_click() -> None:
    page = _reply_page()
    Composer(page, button_never_enables=True)
    with pytest.raises(WriteRefused) as refused:
        _reply(page)
    assert refused.value.submit_started is False
    assert not [entry for entry in page.log if entry[0] == "click"]


def test_a_page_without_a_composer_is_a_refusal() -> None:
    page = _reply_page()
    with pytest.raises(WriteRefused, match="brak pola tekstu"):
        _reply(page)


# -- the page before the click ----------------------------------------------------------------------


def test_a_post_that_is_not_on_the_page_is_refused_with_the_pages_reason() -> None:
    page = XPage([], viewer=ACCOUNT, page_text="Hmm...this page doesn\u2019t exist.")
    Composer(page)
    with pytest.raises(WriteRefused, match="not_found"):
        _reply(page)
    assert _typed_or_clicked(page) == []


def test_an_account_block_on_the_page_stops_the_queue() -> None:
    page = _reply_page(page_text="Your account is suspended and is not permitted to post.")
    Composer(page)
    with pytest.raises(WriteBlocked, match="your account is suspended"):
        _reply(page)
    assert _typed_or_clicked(page) == []


def test_the_same_words_inside_somebodys_post_do_not_block() -> None:
    complaint = post_article("bob", snowflake_at(NOW_MS - 5_000), "lol your account is suspended")
    page = _reply_page(extra_posts=[complaint])
    Composer(page, on_submit=_our_reply_appears(page))
    outcome, *_ = _reply(page)
    assert outcome.post_id == FRESH_ID


# -- own post ---------------------------------------------------------------------------------------


def _home_page() -> XPage:
    older = post_article("someone", snowflake_at(NOW_MS - 60_000), "timeline")
    return XPage([older], viewer=ACCOUNT)


def _own_post(pages: list[XPage], *, text: str = TEXT):
    browser = Browser(pages)
    driver = Driver(browser)
    clock = Clock()
    outcome = _actuator(driver, clock).own_post(text=text, account=ACCOUNT, timeout_s=45.0)
    return outcome, browser


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_an_own_post_with_an_image_attaches_it_before_the_one_click() -> None:
    """27.09: a news post with a screenshot. The image goes in after the text, its preview must
    show, and only then the button is clicked, once, with the image already attached."""
    home = _home_page()
    composer = Composer(
        home,
        accepts_images=True,
        on_submit=lambda _state: show_toast(home, href=f"/{ACCOUNT}/status/{FRESH_ID}"),
    )
    browser = Browser([home])
    outcome = _actuator(Driver(browser), Clock()).own_post(
        text=TEXT, account=ACCOUNT, timeout_s=45.0, image=PNG, image_type="image/png"
    )
    assert outcome.post_id == FRESH_ID
    assert composer.submitted == [TEXT] and composer.submitted_with_images == [1]
    [files] = composer.images
    assert files["buffer"] == PNG and files["mimeType"] == "image/png"
    assert outcome.raw["image_bytes"] == len(PNG)


def test_an_image_x_does_not_take_is_a_refusal_and_nothing_goes_out() -> None:
    home = _home_page()
    composer = Composer(home, accepts_images=True, image_previews=False)
    with pytest.raises(WriteRefused, match="obraz"):
        _actuator(Driver(Browser([home])), Clock()).own_post(
            text=TEXT, account=ACCOUNT, timeout_s=45.0, image=PNG, image_type="image/png"
        )
    assert composer.submitted == [], "bez obrazu tekst nie wychodzi sam"


def test_text_lost_while_the_image_uploads_is_a_refusal_before_the_click() -> None:
    """The consent is the text and the image together: a composer that shows the image but lost
    the text would post an image without the approved words, so it is refused before the click."""
    home = _home_page()
    composer = Composer(home, accepts_images=True, image_clears_text=True)
    with pytest.raises(WriteRefused, match="po wgraniu obrazu"):
        _actuator(Driver(Browser([home])), Clock()).own_post(
            text=TEXT, account=ACCOUNT, timeout_s=45.0, image=PNG, image_type="image/png"
        )
    assert composer.submitted == [], "obraz bez zatwierdzonego tekstu nie wychodzi"


@pytest.mark.parametrize(
    ("image", "image_type"),
    [(b"GIF89a" + b"\x00" * 8, "image/gif"), (b"not a png", "image/png"), (b"", "image/png")],
)
def test_a_wrong_image_is_refused_before_the_browser(image: bytes, image_type: str) -> None:
    home = _home_page()
    composer = Composer(home, accepts_images=True)
    browser = Browser([home])
    with pytest.raises(WriteRefused):
        _actuator(Driver(browser), Clock()).own_post(
            text=TEXT, account=ACCOUNT, timeout_s=45.0, image=image, image_type=image_type
        )
    assert composer.submitted == [] and home.goto_calls == []


def test_an_own_post_is_proven_by_the_toast() -> None:
    home = _home_page()
    Composer(home, on_submit=lambda _state: show_toast(home, href=f"/{ACCOUNT}/status/{FRESH_ID}"))
    outcome, _ = _own_post([home])
    assert outcome.post_id == FRESH_ID and outcome.raw["source"] == "toast"
    assert home.goto_calls == [HOME_URL]


def test_an_own_post_falls_back_to_reading_our_profile_in_a_second_tab() -> None:
    """No toast and no post on the home timeline: the profile is read in a NEW tab, and the
    evidence is the newest post of ours with our text, minted after the click. The compose tab is
    never navigated away (that could cancel the very request we are trying to learn about)."""
    home = _home_page()
    Composer(home)
    profile = XPage(
        [
            post_article(ACCOUNT, FRESH_ID, TEXT),
            post_article(ACCOUNT, snowflake_at(NOW_MS - 86_400_000), "yesterday"),
        ],
        viewer=ACCOUNT,
    )
    outcome, browser = _own_post([home, profile])
    assert outcome.post_id == FRESH_ID and outcome.raw["source"] == "profile"
    assert home.goto_calls == [HOME_URL], "the compose tab stayed where it was"
    assert profile.goto_calls == [f"https://x.com/{ACCOUNT}"]
    assert home.closed and profile.closed and browser.closed


def test_the_profile_fallback_does_not_take_an_older_post_with_the_same_text() -> None:
    """X refuses an exact duplicate; an older post with our text is then the ONLY one on the
    profile, and taking it would report a publication that did not happen."""
    home = _home_page()
    Composer(home)
    profile = XPage(
        [post_article(ACCOUNT, snowflake_at(NOW_MS - 3 * 86_400_000), TEXT)], viewer=ACCOUNT
    )
    with pytest.raises(WriteUnknown, match="nie widać") as unknown:
        _own_post([home, profile])
    assert unknown.value.submit_started is True
    assert "nie publikuj ponownie" in str(unknown.value)


def test_an_own_post_on_the_home_timeline_must_carry_our_text() -> None:
    home = _home_page()

    def another_post_appears(_state: str) -> None:
        home.posts.insert(0, post_article(ACCOUNT, FRESH_ID, "something else entirely"))

    Composer(home, on_submit=another_post_appears)
    profile = XPage([], viewer=ACCOUNT)
    with pytest.raises(WriteUnknown, match="nie widać"):
        _own_post([home, profile])


# -- pure helpers and wiring ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "post_id"),
    [
        (f"https://twitter.com/jack/status/{TARGET_ID}", TARGET_ID),
        (f"https://mobile.x.com/jack/status/{TARGET_ID}", TARGET_ID),
        (f"http://x.com/jack/status/{TARGET_ID}", TARGET_ID),
        (f"https://x.com/jack/status/{TARGET_ID}?s=20", TARGET_ID),
        (f"https://x.com/jack/status/{TARGET_ID}#reply", TARGET_ID),
        (f"https://x.com/jack/status/{TARGET_ID}/photo/1", TARGET_ID),
        (f"https://x.com/i/status/{TARGET_ID}", TARGET_ID),
        (f"https://x.com@evil.example/jack/status/{TARGET_ID}", TARGET_ID),
        (f"https://x.com/jack/status/{TARGET_ID}", "123"),
        (f"https://x.com/jack/status/{TARGET_ID}", "abc"),
        ("", TARGET_ID),
    ],
)
def test_only_the_exact_address_of_the_consented_post_is_accepted(url: str, post_id: str) -> None:
    with pytest.raises(WriteRefused):
        authorize_reply_target(url, post_id)


def test_the_reply_target_is_canonical() -> None:
    assert authorize_reply_target(TARGET_URL + "/", TARGET_ID) == TARGET_URL


def test_snowflake_time_and_text_comparison() -> None:
    assert snowflake_ms(snowflake_at(NOW_MS)) == NOW_MS
    assert snowflake_ms("12") is None and snowflake_ms("not a number") is None
    assert composer_holds("Hello  world \U0001f600", "Hello world \U0001f600")
    assert composer_holds("Hello world", "Hello world \U0001f600"), "X draws emoji as images"
    assert not composer_holds("Hello @worldwide", "Hello world")
    assert not composer_holds("", "Hello")
    assert posted_text_matches(
        "read example.com/a/very/lo\u2026 now", "read https://example.com/a/very/long/path now"
    )
    assert not posted_text_matches("read now", "read https://example.com/a now please")


def test_the_actuator_borrows_the_shared_driver_and_never_starts_its_own() -> None:
    """The first live smoke on Reddit failed because an actuator started a SECOND Playwright
    driver on the reader's thread. With a shared driver injected, only that one is used."""
    calls: list[int] = []
    refused = Driver(Browser([]), refuse=RuntimeError("fake CDP refused"))

    def shared() -> Driver:
        calls.append(1)
        return refused

    actuator = XActuator("http://127.0.0.1:1", driver=shared)
    with pytest.raises(WriteRefused, match="brak połączenia CDP"):
        actuator.own_post(text="x", account=ACCOUNT, timeout_s=1.0)
    assert calls == [1]
    assert actuator._playwright is None


def test_the_selectors_the_fake_answers_to_are_the_readers_constants() -> None:
    """The actuator and the reader share ONE selector block; the fake refuses anything else, so a
    selector typed inline in `x_writer.py` would fail every test above."""
    page = XPage([], viewer=ACCOUNT)
    with pytest.raises(AssertionError, match="SEL_"):
        page.locator('a[data-testid="AppTabBar_Profile_Link"][href="/Demo_Maint"]')
    assert page.locator(SEL_NAV_PROFILE_LINK).count() == 1
    page.elements[SEL_ACCOUNT_SWITCHER] = [Element("x")]
    assert page.locator(SEL_ACCOUNT_SWITCHER).count() == 1


def test_the_actuator_finds_an_x_article_by_its_authors_link_not_the_embedded_post() -> None:
    """27.09: the first X reply was refused with "the post is not on the page" (nothing was sent):
    every `article` on an X article page led with the embedded post's status link."""
    from browser_supervisor.x_writer import _scan_posts
    from x_fake_browser import x_article

    article = x_article(
        "demo_author",
        "2102827031166189870",
        title="Add agent tools to your store's search and cart this weekend",
        body="An agent is shopping your store for a customer.",
        embedded_handle="demo_embedded",
        embedded_id="2102444357507350829",
        embedded_text="Hi internet! We went ahead and upgraded all checkout pages",
    )
    page = XPage([article], viewer="demo_founder")
    seen = _scan_posts(page)
    assert [(one.handle, one.post_id) for one in seen] == [("demo_author", "2102827031166189870")]
