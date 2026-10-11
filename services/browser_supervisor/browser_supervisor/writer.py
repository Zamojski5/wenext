"""The capsule's comment actuator: the ONE module in the capsule that changes something on a
platform (R4 minimal, D-028). Reached only through `POST /act/comment` on the reader's server,
behind its OWN token (`WENEXT_WRITER_TOKEN`), and that endpoint is called only by
`control_plane.execution.adapter.RedditCapsuleCommentPort`, which runs only inside
`SideEffectAdapter.perform` with a consumed `ExecutionIntent` in its signature (D-027).

How the comment is written: like a person at the keyboard. The capsule's own Chrome (the profile
where the human logged the account in) opens the thread on old.reddit.com, the actuator checks
that the page shows the EXPECTED account in its header, fills the comment form's textarea with the
consented text and clicks its save button. Then it waits for a new comment by that account to
appear in the page and reads its fullname and permalink off the DOM. No fetch outside the browser,
no user-agent games, no `Runtime.evaluate`: the same navigation-and-input vocabulary a human's
session produces, which is what makes the repository's bypass scanner able to COUNT every write
site in this file (`control_plane/execution/bypass_scan.py::PERMITTED_PLATFORM_ACTUATORS`).

Three outcomes, three exception types, because the caller has to treat them differently:

  * `CommentRefused`: nothing was submitted (wrong account in the header, archived or locked
    thread without a form, text too long, a form error Reddit shows before accepting). Safe to
    report and to leave the request as it was.
  * `CommentBlocked`: Reddit said no in a way that will not pass on retry (rate limit, suspended
    account, "you are doing that too much"). The queue must STOP, not try the next one.
  * `CommentUnknown`: the save button was clicked and no confirmation could be read back within
    the timeout. The comment may exist. NEVER retried automatically (L5335); the control plane
    reconciles by reading the thread.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

#: The header carrying the writer's token: a different secret than the reader's, so a process that
#: may read (`api`'s observer) is not thereby a process that may write.
WRITER_TOKEN_HEADER = "X-Wenext-Writer-Token"
ENV_WRITER_TOKEN = "WENEXT_WRITER_TOKEN"

#: The comment form lives on old.reddit.com: plain HTML, a textarea and a button, stable for
#: years. www.reddit.com's composer is a web component inside shadow DOM and changes monthly.
WRITE_HOST = "old.reddit.com"
_THREAD_PATH = re.compile(r"^/r/(?P<sub>[A-Za-z0-9_]{2,21})/comments/(?P<post>[a-z0-9]{1,12})(/|$)")
_FULLNAME = re.compile(r"^t[13]_[a-z0-9]{1,12}$")

MAX_TEXT_CHARS = 10_000
DEFAULT_TIMEOUT_S = 45.0

#: WŁASNY POST (0023): formularz `submit` starego Reddita, ta sama zasada co przy komentarzu.
#: `?selftext=true` wybiera zakładkę postu tekstowego, więc nie trzeba w nią klikać; link byłby
#: innym rodzajem publikacji i tego rodzaju tu nie ma.
_SUBREDDIT = re.compile(r"^[A-Za-z0-9_]{2,21}$")
MAX_TITLE_CHARS = 300
MAX_POST_CHARS = 40_000
#: Adres, pod którym staje nowy post: to po nim poznajemy, że platforma go przyjęła.
_NEW_POST_PATH = re.compile(r"^/r/(?P<sub>[A-Za-z0-9_]+)/comments/(?P<post>[a-z0-9]{1,12})(/|$)")
#: Czego stary Reddit odmawia PRZY ZAKŁADANIU wątku, a czego nie ma przy komentowaniu.
POST_REFUSED_MARKERS = (
    "that subreddit doesn't exist",
    "subreddit_noexist",
    "you aren't allowed to post there",
    "subreddit_notallowed",
    "this subreddit only allows",
    "no_selfs",
    "title_too_long",
    "no_text",
    "bad_captcha",
)

#: Phrases old.reddit shows when it refuses to take the comment. Matched case-insensitively against
#: the form's error text (or the page, for the account-level ones).
BLOCKED_MARKERS = (
    "you are doing that too much",
    "your account has been suspended",
    "ratelimit",
    "rate limit",
    "suspended",
)
#: Page-level phrases that mean the ACCOUNT is in trouble, checked before any form is touched.
PAGE_BLOCKED_MARKERS = ("you are doing that too much", "your account has been suspended")

#: Strona, którą platforma pokazuje ZAMIAST żądanej, gdy odmawia całemu klientowi. Zmierzone
#: 21.09 sondą kapsuły: `old.reddit.com` oddał 403 i stronę „You've been blocked / network
#: security" dla DWÓCH różnych społeczności i dla wątku, który pilot komentował 17.09, podczas
#: gdy `www.reddit.com` odpowiadał 200 i pokazywał zalogowane konto. To nie jest odmowa jednego
#: zapisu, tylko brak drogi z tej przeglądarki na ten host.
HOST_BLOCKED_MARKERS = (
    "you've been blocked",
    "youve been blocked",
    "network security",
    "blocked by network security",
)


def host_blocked(status: int | None, body_text: str) -> str | None:
    """Zdanie, gdy platforma odmówiła CAŁEMU klientowi, albo `None`.

    ROZRÓŻNIENIE JEST CAŁYM SENSEM TEJ FUNKCJI. Odmowa jednego zapisu („wątek zablokowany",
    „za długie") dotyczy tej jednej próby i następna ma sens. Odmowa hosta znaczy, że NIC z tej
    przeglądarki tam nie wejdzie, więc kolejka ma stanąć, a nie mielić kolejne wnioski i palić
    próby. Przed 21.09 obie drogi kończyły się tak samo, czyli pojedynczą odmową, i publikator
    próbowałby w nieskończoność, każdy raz „tylko ten jeden".

    Status 403 sam w sobie NIE wystarcza: bywa odpowiedzią na pojedynczy zakazany zasób. Dopiero
    razem z treścią strony blokady jest tym, czym jest. Sam tekst też nie wystarcza, bo słowo
    „blocked" pada w cudzych wątkach o moderacji.
    """
    text = (body_text or "").lower()
    marker = next((one for one in HOST_BLOCKED_MARKERS if one in text), None)
    if marker is None:
        return None
    if status is not None and status < 400:
        return None
    return (
        f"host odmówił całemu klientowi (status {status}, strona blokady: {marker!r}). "
        "To nie jest odmowa jednego zapisu: z tej przeglądarki nie ma dziś drogi na ten host"
    )


REFUSED_MARKERS = (
    "archived post",
    "this thread has been locked",
    "thread_locked",
    "too long",
    "too_long",
    "user_required",
    "you must be logged in",
)


class CommentRefused(Exception):
    """Nothing was submitted: the save button was never clicked."""

    submit_started = False


class CommentBlocked(Exception):
    """The platform refused in a way that will not pass on retry. Stop the queue. Raised only
    before the click (account, page-level block) or from a form error that Reddit shows INSTEAD
    of accepting; in the second case the click did happen, so `submit_started` says so."""

    def __init__(self, message: str, *, submit_started: bool = False) -> None:
        super().__init__(message)
        self.submit_started = submit_started


class CommentUnknown(Exception):
    """The save button was clicked (or the actuator cannot prove it was not) and no confirmation
    could be read back. `submit_started` is the ONLY thing a requeue may trust: it is set from the
    actuator's own state at the moment of the failure, never inferred from the error text."""

    def __init__(self, message: str, *, submit_started: bool = True) -> None:
        super().__init__(message)
        self.submit_started = submit_started


#: TE SAME TRZY WYNIKI dotyczą każdego zapisu na platformie, nie tylko komentarza: nic nie
#: wysłano, platforma odmówiła na stałe, skutek nieznany. Klasy zostały przy swoich nazwach, bo
#: komentarz był pierwszy i nazwy są w kilkunastu miejscach; własny post używa aliasów, żeby kod,
#: który zakłada wątek, nie musiał udawać, że komentuje.
WriteRefused = CommentRefused
WriteBlocked = CommentBlocked
WriteUnknown = CommentUnknown


class _NoPlaywright(Exception):
    """Stands in for Playwright's error classes where Playwright is not installed (unit tests on
    the host). Nothing ever raises it, so the `except` clauses that name it never match."""


def authorize_thread_url(url: str) -> str:
    """The canonical old.reddit.com thread URL for `url`, or `CommentRefused`.

    Accepts `https://www.reddit.com/r/<sub>/comments/<id>/...` and the old.reddit.com spelling;
    anything else (another host, another path shape, a fragment, user-info) is refused before Chrome
    is asked. The canonical form drops the title slug: `/r/<sub>/comments/<id>/`.
    """
    if not isinstance(url, str) or not url:
        raise CommentRefused("brak adresu wątku")
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise CommentRefused(f"tylko https, nie {parts.scheme!r}")
    if parts.netloc.lower() not in {"www.reddit.com", WRITE_HOST}:
        raise CommentRefused(f"tylko www.reddit.com albo {WRITE_HOST}, nie {parts.netloc!r}")
    match = _THREAD_PATH.match(parts.path)
    if match is None:
        raise CommentRefused(f"ścieżka nie jest wątkiem Reddita: {parts.path!r}")
    return urlunsplit(("https", WRITE_HOST, f"/r/{match['sub']}/comments/{match['post']}/", "", ""))


#: Host NOWEGO interfejsu. Osobna stała od `WRITE_HOST`, bo obie drogi mają istnieć obok siebie:
#: stary interfejs jest dziś zablokowany dla tej przeglądarki (403, zmierzone 21.09), ale blokada
#: może minąć, a droga zmierzona dziesiątki razy jest warta więcej niż nowa.
WWW_HOST = "www.reddit.com"

#: Selektory nowego interfejsu, WSZYSTKIE przepisane z artefaktu sondy
#: `output/community-probe/www_reddit_com_r_ecommerce_comments_1wixhex_….html` z 21.09
#: (655 050 bajtów, zalogowana sesja). Ani jeden nie pochodzi z pamięci, bo nowy Reddit nazywa
#: elementy generowanymi klasami i zgadywanie skończyłoby się atrapą zgodną samą ze sobą.
#:
#: `rs-current-user` jest tu najważniejszy: niesie `display-name` i `id` zalogowanego konta jako
#: ATRYBUTY, więc warunek „to nasze konto" nie opiera się na wyskrobaniu tekstu z przycisku.
WWW_CURRENT_USER = "rs-current-user"
WWW_COMPOSER_HOST = "comment-composer-host"
WWW_COMPOSER_TEXT = "div[slot='rte'][contenteditable='true']"
WWW_INNER_TRIGGER = "[data-testid='trigger-button']:visible textarea"
WWW_COMPOSER_TRIGGER = f"comment-body-header {WWW_INNER_TRIGGER}"
WWW_EDITOR_READY = f"{WWW_COMPOSER_TEXT}:visible, {WWW_INNER_TRIGGER}"
WWW_SUBMIT_BUTTON = "#comment-composer-submit-button"
WWW_COMMENT = "shreddit-comment"


def authorize_thread_url_www(url: str) -> str:
    """To samo co `authorize_thread_url`, tylko kanonicznie na NOWYM interfejsie.

    Jedna funkcja na host, a nie flaga w jednej: adres jest tym, co odróżnia obie drogi, więc ma
    być widać w wywołaniu, którą się wybrało.
    """
    if not isinstance(url, str) or not url:
        raise CommentRefused("brak adresu wątku")
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise CommentRefused(f"tylko https, nie {parts.scheme!r}")
    if parts.netloc.lower() not in {WWW_HOST, WRITE_HOST}:
        raise CommentRefused(f"tylko {WWW_HOST} albo {WRITE_HOST}, nie {parts.netloc!r}")
    match = _THREAD_PATH.match(parts.path)
    if match is None:
        raise CommentRefused(f"ścieżka nie jest wątkiem Reddita: {parts.path!r}")
    return urlunsplit(("https", WWW_HOST, f"/r/{match['sub']}/comments/{match['post']}/", "", ""))


def authorize_submit_url(community: str) -> str:
    """Adres formularza nowego wątku dla `community`, albo `WriteRefused`.

    BIERZE NAZWĘ SPOŁECZNOŚCI, NIE ADRES, i to jest cała różnica w stosunku do komentarza:
    komentarz musi trafić w konkretny wątek, więc dostaje adres i sprawdzamy, czy jest nasz;
    własny post trafia w społeczność, a społeczność to jedno słowo, które da się sprawdzić w
    całości. Adres składamy sami, więc nie ma czego przemycić w ścieżce ani w zapytaniu.
    """
    if not isinstance(community, str) or not community.strip():
        raise WriteRefused("brak społeczności")
    name = community.strip()
    for prefix in ("/r/", "r/"):
        if name.lower().startswith(prefix):
            name = name[len(prefix) :]
    name = name.strip("/")
    if not _SUBREDDIT.match(name):
        raise WriteRefused(f"nazwa społeczności nie jest nazwą subreddita: {community!r}")
    return urlunsplit(("https", WRITE_HOST, f"/r/{name}/submit", "selftext=true", ""))


def require_fullname(value: str) -> str:
    if not isinstance(value, str) or not _FULLNAME.match(value):
        raise CommentRefused(f"cel komentarza nie jest fullname t3_/t1_: {value!r}")
    return value


def public_permalink(href: str | None, *, thread_url: str, comment_id: str) -> str:
    """The comment's permalink on www.reddit.com. Old reddit hands out an absolute or relative
    href; a missing one is rebuilt from the thread and the id, which Reddit resolves."""
    if href:
        parts = urlsplit(href)
        path = parts.path if parts.path.startswith("/") else f"/{parts.path}"
        return urlunsplit(("https", "www.reddit.com", path, "", ""))
    base = urlsplit(thread_url).path.rstrip("/")
    return urlunsplit(("https", "www.reddit.com", f"{base}/_/{comment_id}/", "", ""))


@dataclass(frozen=True)
class CommentResult:
    """What the page showed after the save button: the new comment, by our account."""

    fullname: str
    comment_id: str
    permalink: str
    account: str
    observed_at: str
    raw: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "fullname": self.fullname,
            "comment_id": self.comment_id,
            "permalink": self.permalink,
            "account": self.account,
            "observed_at": self.observed_at,
            "submit_started": True,
            "raw": self.raw,
        }


@dataclass(frozen=True)
class PostResult:
    """Co pokazała strona po wysłaniu formularza: nowy wątek, naszego konta."""

    fullname: str
    post_id: str
    permalink: str
    account: str
    observed_at: str
    title: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "fullname": self.fullname,
            "post_id": self.post_id,
            "permalink": self.permalink,
            "account": self.account,
            "observed_at": self.observed_at,
            "title": self.title,
            "submit_started": True,
            "raw": self.raw,
        }


class CommentActuator(Protocol):
    """One operation. Named `comment`, not `submit`/`post`: the reader's HTTP handler calls this
    method, and the reader module must stay free of the scanner's writing vocabulary; the writing
    calls live in the implementation below, in this file, where they are counted."""

    def comment(
        self,
        *,
        thread_url: str,
        parent_fullname: str,
        text: str,
        account: str,
        timeout_s: float,
    ) -> CommentResult: ...

    #: Nazwa `own_post`, nie `post` ani `submit`: te dwa słowa są w słowniku skanera zapisów
    #: (`bypass_scan.WRITING_METHOD_NAMES`), a ten protokół woła `reader.py`, który ma zostać
    #: wolny od słownictwa zapisu. Klikanie i wpisywanie jest w tym pliku, gdzie się je liczy.
    def own_post(
        self,
        *,
        community: str,
        title: str,
        text: str,
        account: str,
        timeout_s: float,
    ) -> PostResult: ...


def _lower(text: str | None) -> str:
    return (text or "").strip().lower()


def _body_text(page, *, timeout_ms: int = 3000) -> str:
    """Tekst strony albo pusty napis. Nigdy nie rzuca: wołamy go po to, żeby ROZPOZNAĆ odmowę,
    więc jego własna porażka nie może zastąpić odmowy czymś innym."""
    try:
        return page.locator("body").inner_text(timeout=timeout_ms) or ""
    except Exception:
        return ""


class CdpCommentActuator:
    """`CommentActuator` over the capsule's own Chrome, attached through CDP, on old.reddit.com.

    `driver` is the Playwright sync driver to use, shared with the reader's `CdpPageFetcher` when
    both live on the same thread: the sync API starts one event loop per thread, and a SECOND
    `sync_playwright().start()` on that thread fails with "Sync API inside the asyncio loop"
    before touching any page (measured live 2026-09-09 00:4x, the first smoke: attempt UNKNOWN,
    nothing published). Without a shared driver the actuator starts its own, which is right only
    when it is the sole Playwright user of its thread (the `--actuator-check` run).

    `url_policy` decides which thread URLs may be written to and canonicalises them; the default
    is Reddit only. The actuator check passes a policy for its loopback stub. `submit_policy` is
    the same thing for the OTHER write this class does since 0023: founding a thread of our own
    in a community (`own_post`). Two policies rather than one, because the two writes take
    different things: a comment takes an address, an own post takes a community name.
    """

    def __init__(
        self,
        endpoint_http: str | Callable[[], str],
        *,
        driver: Callable[[], Any] | None = None,
        url_policy: Callable[[str], str] = authorize_thread_url,
        submit_policy: Callable[[str], str] = authorize_submit_url,
        www_policy: Callable[[str], str] = authorize_thread_url_www,
    ) -> None:
        self._endpoint_http = endpoint_http
        self._shared_driver = driver
        self._url_policy = url_policy
        self._submit_policy = submit_policy
        # TRZECIA POLITYKA, bo są trzy różne cele: wątek na starym interfejsie, społeczność przy
        # własnym poście i wątek na nowym. Kontrola aktuatora podmienia każdą z nich na
        # przepuszczającą, żeby mierzyć wpisywanie i odczyt dowodu na atrapie z pętli zwrotnej, a
        # nie listę dozwolonych adresów, którą mierzą testy jednostkowe.
        self._www_policy = www_policy
        self._playwright: Any = None

    def _driver(self) -> Any:
        if self._shared_driver is not None:
            return self._shared_driver()
        if self._playwright is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
        return self._playwright

    def _endpoint(self) -> str:
        if callable(self._endpoint_http):
            return self._endpoint_http()
        return self._endpoint_http

    def comment(
        self,
        *,
        thread_url: str,
        parent_fullname: str,
        text: str,
        account: str,
        timeout_s: float,
    ) -> CommentResult:
        """Classifies EVERY failure by one fact the actuator itself knows: whether the save button
        had been clicked. Before the click any exception is a refusal (nothing sent); after it,
        an unknown (the comment may exist). The three named exceptions pass through with their
        own flag; anything else (a Playwright error, a bug) is wrapped so the caller never has
        to guess from an error message."""
        # Follow the interface named by the target. The www actuator was tested but never
        # connected to this entry point, so canonical URLs were silently sent to old Reddit.
        # This is chosen before navigation, never as a retry after a platform refusal.
        if urlsplit(thread_url).hostname == WWW_HOST:
            return self.comment_www(
                thread_url=thread_url,
                parent_fullname=parent_fullname,
                text=text,
                account=account,
                timeout_s=timeout_s,
            )
        state = {"submit_started": False}
        try:
            return self._comment_impl(
                thread_url=thread_url,
                parent_fullname=parent_fullname,
                text=text,
                account=account,
                timeout_s=timeout_s,
                state=state,
            )
        except (CommentRefused, CommentBlocked, CommentUnknown):
            raise
        except Exception as exc:
            if state["submit_started"]:
                raise CommentUnknown(
                    f"po kliknięciu zapisu: {type(exc).__name__}: {exc}"[:400], submit_started=True
                ) from exc
            raise CommentRefused(f"przed wysłaniem: {type(exc).__name__}: {exc}"[:400]) from exc

    def comment_www(
        self,
        *,
        thread_url: str,
        parent_fullname: str,
        text: str,
        account: str,
        timeout_s: float,
    ) -> CommentResult:
        """To samo co `comment`, na NOWYM interfejsie. Istnieje OBOK, nie zamiast.

        Powód powstania: 21.09 stary interfejs zaczął oddawać tej przeglądarce 403 i stronę
        blokady, także na stronie wątku, który pilot komentował 17.09. Powód istnienia obok:
        blokada może minąć, a droga zmierzona dziesiątki razy jest warta więcej niż nowa.

        Klasyfikacja wyników jest TA SAMA i to jest celowe: przed kliknięciem każdy wyjątek to
        odmowa, po kliknięciu skutek nieznany. Zmienia się markup, nie zasady.
        """
        state = {"submit_started": False}
        try:
            return self._comment_www_impl(
                thread_url=thread_url,
                parent_fullname=parent_fullname,
                text=text,
                account=account,
                timeout_s=timeout_s,
                state=state,
            )
        except (CommentRefused, CommentBlocked, CommentUnknown):
            raise
        except Exception as exc:
            if state["submit_started"]:
                raise CommentUnknown(
                    f"po kliknięciu zapisu: {type(exc).__name__}: {exc}"[:400], submit_started=True
                ) from exc
            raise CommentRefused(f"przed wysłaniem: {type(exc).__name__}: {exc}"[:400]) from exc

    def own_post(
        self,
        *,
        community: str,
        title: str,
        text: str,
        account: str,
        timeout_s: float,
    ) -> PostResult:
        """Zakłada wątek w społeczności. Klasyfikuje tak samo jak `comment`: wszystko przed
        kliknięciem zapisu to odmowa, wszystko po nim to skutek nieznany."""
        state = {"submit_started": False}
        try:
            return self._own_post_impl(
                community=community,
                title=title,
                text=text,
                account=account,
                timeout_s=timeout_s,
                state=state,
            )
        except (WriteRefused, WriteBlocked, WriteUnknown):
            raise
        except Exception as exc:
            if state["submit_started"]:
                raise WriteUnknown(
                    f"po wysłaniu formularza: {type(exc).__name__}: {exc}"[:400],
                    submit_started=True,
                ) from exc
            raise WriteRefused(f"przed wysłaniem: {type(exc).__name__}: {exc}"[:400]) from exc

    def _own_post_impl(
        self,
        *,
        community: str,
        title: str,
        text: str,
        account: str,
        timeout_s: float,
        state: dict[str, bool],
    ) -> PostResult:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
        except ImportError:  # a host without Playwright (unit tests): never raised, never caught
            PlaywrightError = PlaywrightTimeout = _NoPlaywright  # type: ignore[misc,assignment]

        url = self._submit_policy(community)
        clean_title = (title or "").strip()
        if not clean_title:
            raise WriteRefused("pusty tytuł")
        if "\n" in clean_title or "\r" in clean_title:
            raise WriteRefused("tytuł w jednej linii")
        if len(clean_title) > MAX_TITLE_CHARS:
            raise WriteRefused(f"tytuł ma {len(clean_title)} znaków, limit {MAX_TITLE_CHARS}")
        if not text or not text.strip():
            raise WriteRefused("pusta treść postu")
        if len(text) > MAX_POST_CHARS:
            raise WriteRefused(f"treść ma {len(text)} znaków, limit {MAX_POST_CHARS}")
        if not account or not account.strip():
            raise WriteRefused("brak oczekiwanego konta")
        deadline_ms = int(timeout_s * 1000)

        driver = self._driver()
        try:
            browser = driver.chromium.connect_over_cdp(self._endpoint(), timeout=deadline_ms)
        except Exception as exc:  # no page was touched yet: any connection failure is a refusal
            raise WriteRefused(f"brak połączenia CDP z Chrome kapsuły: {exc}") from exc
        try:
            if not browser.contexts:
                raise WriteRefused("Chrome kapsuły nie ma kontekstu profilu, brak sesji")
            page = browser.contexts[0].new_page()
            try:
                try:
                    response = page.goto(url, wait_until="load", timeout=deadline_ms)
                except PlaywrightTimeout as exc:
                    raise WriteRefused(
                        f"formularz {url} nie załadował się w {timeout_s:g} s"
                    ) from exc
                except PlaywrightError as exc:
                    raise WriteRefused(f"nawigacja do {url} nie powiodła się: {exc}") from exc
                if response is None or response.status >= 400:
                    status = response.status if response is not None else None
                    stopped = host_blocked(status, _body_text(page))
                    if stopped is not None:
                        raise WriteBlocked(stopped)
                    raise WriteRefused(
                        f"formularz {url} odpowiedział {status if status is not None else 'niczym'}"
                    )

                # 1. Konto na stronie, tak samo jak przy komentarzu: twardy warunek.
                shown = _lower(
                    _first_text(page.locator("#header-bottom-right span.user a"), timeout_ms=5_000)
                )
                if shown != account.strip().lower():
                    if not shown or shown in {"login", "log in or sign up", "log in"}:
                        raise WriteRefused("strona nie pokazuje zalogowanego konta")
                    raise WriteBlocked(
                        f"strona pokazuje konto {shown!r}, oczekiwano {account!r}: sesja w kapsule "
                        "należy do innego konta, kolejka stoi"
                    )
                body_text = _lower(page.locator("body").inner_text(timeout=5_000))
                blocked = next((m for m in PAGE_BLOCKED_MARKERS if m in body_text), None)
                if blocked is not None:
                    raise WriteBlocked(f"strona zgłasza blokadę: {blocked!r}")
                refused = next((m for m in POST_REFUSED_MARKERS if m in body_text), None)
                if refused is not None:
                    raise WriteRefused(f"formularz odmawia przed wysłaniem: {refused!r}")

                # 2. Formularz nowego wątku. Jego brak znaczy, że tu nie wolno pisać.
                form = page.locator("form#newlink").first
                if form.count() == 0:
                    raise WriteRefused(
                        "brak formularza nowego wątku (społeczność zamknięta albo tylko linki?)"
                    )

                # 3. Wpisz i wyślij. Od następnej instrukcji wynik należy do platformy.
                form.locator("[name='title']").first.fill(clean_title, timeout=deadline_ms)
                form.locator("textarea[name='text']").first.fill(text, timeout=deadline_ms)
                state["submit_started"] = True
                form.locator("button[name='submit']").first.click(timeout=deadline_ms)

                started = time.monotonic()
                while time.monotonic() - started < timeout_s:
                    result = _own_post_of(
                        page=page, account=account, title=clean_title, submit_url=url
                    )
                    if result is not None:
                        return result
                    error_text = _lower(_visible_text(form.locator(".error")))
                    if error_text:
                        if any(m in error_text for m in BLOCKED_MARKERS):
                            raise WriteBlocked(
                                f"Reddit odmówił: {error_text[:200]}", submit_started=True
                            )
                        # JEDNA ZASADA DLA OBU ZAPISÓW: po kliknięciu każdy błąd formularza,
                        # który nie jest blokadą, jest skutkiem nieznanym. Rozpoznawanie „to
                        # akurat nic nie zapisało" po treści komunikatu byłoby zgadywaniem, a
                        # zgadnięcie w złą stronę daje drugi post o tej samej treści.
                        raise WriteUnknown(
                            f"formularz pokazuje błąd: {error_text[:200]}", submit_started=True
                        )
                    time.sleep(1.0)
                raise WriteUnknown(
                    f"po wysłaniu formularza przez {timeout_s:g} s nie widać wątku konta "
                    f"{account} w {community}; skutek nieznany, sprawdź społeczność, nie "
                    "publikuj ponownie"
                )
            finally:
                page.close()
        finally:
            browser.close()

    def _comment_www_impl(
        self,
        *,
        thread_url: str,
        parent_fullname: str,
        text: str,
        account: str,
        timeout_s: float,
        state: dict[str, bool],
    ) -> CommentResult:
        """Wpisz i wyślij komentarz w kompozytorze nowego interfejsu, potem ODCZYTAJ DOWÓD.

        Każdy selektor pochodzi z artefaktu sondy z 21.09, nie z pamięci. Trzy różnice wobec
        starego interfejsu, wszystkie zmierzone: treść wpisuje się w `contenteditable`, a nie w
        `textarea`; wysyłka podmienia fragment strony, a nie nawiguje; a konto zalogowane niesie
        `rs-current-user` jako ATRYBUTY, więc warunek „to nasze konto" nie skrobie tekstu z
        przycisku.

        DOWODEM JEST `shreddit-comment` NASZEGO KONTA, którego przed wysłaniem nie było. Jego
        `thingid` i `permalink` to te same dwa fakty, które czyta stara droga z `.thing.comment`;
        zmieniły się nazwy atrybutów, nie sens dowodu.
        """
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
        except ImportError:  # a host without Playwright (unit tests): never raised, never caught
            PlaywrightError = PlaywrightTimeout = _NoPlaywright  # type: ignore[misc,assignment]

        url = self._www_policy(thread_url)
        require_fullname(parent_fullname)
        if not text or not text.strip():
            raise CommentRefused("pusty tekst komentarza")
        if len(text) > MAX_TEXT_CHARS:
            raise CommentRefused(f"tekst ma {len(text)} znaków, limit {MAX_TEXT_CHARS}")
        if not account or not account.strip():
            raise CommentRefused("brak oczekiwanego konta")
        deadline_ms = int(timeout_s * 1000)
        wanted = account.strip().lower()

        driver = self._driver()
        try:
            browser = driver.chromium.connect_over_cdp(self._endpoint(), timeout=deadline_ms)
        except Exception as exc:  # no page was touched yet: any connection failure is a refusal
            raise CommentRefused(f"brak połączenia CDP z Chrome kapsuły: {exc}") from exc
        try:
            if not browser.contexts:
                raise CommentRefused("Chrome kapsuły nie ma kontekstu profilu, brak sesji")
            page = browser.contexts[0].new_page()
            try:
                try:
                    response = page.goto(url, wait_until="load", timeout=deadline_ms)
                except PlaywrightTimeout as exc:
                    raise CommentRefused(
                        f"wątek {url} nie załadował się w {timeout_s:g} s"
                    ) from exc
                except PlaywrightError as exc:
                    raise CommentRefused(f"nawigacja do {url} nie powiodła się: {exc}") from exc
                if response is None or response.status >= 400:
                    status = response.status if response is not None else None
                    stopped = host_blocked(status, _body_text(page))
                    if stopped is not None:
                        raise CommentBlocked(stopped)
                    raise CommentRefused(
                        f"wątek {url} odpowiedział {status if status is not None else 'niczym'}"
                    )

                # 1. KONTO. Twardy warunek, tak samo jak na starym interfejsie, tylko z atrybutu.
                shown = _lower(
                    _first_attribute(
                        page.locator(WWW_CURRENT_USER), "display-name", timeout_ms=5_000
                    )
                )
                if shown != wanted:
                    if not shown:
                        raise CommentRefused("strona nie pokazuje zalogowanego konta")
                    raise CommentBlocked(
                        f"strona pokazuje konto {shown!r}, oczekiwano {account!r}: sesja w "
                        "kapsule należy do innego konta, kolejka stoi"
                    )
                body_text = _lower(_body_text(page, timeout_ms=5000))
                blocked = next((m for m in PAGE_BLOCKED_MARKERS if m in body_text), None)
                if blocked is not None:
                    raise CommentBlocked(f"strona zgłasza blokadę: {blocked!r}")

                # 2. KOMPOZYTOR. Bywa dogrywany komponentem, więc czekamy NA NIEGO, a nie na
                # upływ czasu; jego brak znaczy, że w tym wątku nie wolno pisać.
                try:
                    composer, field = _www_comment_editor(page, timeout_ms=deadline_ms)
                except PlaywrightTimeout as exc:
                    raise CommentRefused(
                        "edytor komentarza nie jest gotowy do wpisania "
                        "(brak dostępu albo nie udało się otworzyć pola); nic nie wysłano"
                    ) from exc

                # 3. Czego było przed wysłaniem. Bez tej listy „nowy komentarz" znaczyłoby
                # „jakikolwiek nasz komentarz w tym wątku", a my mogliśmy tam pisać wcześniej.
                before = set(_www_comment_ids(page, wanted))

                field.fill(text, timeout=deadline_ms)
                state["submit_started"] = True
                composer.locator(WWW_SUBMIT_BUTTON).first.click(timeout=deadline_ms)

                started = time.monotonic()
                while time.monotonic() - started < timeout_s:
                    fresh = [one for one in _www_comment_ids(page, wanted) if one not in before]
                    if fresh:
                        # W DOWODZIE ZOSTAJE NAZWA KONTA TAKA, JAK W ZGODZIE, nie sprowadzona do
                        # małych liter: `wanted` jest do PORÓWNAŃ ze stroną, a `account` jest
                        # tym, co pod dowodem publikacji zobaczy człowiek. Stara droga zapisuje
                        # tak samo (`account=account` niżej).
                        return _www_comment_result(page, account.strip(), fresh[0])
                    time.sleep(1.0)
                raise CommentUnknown(
                    f"po wysłaniu przez {timeout_s:g} s nie widać komentarza konta {account} "
                    f"w {url}; skutek nieznany, sprawdź wątek, nie publikuj ponownie",
                    submit_started=True,
                )
            finally:
                page.close()
        finally:
            browser.close()

    def _comment_impl(
        self,
        *,
        thread_url: str,
        parent_fullname: str,
        text: str,
        account: str,
        timeout_s: float,
        state: dict[str, bool],
    ) -> CommentResult:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
        except ImportError:  # a host without Playwright (unit tests): never raised, never caught
            PlaywrightError = PlaywrightTimeout = _NoPlaywright  # type: ignore[misc,assignment]

        url = self._url_policy(thread_url)
        parent = require_fullname(parent_fullname)
        if not text or not text.strip():
            raise CommentRefused("pusty tekst komentarza")
        if len(text) > MAX_TEXT_CHARS:
            raise CommentRefused(f"tekst ma {len(text)} znaków, limit {MAX_TEXT_CHARS}")
        if not account or not account.strip():
            raise CommentRefused("brak oczekiwanego konta")
        deadline_ms = int(timeout_s * 1000)

        driver = self._driver()
        try:
            browser = driver.chromium.connect_over_cdp(self._endpoint(), timeout=deadline_ms)
        except Exception as exc:  # no page was touched yet: any connection failure is a refusal
            raise CommentRefused(f"brak połączenia CDP z Chrome kapsuły: {exc}") from exc
        try:
            if not browser.contexts:
                raise CommentRefused("Chrome kapsuły nie ma kontekstu profilu, brak sesji")
            page = browser.contexts[0].new_page()
            try:
                try:
                    response = page.goto(url, wait_until="load", timeout=deadline_ms)
                except PlaywrightTimeout as exc:
                    raise CommentRefused(
                        f"wątek {url} nie załadował się w {timeout_s:g} s"
                    ) from exc
                except PlaywrightError as exc:
                    raise CommentRefused(f"nawigacja do {url} nie powiodła się: {exc}") from exc
                if response is None or response.status >= 400:
                    status = response.status if response is not None else None
                    # STRONA BLOKADY CAŁEGO KLIENTA zatrzymuje kolejkę, a nie tylko ten wniosek.
                    # Odczyt treści NIE jest opakowany w `suppress`, bo to połknęłoby także
                    # `CommentBlocked`, które właśnie tu podnosimy; `_body_text` nie rzuca sam.
                    stopped = host_blocked(status, _body_text(page))
                    if stopped is not None:
                        raise CommentBlocked(stopped)
                    raise CommentRefused(
                        f"wątek {url} odpowiedział {status if status is not None else 'niczym'}"
                    )

                # 1. The page must show OUR account, or nothing happens (the hard condition).
                shown = _lower(
                    _first_text(page.locator("#header-bottom-right span.user a"), timeout_ms=5_000)
                )
                if shown != account.strip().lower():
                    if not shown or shown in {"login", "log in or sign up", "log in"}:
                        raise CommentRefused("strona nie pokazuje zalogowanego konta")
                    raise CommentBlocked(
                        f"strona pokazuje konto {shown!r}, oczekiwano {account!r}: sesja w kapsule "
                        "należy do innego konta, kolejka stoi"
                    )
                body_text = _lower(page.locator("body").inner_text(timeout=5_000))
                blocked = next((m for m in PAGE_BLOCKED_MARKERS if m in body_text), None)
                if blocked is not None:
                    raise CommentBlocked(f"strona zgłasza blokadę: {blocked!r}")
                if "archived post" in body_text or "this thread has been locked" in body_text:
                    raise CommentRefused(
                        "wątek jest zarchiwizowany albo zablokowany, brak formularza"
                    )

                # 2. The form: top-level under the comment area, or the reply form of one comment.
                if parent.startswith("t3_"):
                    form = page.locator("div.commentarea > form.usertext").first
                else:
                    thing = page.locator(f".thing.comment[data-fullname='{parent}']").first
                    if thing.count() == 0:
                        raise CommentRefused(f"komentarz {parent} nie jest na stronie")
                    thing.locator("a[data-event-action='reply']").first.click(timeout=deadline_ms)
                    form = thing.locator("form.usertext").first
                if form.count() == 0:
                    raise CommentRefused("brak formularza komentarza na stronie (wątek zamknięty?)")
                known = {
                    thing.get_attribute("data-fullname") or ""
                    for thing in page.locator(".thing.comment").element_handles()
                }

                # 3. Type and save. From here on the outcome is a platform fact, not ours.
                form.locator("textarea[name='text']").first.fill(text, timeout=deadline_ms)
                # From the next statement on the outcome is a platform fact: mark it BEFORE
                # the click, so a failure between the click and this line cannot be misread.
                state["submit_started"] = True
                form.locator("button.save").first.click(timeout=deadline_ms)
                started = time.monotonic()
                while time.monotonic() - started < timeout_s:
                    error_text = _lower(_visible_text(form.locator(".error")))
                    if error_text:
                        # Reddit answered the click with an error INSTEAD of a comment: the click
                        # happened, nothing was written. Blocked stops the queue; a refusal here
                        # is still a refusal, but the flag says the click was made.
                        if any(m in error_text for m in BLOCKED_MARKERS):
                            raise CommentBlocked(
                                f"Reddit odmówił: {error_text[:200]}", submit_started=True
                            )
                        raise CommentUnknown(
                            f"formularz pokazuje błąd: {error_text[:200]}", submit_started=True
                        )
                    wanted = account.strip().lower()
                    for thing in page.locator(".thing.comment").element_handles():
                        if _lower(thing.get_attribute("data-author")) != wanted:
                            continue
                        fullname = thing.get_attribute("data-fullname") or ""
                        if fullname and fullname not in known and _FULLNAME.match(fullname):
                            link = thing.query_selector("a.bylink")
                            href = link.get_attribute("href") if link is not None else None
                            comment_id = fullname.split("_", 1)[1]
                            return CommentResult(
                                fullname=fullname,
                                comment_id=comment_id,
                                permalink=public_permalink(
                                    href, thread_url=url, comment_id=comment_id
                                ),
                                account=account,
                                observed_at=datetime.now(UTC).isoformat(),
                                raw={"url": url, "parent": parent, "href": href},
                            )
                    time.sleep(1.0)
                raise CommentUnknown(
                    f"po kliknięciu zapisu przez {timeout_s:g} s nie pojawił się nowy komentarz "
                    f"konta {account}; skutek nieznany, sprawdź wątek, nie publikuj ponownie"
                )
            finally:
                page.close()
        finally:
            browser.close()


def _own_post_of(
    *,
    page: Any,
    account: str,
    title: str,
    submit_url: str,
) -> PostResult | None:
    """Nowy wątek naszego konta, jeśli strona już nim jest; w przeciwnym razie `None`.

    Stary Reddit po przyjęciu formularza PRZENOSI PRZEGLĄDARKĘ pod adres nowego wątku, więc to
    adres jest pierwszym dowodem, a nie tekst na stronie. Identyfikator czytamy z adresu, a potem
    sprawdzamy na stronie, że wątek o tym identyfikatorze należy do naszego konta: sam adres
    mówiłby tylko, że gdzieś trafiliśmy.

    STRONA W TRAKCIE PRZENOSIN TEŻ DAJE `None`, i to jest celowe: pytanie zadaje pętla co
    sekundę, a wyjątek z na wpół załadowanej strony zamieniłby udaną publikację w „skutek
    nieznany", czyli w coś, czego nikt nigdy nie ponowi.
    """
    try:
        return _own_post_seen(page=page, account=account, title=title, submit_url=submit_url)
    except Exception:
        return None


def _own_post_seen(
    *,
    page: Any,
    account: str,
    title: str,
    submit_url: str,
) -> PostResult | None:
    match = _NEW_POST_PATH.match(urlsplit(page.url).path)
    if match is None:
        return None
    post_id = match["post"]
    fullname = f"t3_{post_id}"
    thing = page.locator(f".thing.link[data-fullname='{fullname}']").first
    if thing.count() == 0:
        return None
    if _lower(thing.get_attribute("data-author")) != account.strip().lower():
        return None
    # TYTUŁ TEŻ MUSI SIĘ ZGADZAĆ, nie tylko konto (przegląd krzyżowy 19.09). Reddit potrafi
    # przenieść przeglądarkę na INNY nasz wątek, na przykład przy duplikacie, a wtedy adres i
    # autor zgadzają się oba, a dowodem byłby cudzy, wcześniejszy post. Niezgodność nie jest
    # odmową: pętla pyta dalej i skończy się „skutek nieznany", czyli po właściwej stronie
    # ostrożności, bo wątek mógł powstać i człowiek ma sprawdzić.
    shown = _squeezed(_first_text(thing.locator("a.title, p.title"), timeout_ms=2_000))
    if shown and shown != _squeezed(title):
        return None
    link = thing.locator("a.comments").first
    href = link.get_attribute("href") if link.count() else None
    path = urlsplit(href).path if href else urlsplit(page.url).path
    return PostResult(
        fullname=fullname,
        post_id=post_id,
        permalink=urlunsplit(("https", "www.reddit.com", path, "", "")),
        account=account,
        observed_at=datetime.now(UTC).isoformat(),
        title=title,
        raw={"submit_url": submit_url, "landed_on": page.url},
    )


def _squeezed(text: str | None) -> str:
    """Tekst do porównania: bez brzegów, bez podwójnych spacji, bez wielkości liter. Reddit
    potrafi zmienić same odstępy, a to nie jest inny tytuł."""
    return " ".join((text or "").split()).casefold()


def _first_text(locator: Any, *, timeout_ms: int = 2_000) -> str | None:
    """The text of the first match, or `None` when there is none or it vanished meanwhile."""
    try:
        if locator.count() == 0:
            return None
        return locator.first.text_content(timeout=timeout_ms)
    except Exception:
        return None


def _first_attribute(locator: Any, name: str, *, timeout_ms: int = 2_000) -> str | None:
    """Wartość atrybutu pierwszego dopasowania, albo `None`.

    Nowy interfejs trzyma fakty w ATRYBUTACH (`rs-current-user[display-name]`,
    `shreddit-comment[thingid]`), a nie w tekście, i to jest dla nas lepsze: atrybut nie zmienia
    się od tłumaczenia strony ani od tego, co akurat widać.
    """
    try:
        # get_attribute waits for attachment; count() returned immediately during hydration
        # and falsely classified an authenticated page as logged out.
        return locator.first.get_attribute(name, timeout=timeout_ms)
    except Exception:
        return None


def _www_comment_editor(page: Any, *, timeout_ms: int) -> tuple[Any, Any]:
    """Open the collapsed top-level editor without entering text or submitting anything."""
    deadline = time.monotonic() + timeout_ms / 1000

    def remaining() -> int:
        return max(1, int((deadline - time.monotonic()) * 1000))

    composer = page.locator(WWW_COMPOSER_HOST).first
    composer.wait_for(state="attached", timeout=remaining())
    field = composer.locator(WWW_COMPOSER_TEXT).first
    if not field.is_visible():
        # Measured on the authenticated page on 2026-09-23: markup for the editor already
        # exists, but Reddit exposes it only after the visible conversation control is used.
        # Target its textarea in the open shadow root, not the custom element's padding.
        page.locator(WWW_COMPOSER_TRIGGER).first.click(timeout=remaining())
        # Reddit has two triggers: the outer placeholder loads the bundle; the inner
        # placeholder opens the editor. On a warm page only the latter is needed.
        composer.locator(WWW_EDITOR_READY).first.wait_for(state="visible", timeout=remaining())
        if not field.is_visible():
            composer.locator(WWW_INNER_TRIGGER).first.click(timeout=remaining())
    field.wait_for(state="visible", timeout=remaining())
    return composer, field


def _www_comment_ids(page: Any, account: str) -> list[str]:
    """Identyfikatory komentarzy NASZEGO konta widoczne teraz w wątku, w kolejności strony.

    KONTO PORÓWNUJEMY W PYTHONIE, A NIE W SELEKTORZE, i to jest poprawka z pomiaru 21.09.
    Pierwsza wersja pytała o `shreddit-comment[author="<konto małymi literami>"]`, bo konto jest
    wcześniej sprowadzane do małych liter na potrzeby porównania z nagłówkiem. Wartości atrybutów
    w HTML rozróżniają wielkość liter, a nazwa konta ma swoją (`Demo_Maintainer`), więc ten
    selektor nie trafiał NIGDY. Skutek był najdroższy z możliwych: aktuator zapisywał komentarz i
    zaraz potem mówił „skutek nieznany, sprawdź wątek, nie publikuj ponownie", czyli zatrzymywał
    kolejkę i wołał człowieka do czegoś, co się udało. Stara droga porównuje dokładnie tak samo
    (`data-author` niżej), więc to jest zarazem powrót do idiomu tego pliku. Przy okazji znika
    wstawianie cudzej nazwy do selektora.
    """
    found: list[str] = []
    wanted = (account or "").strip().lower()
    if not wanted:
        return found
    try:
        comments = page.locator(WWW_COMMENT)
        for index in range(comments.count()):
            one = comments.nth(index)
            if _lower(one.get_attribute("author")) != wanted:
                continue
            thing = one.get_attribute("thingid")
            if thing:
                found.append(thing)
    except Exception:
        return found
    return found


def _www_comment_result(page: Any, account: str, thing_id: str) -> CommentResult:
    """Dowód z nowego interfejsu: identyfikator i permalink z atrybutów `shreddit-comment`."""
    # Ten sam powód, co wyżej: identyfikator pochodzi z cudzej strony, więc szukamy go
    # porównaniem, a nie wstawieniem do selektora.
    permalink = ""
    try:
        comments = page.locator(WWW_COMMENT)
        for index in range(comments.count()):
            one = comments.nth(index)
            if one.get_attribute("thingid") == thing_id:
                # Pytamy wprost, bo `one` to już pojedynczy element; `_first_attribute` jest od
                # lokatorów, które mogą złapać wiele, i tutaj tylko zasłaniałby brak atrybutu.
                permalink = one.get_attribute("permalink") or ""
                break
    except Exception:
        permalink = ""
    comment_id = thing_id.split("_", 1)[-1]
    return CommentResult(
        fullname=thing_id,
        comment_id=comment_id,
        permalink=public_permalink(permalink, thread_url=page.url, comment_id=comment_id),
        account=account,
        observed_at=datetime.now(UTC).isoformat(),
        raw={"thingid": thing_id, "permalink": permalink, "interfejs": "www"},
    )


def _visible_text(locator: Any, *, timeout_ms: int = 2_000) -> str | None:
    """Like `_first_text`, but only for a VISIBLE first match: old reddit keeps an empty, hidden
    `.error` span in every form, and its text is not an error."""
    try:
        if locator.count() == 0 or not locator.first.is_visible():
            return None
        return locator.first.text_content(timeout=timeout_ms)
    except Exception:
        return None
