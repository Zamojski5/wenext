"""A DUMB stand-in for the part of Playwright that the X reader and the X actuator touch.

DUMB ON PURPOSE (docs/KONWENCJE.md, "Klej między sprawdzonymi częściami"): a selector is a KEY,
never parsed. The fake knows which elements answer to which literal selector string and nothing
about CSS: no attribute matching, no case folding, no `:has()`. A fake that understood selectors
would let through code that puts a handle into a selector, the exact bug of the 21.09 Reddit
evidence read. Instead it asserts that every selector the code asks for is one of the `SEL_*`
constants of `x_reader.py`, so the "one block of selectors" rule is measured as well.

Three real behaviours are mimicked because the code depends on them: locators are STRICT (a
single-value read on several matches fails, as in Playwright's strict mode), a read on a locator
with no match fails after its timeout (here at once), and a click on a disabled element does not
happen.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from browser_supervisor import x_reader
from browser_supervisor.x_reader import (
    SEL_ACCOUNT_SWITCHER,
    SEL_ACTION_BAR,
    SEL_ARTICLE,
    SEL_ARTICLE_BODY,
    SEL_ARTICLE_TITLE,
    SEL_ATTACHMENTS,
    SEL_BODY,
    SEL_CELL,
    SEL_COMPOSER_TEXTBOX,
    SEL_FILE_INPUT,
    SEL_NAV_PROFILE_LINK,
    SEL_POST_TEXT,
    SEL_POST_USER_NAME,
    SEL_STATUS_LINK,
    SEL_SUBMIT_INLINE,
    SEL_TIME,
    SEL_TOAST,
    SEL_TOAST_STATUS_LINK,
)
from browser_supervisor.x_writer import SNOWFLAKE_EPOCH_MS

KNOWN_SELECTORS = frozenset(
    value for name, value in vars(x_reader).items() if name.startswith("SEL_")
)


class FakeTimeoutError(Exception):
    """Named like Playwright's `TimeoutError`: the reader tells a timeout apart by the class name."""


def _not_editable(_value: str) -> None:
    raise RuntimeError("Error: Element is not an <input>, <textarea> or [contenteditable] element")


def _no_keys(_key: str) -> None:
    raise RuntimeError("Error: this fake element takes no keyboard input")


def _no_click() -> None:
    raise AssertionError("the code clicked an element the test did not make clickable")


def _not_a_file_input(_files: Any) -> None:
    raise AssertionError("the code gave files to an element the test did not make a file input")


class Element:
    """Text, attributes, and children under LITERAL selector keys. Nothing else."""

    def __init__(
        self,
        text: str = "",
        *,
        attrs: dict[str, str] | None = None,
        children: dict[str, list[Element]] | None = None,
        enabled: bool = True,
        visible: bool = True,
    ) -> None:
        self.text = text
        self.attrs = dict(attrs or {})
        self.children: dict[str, list[Element]] = {k: list(v) for k, v in (children or {}).items()}
        self.enabled = enabled
        self.visible = visible
        self.on_fill: Callable[[str], None] = _not_editable
        self.on_press: Callable[[str], None] = _no_keys
        self.on_type: Callable[[str], None] = _not_editable
        self.on_click: Callable[[], None] = _no_click
        self.on_files: Callable[[Any], None] = _not_a_file_input


class Locator:
    def __init__(self, page: Page, selector: str, resolve: Callable[[], list[Element]]) -> None:
        self._page = page
        self._selector = selector
        self._resolve = resolve

    @property
    def first(self) -> Locator:
        return Locator(self._page, self._selector, lambda: self._resolve()[:1])

    def nth(self, index: int) -> Locator:
        return Locator(self._page, self._selector, lambda: self._resolve()[index : index + 1])

    def count(self) -> int:
        return len(self._resolve())

    def _single(self, action: str) -> Element:
        found = self._resolve()
        if not found:
            raise FakeTimeoutError(f"{action}: nothing matches {self._selector!r}")
        if len(found) > 1:
            raise AssertionError(
                f"strict mode violation: {self._selector!r} matched {len(found)} elements in "
                f"{action}; Playwright would refuse this read"
            )
        return found[0]

    def get_attribute(self, name: str, timeout: float | None = None) -> str | None:
        return self._single("get_attribute").attrs.get(name)

    def inner_text(self, timeout: float | None = None) -> str:
        return self._single("inner_text").text

    def all_inner_texts(self) -> list[str]:
        return [one.text for one in self._resolve()]

    def wait_for(self, state: str = "visible", timeout: float | None = None) -> None:
        found = self._resolve()
        if not found or (state == "visible" and not found[0].visible):
            raise FakeTimeoutError(f"wait_for({state}): {self._selector!r} did not appear")

    def is_enabled(self, timeout: float | None = None) -> bool:
        return self._single("is_enabled").enabled

    def is_visible(self, timeout: float | None = None) -> bool:
        found = self._resolve()
        return bool(found) and found[0].visible

    def locator(self, selector: str) -> Locator:
        self._page.check(selector)
        return Locator(
            self._page,
            selector,
            lambda: [child for one in self._resolve() for child in one.children.get(selector, [])],
        )

    def fill(self, value: str, timeout: float | None = None) -> None:
        element = self._single("fill")
        self._page.log.append(("fill", value))
        element.on_fill(value)

    def press(self, key: str, timeout: float | None = None) -> None:
        element = self._single("press")
        self._page.log.append(("press", key))
        element.on_press(key)

    def press_sequentially(
        self, text: str, delay: float | None = None, timeout: float | None = None
    ) -> None:
        element = self._single("press_sequentially")
        self._page.log.append(("type", text))
        element.on_type(text)

    def set_input_files(self, files: Any, timeout: float | None = None) -> None:
        element = self._single("set_input_files")
        self._page.log.append(("files", self._selector))
        element.on_files(files)

    def click(self, timeout: float | None = None) -> None:
        element = self._single("click")
        if not element.enabled:
            raise FakeTimeoutError(f"click: {self._selector!r} is not enabled")
        self._page.log.append(("click", self._selector))
        element.on_click()


class Response:
    def __init__(self, url: str, status: int, frame: object) -> None:
        self.url = url
        self.status = status
        self.frame = frame
        self.headers: dict[str, str] = {"content-type": "text/html"}


class Page:
    """One tab. `elements` maps a literal selector to a list, or to a callable returning one when
    the page changes over time (a toast after the click, a post that appears)."""

    def __init__(
        self,
        *,
        elements: dict[str, Any] | None = None,
        redirect_to: str | None = None,
        status: int = 200,
        goto_error: Exception | None = None,
        captured_status: int | None = None,
    ) -> None:
        self.url = "about:blank"
        self.main_frame = object()
        self.elements: dict[str, Any] = dict(elements or {})
        self.redirect_to = redirect_to
        self.status = status
        self.goto_error = goto_error
        self.captured_status = captured_status
        self.log: list[tuple[str, str]] = []
        self.goto_calls: list[str] = []
        self.closed = False
        self.brought_to_front = False
        self._listeners: list[Callable[[Any], None]] = []

    def check(self, selector: str) -> None:
        if selector not in KNOWN_SELECTORS:
            raise AssertionError(
                f"the code asked for {selector!r}, which is not one of the SEL_* constants of "
                "x_reader.py: every selector lives in that one block"
            )

    def locator(self, selector: str) -> Locator:
        self.check(selector)
        return Locator(self, selector, lambda: self._elements_for(selector))

    def _elements_for(self, selector: str) -> list[Element]:
        value = self.elements.get(selector, [])
        return list(value() if callable(value) else value)

    def on(self, event: str, callback: Callable[[Any], None]) -> None:
        if event == "response":
            self._listeners.append(callback)

    def goto(self, url: str, wait_until: str | None = None, timeout: float | None = None) -> Any:
        self.goto_calls.append(url)
        self.log.append(("goto", url))
        if self.captured_status is not None:
            for callback in self._listeners:
                callback(Response(url, self.captured_status, self.main_frame))
        if self.goto_error is not None:
            raise self.goto_error
        self.url = self.redirect_to or url
        response = Response(url, self.status, self.main_frame)
        for callback in self._listeners:
            callback(response)
        return response

    def close(self) -> None:
        self.closed = True

    def bring_to_front(self) -> None:
        self.brought_to_front = True


class Context:
    def __init__(self, pages: list[Page]) -> None:
        self._pages = list(pages)
        self.opened: list[Page] = []
        self.closed = False

    def new_page(self) -> Page:
        if not self._pages:
            raise AssertionError("the code opened more tabs than the test prepared")
        page = self._pages.pop(0)
        self.opened.append(page)
        return page

    def close(self) -> None:
        self.closed = True


class Browser:
    def __init__(self, profile_pages: list[Page], fresh_pages: list[Page] | None = None) -> None:
        self.contexts = [Context(profile_pages)]
        self.fresh = Context(fresh_pages or [])
        self.new_contexts = 0
        self.closed = False

    def new_context(self) -> Context:
        self.new_contexts += 1
        return self.fresh

    def close(self) -> None:
        self.closed = True


class Driver:
    def __init__(self, browser: Browser, *, refuse: Exception | None = None) -> None:
        self.chromium = self
        self.browser = browser
        self.refuse = refuse
        self.connects: list[str] = []

    def connect_over_cdp(self, endpoint: str, timeout: float | None = None) -> Browser:
        self.connects.append(endpoint)
        if self.refuse is not None:
            raise self.refuse
        return self.browser


class Clock:
    """A clock that moves only when somebody sleeps: every wait of the code ends at once, and the
    recorded sleeps show how long the code WOULD have waited."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


# -- X page builders --------------------------------------------------------------------------------

#: Wall-clock "now" of the writer tests, and ids minted around it.
NOW_MS = 1_790_000_000_000


def snowflake_at(epoch_ms: int, sequence: int = 0) -> str:
    """An X id minted at `epoch_ms` (the inverse of `x_writer.snowflake_ms`)."""
    return str(((epoch_ms - SNOWFLAKE_EPOCH_MS) << 22) + sequence)


def post_article(
    handle: str,
    post_id: str,
    text: str | None = None,
    *,
    name: str | None = None,
    published_at: str | None = None,
    metrics_label: str | None = None,
    logged_in: bool = True,
    extra_links: tuple[str, ...] = (),
) -> Element:
    """A post as X renders it. `logged_in=False` is the MEASURED logged-out shape: an `article`
    with links and the whole card as its only text, no `tweetText`, `User-Name` or `time`."""
    display = name or handle.capitalize()
    children: dict[str, list[Element]] = {
        SEL_STATUS_LINK: [Element(attrs={"href": f"/{handle}/status/{post_id}"})]
        + [Element(attrs={"href": href}) for href in extra_links]
    }
    if logged_in:
        children[SEL_POST_USER_NAME] = [Element(f"{display}\n@{handle}\n·\n3h")]
        if text is not None:
            children[SEL_POST_TEXT] = [Element(text)]
        if published_at is not None:
            children[SEL_TIME] = [Element(attrs={"datetime": published_at})]
        if metrics_label is not None:
            children[SEL_ACTION_BAR] = [Element(attrs={"aria-label": metrics_label})]
    card = f"{display}\n@{handle}\n{text or ''}\n{metrics_label or ''}".strip()
    return Element(card, children=children)


def x_article(
    handle: str,
    post_id: str,
    *,
    title: str,
    body: str,
    embedded_handle: str,
    embedded_id: str,
    embedded_text: str,
) -> Element:
    """An X ARTICLE as measured logged in on 27.09 (x.com/demo_author/status/2102827031166189870):
    an embedded post's link, name, `tweetText` and time come BEFORE the article's own timestamp
    link; the article has a title and a rich-text body instead of a `tweetText` of its own."""
    links = [
        f"/{handle}/status/{post_id}/analytics",
        f"https://x.com/{embedded_handle}/status/{embedded_id}",
        f"/{embedded_handle}/status/{embedded_id}",
        f"/{handle}/status/{post_id}",
    ]
    return Element(
        f"{handle.capitalize()}\n@{handle}\n{title}",
        children={
            SEL_STATUS_LINK: [Element(attrs={"href": href}) for href in links],
            SEL_POST_USER_NAME: [
                Element(f"{handle.capitalize()}\n@{handle}"),
                Element(f"{embedded_handle.capitalize()}\n@{embedded_handle}\n·\nSep 22"),
            ],
            SEL_POST_TEXT: [Element(embedded_text)],
            SEL_TIME: [
                Element(attrs={"datetime": "2026-09-22T17:06:11.000Z"}),
                Element(attrs={"datetime": "2026-09-23T18:26:47.000Z"}),
            ],
            SEL_ARTICLE_TITLE: [Element(title)],
            SEL_ARTICLE_BODY: [Element(body)],
            SEL_ACTION_BAR: [
                Element(
                    attrs={"aria-label": "6 replies, 2 reposts, 4 likes, 1 bookmark, 1200 views"}
                )
            ],
        },
    )


def cell(content: Element | None = None, *, heading: str | None = None) -> Element:
    """A timeline row: a post, or a heading row such as "Discover more"."""
    if content is not None:
        return Element(content.text, children={SEL_ARTICLE: [content]})
    return Element(heading or "")


class XPage(Page):
    """A page of X: posts, the navigation (who is logged in), the body text, optional rows."""

    def __init__(
        self,
        posts: list[Element] | None = None,
        *,
        viewer: str | None = None,
        switcher_text: str | None = None,
        cells: list[Element] | None = None,
        page_text: str = "",
        **page_kwargs: Any,
    ) -> None:
        super().__init__(**page_kwargs)
        self.posts: list[Element] = list(posts or [])
        self.page_text = page_text
        self.elements[SEL_ARTICLE] = lambda: list(self.posts)
        self.elements[SEL_BODY] = lambda: [Element(self.body_text())]
        if viewer is not None:
            self.elements[SEL_NAV_PROFILE_LINK] = [Element(attrs={"href": f"/{viewer}"})]
        if switcher_text is not None:
            self.elements[SEL_ACCOUNT_SWITCHER] = [Element(switcher_text)]
        if cells is not None:
            self.elements[SEL_CELL] = list(cells)

    def body_text(self) -> str:
        cards = "\n".join(post.text for post in self.posts)
        return f"Home\nExplore\n{cards}\n{self.page_text}".strip()


class Composer:
    """X's composer as the actuator sees it: a textbox, and a button that turns live only when
    the EDITOR'S OWN STATE holds text (DraftJS keeps one apart from the DOM).

    `fill_registers=False` is the DraftJS desync: `fill` puts the text into the DOM, the editor's
    state stays empty, the button stays disabled. `typed_as` rewrites what the keyboard produces
    (a typeahead that completes a mention). `on_submit` is what the platform does after the click.
    """

    def __init__(
        self,
        page: Page,
        *,
        fill_registers: bool = True,
        typing_registers: bool = True,
        typed_as: Callable[[str], str] | None = None,
        button_never_enables: bool = False,
        on_submit: Callable[[str], None] | None = None,
        accepts_images: bool = False,
        image_previews: bool = True,
        image_clears_text: bool = False,
    ) -> None:
        self.textbox = Element()
        self.images: list[Any] = []
        self._page = page
        self._image_previews = image_previews
        self._image_clears_text = image_clears_text
        self.button = Element(enabled=False)
        self.state = ""
        self.selected = False
        self.submitted: list[str] = []
        self.submitted_with_images: list[int] = []
        self._fill_registers = fill_registers
        self._typing_registers = typing_registers
        self._typed_as = typed_as or (lambda text: text)
        self._button_never_enables = button_never_enables
        self._on_submit = on_submit or (lambda _text: None)
        self.textbox.on_fill = self._fill
        self.textbox.on_press = self._press
        self.textbox.on_type = self._type
        self.button.on_click = self._click
        page.elements[SEL_COMPOSER_TEXTBOX] = [self.textbox]
        page.elements[SEL_SUBMIT_INLINE] = [self.button]
        if accepts_images:
            # X's hidden media input: it takes the file, shows a preview when the upload works.
            file_input = Element(visible=False)
            file_input.on_files = self._files
            page.elements[SEL_FILE_INPUT] = [file_input]

    def _files(self, files: Any) -> None:
        self.images.append(files)
        if self._image_previews:
            self._page.elements[SEL_ATTACHMENTS] = [Element("image")]
        if self._image_clears_text:
            # A re-render that lost the typed text while the upload ran; the button stays live
            # because the image alone is postable.
            self.textbox.text = ""
            self.button.enabled = True

    def _sync_button(self) -> None:
        self.button.enabled = bool(self.state.strip()) and not self._button_never_enables

    def _fill(self, value: str) -> None:
        self.textbox.text = value
        if self._fill_registers:
            self.state = value
        self._sync_button()

    def _press(self, key: str) -> None:
        if key == "ControlOrMeta+a":
            self.selected = True
        elif key == "Backspace" and self.selected:
            self.textbox.text, self.state, self.selected = "", "", False
        self._sync_button()

    def _type(self, text: str) -> None:
        produced = self._typed_as(text)
        self.textbox.text += produced
        if self._typing_registers:
            self.state += produced
        self._sync_button()

    def _click(self) -> None:
        # What went out with the click: the text, and whether an image was attached by then.
        self.submitted_with_images.append(len(self.images))
        self.submitted.append(self.state)
        self._on_submit(self.state)


def show_toast(page: Page, *, href: str | None, text: str = "Your post was sent.") -> None:
    """X's toast after a submission; `href` is the "View" link, `None` for an error toast."""
    page.elements[SEL_TOAST] = [Element(f"{text}\nView" if href else text)]
    page.elements[SEL_TOAST_STATUS_LINK] = [Element("View", attrs={"href": href})] if href else []
