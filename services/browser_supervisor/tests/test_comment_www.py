"""Komentarz na NOWYM interfejsie: polityka adresu i atrapa, bez Chrome.

DLACZEGO TO W OGÓLE POWSTAJE. 21.09 sonda kapsuły zmierzyła, że `old.reddit.com` oddaje tej
przeglądarce 403 i stronę blokady, także na stronie wątku, który pilot komentował 17.09.
Aktuator komentarzy pisze właśnie tam, więc pilot nie ma dziś drogi do publikacji. Nowa metoda
staje OBOK starej, nie zamiast niej: blokada może minąć, a droga zmierzona dziesiątki razy jest
warta więcej niż nowa.

CZEGO TEN PLIK NIE MIERZY, i to jest ważniejsze od tego, co mierzy: nie dotyka Chrome, więc nie
mówi nic o tym, czy `contenteditable` przyjmie wpisanie ani czy platforma przyjmie komentarz.
Od tego jest `--actuator-check` na atrapie (kontrola z Chrome) i potem JEDEN prawdziwy komentarz
w zakresie powierzenia. Tutaj sprawdzamy dwie rzeczy, które da się sprawdzić uczciwie bez
przeglądarki: co wolno otworzyć i czy atrapa niesie to, czego szuka kod.
"""

from __future__ import annotations

import pytest
from browser_supervisor.actuator_check import ACCOUNT, _www_thread_page
from browser_supervisor.writer import (
    WWW_COMMENT,
    WWW_COMPOSER_HOST,
    WWW_COMPOSER_TEXT,
    WWW_COMPOSER_TRIGGER,
    WWW_CURRENT_USER,
    WWW_EDITOR_READY,
    WWW_INNER_TRIGGER,
    WWW_SUBMIT_BUTTON,
    CdpCommentActuator,
    CommentBlocked,
    CommentRefused,
    _first_attribute,
    _www_comment_ids,
    _www_comment_result,
    authorize_thread_url_www,
)


def test_both_spellings_of_a_thread_become_the_new_interface_address() -> None:
    """Adres składamy sami z nazwy społeczności i identyfikatora wątku, bez tytułu w ścieżce."""
    canonical = "https://www.reddit.com/r/ecommerce/comments/1wixhex/"
    for given in (
        "https://www.reddit.com/r/ecommerce/comments/1wixhex/changement_de_ligne/",
        "https://old.reddit.com/r/ecommerce/comments/1wixhex/changement_de_ligne/",
        "https://www.reddit.com/r/ecommerce/comments/1wixhex/",
    ):
        assert authorize_thread_url_www(given) == canonical, given


@pytest.mark.parametrize(
    "wrong",
    [
        "",
        "http://www.reddit.com/r/ecommerce/comments/1wixhex/",
        "https://evil.example/r/ecommerce/comments/1wixhex/",
        "https://www.reddit.com@evil.example/r/ecommerce/comments/1wixhex/",
        "https://www.reddit.com/r/ecommerce/submit",
        "https://www.reddit.com/user/someone/",
    ],
)
def test_anything_else_is_refused_before_chrome_is_asked(wrong: str) -> None:
    with pytest.raises(CommentRefused):
        authorize_thread_url_www(wrong)


def test_the_stub_carries_every_selector_the_actuator_uses() -> None:
    """Atrapa i kod muszą mówić o tych samych elementach.

    Oba pisałem ja, więc ten test nie dowodzi, że tak wygląda Reddit; dowodzi, że zmiana nazwy w
    jednym miejscu nie przejdzie niezauważona w drugim. O tym, że nazwy są PRAWDZIWE, mówi
    artefakt sondy, z którego je przepisałem, i to jest osobny rodzaj dowodu.
    """
    page = _www_thread_page(account=ACCOUNT).decode("utf-8")

    assert WWW_CURRENT_USER in page
    assert f'display-name="{ACCOUNT}"' in page
    assert WWW_COMPOSER_HOST in page
    assert 'slot="rte"' in page and 'contenteditable="true"' in page
    assert WWW_SUBMIT_BUTTON.lstrip("#") in page
    assert WWW_COMMENT in page
    assert "thingid=" in page and "permalink=" in page
    assert WWW_COMPOSER_TEXT.count("[") == 2, (
        "selektor treści ma być parą warunków (slot i contenteditable); sam `div` złapałby pół "
        "strony"
    )


def test_a_thread_without_a_composer_has_nothing_to_click() -> None:
    """Tak wygląda strona, na której konto nie ma prawa pisać. Aktuator ma to rozpoznać PRZED
    wpisaniem czegokolwiek, a nie po kliknięciu."""
    page = _www_thread_page(account=ACCOUNT, locked=True).decode("utf-8")

    assert WWW_COMPOSER_HOST not in page
    assert WWW_SUBMIT_BUTTON.lstrip("#") not in page
    assert WWW_CURRENT_USER in page, "konto na stronie zostaje: to nie jest wylogowanie"


class _FakeElement:
    """Element z atrybutami i niczym więcej: tyle o stronie wie odczyt dowodu."""

    def __init__(self, **attributes: str) -> None:
        self._attributes = attributes

    def get_attribute(self, name: str) -> str | None:
        return self._attributes.get(name)


class _FakeLocator:
    def __init__(self, elements: list[_FakeElement]) -> None:
        self._elements = elements

    def count(self) -> int:
        return len(self._elements)

    def nth(self, index: int) -> _FakeElement:
        return self._elements[index]


class _FakePage:
    """Strona, która oddaje `shreddit-comment` i zapamiętuje, o co ją pytano.

    Atrapa jest tu z premedytacją GŁUPIA: nie umie selektorów z atrybutem. Gdyby umiała, test
    przechodziłby i dla kodu, który wstawia nazwę konta do selektora, czyli dla kodu z usterką,
    którą ten plik ma łapać.
    """

    url = "https://www.reddit.com/r/testsub/comments/abc123/"

    def __init__(self, elements: list[_FakeElement]) -> None:
        self._elements = elements
        self._asked: list[str] = []

    def locator(self, selector: str) -> _FakeLocator:
        self._asked.append(selector)
        if selector != WWW_COMMENT:
            raise AssertionError(
                f"kod pyta stronę o {selector!r}; odczyt dowodu ma pytać o same "
                f"{WWW_COMMENT!r} i porównywać atrybuty w Pythonie"
            )
        return _FakeLocator(self._elements)


def test_our_comment_is_recognised_although_the_account_has_capital_letters() -> None:
    """USTERKA ZMIERZONA 21.09, i to jest jej test.

    Konto jest wcześniej sprowadzane do małych liter, żeby porównać je z nagłówkiem strony.
    Pierwsza wersja odczytu dowodu wstawiała tę małą postać do selektora
    (`shreddit-comment[author="demo_maintainer"]`), a wartości atrybutów w HTML rozróżniają
    wielkość liter. Skutek: komentarz był zapisany, a aktuator mówił „skutek nieznany", czyli
    zatrzymywał kolejkę i wołał człowieka do czegoś, co się udało.
    """
    page = _FakePage(
        [
            _FakeElement(author="earlier_user", thingid="t1_e0", permalink="/r/testsub/e0/"),
            _FakeElement(author=ACCOUNT, thingid="t1_n1", permalink="/r/testsub/n1/"),
        ]
    )

    assert _www_comment_ids(page, ACCOUNT.lower()) == ["t1_n1"]
    assert _www_comment_ids(page, ACCOUNT) == ["t1_n1"], "wielkość liter nie ma tu nic zmieniać"


def test_a_comment_of_another_account_is_not_our_proof() -> None:
    """Drugi kierunek tego samego: gdyby odczyt brał cudze komentarze, aktuator zameldowałby
    publikację po cudzym wpisie, który akurat pojawił się w wątku w trakcie czekania."""
    page = _FakePage([_FakeElement(author="somebodyelse", thingid="t1_x9")])

    assert _www_comment_ids(page, ACCOUNT.lower()) == []


def test_a_comment_without_an_identifier_is_not_proof_either() -> None:
    """Dowodem jest identyfikator, nie obecność elementu: bez `thingid` nie ma czego zapisać."""
    page = _FakePage([_FakeElement(author=ACCOUNT)])

    assert _www_comment_ids(page, ACCOUNT.lower()) == []


def test_the_proof_carries_the_permalink_of_that_very_comment() -> None:
    """Permalink bierzemy z TEGO komentarza, o którym mówimy, a nie z pierwszego na stronie."""
    page = _FakePage(
        [
            _FakeElement(author="earlier_user", thingid="t1_e0", permalink="/r/testsub/e0/"),
            _FakeElement(author=ACCOUNT, thingid="t1_n1", permalink="/r/testsub/n1/"),
        ]
    )

    result = _www_comment_result(page, ACCOUNT.lower(), "t1_n1")

    assert result.fullname == "t1_n1"
    assert result.comment_id == "n1"
    assert result.permalink.endswith("/r/testsub/n1/")
    assert result.raw["interfejs"] == "www"


class _FakeAttributeLocator:
    """Lokator, który udaje Playwrighta na tyle, na ile pyta go aktuator: `.first`, `.count()`,
    `.nth()`, `.get_attribute()`, `.fill()`, `.click()`, `.wait_for()`."""

    def __init__(self, elements: list[_FakeElement], *, log: list[str], name: str) -> None:
        self._elements = elements
        self._log = log
        self._name = name

    @property
    def first(self) -> _FakeAttributeLocator:
        return self

    def count(self) -> int:
        return len(self._elements)

    def is_visible(self) -> bool:
        return bool(self._elements)

    def nth(self, index: int) -> _FakeElement:
        return self._elements[index]

    def get_attribute(self, name: str, timeout: float | None = None) -> str | None:
        if not self._elements:
            return None
        return self._elements[0].get_attribute(name)

    def inner_text(self, timeout: float | None = None) -> str:
        return "wątek testowy"

    def wait_for(self, state: str = "attached", timeout: float | None = None) -> None:
        if not self._elements:
            raise _FakeTimeout(f"nie ma {self._name}")

    def locator(self, selector: str) -> _FakeAttributeLocator:
        return self._inner_locator(selector)

    def _inner_locator(self, selector: str) -> _FakeAttributeLocator:
        raise AssertionError("atrapa złożonych zejść nie obsługuje; ustaw je w _FakeWwwPage")

    def fill(self, text: str, timeout: float | None = None) -> None:
        self._log.append(f"fill:{text}")

    def click(self, timeout: float | None = None) -> None:
        self._log.append("click")


class _FakeTimeout(Exception):
    pass


class _FakeWwwPage:
    """Strona wątku na nowym interfejsie, bez Chrome.

    Komentarz naszego konta pojawia się DOPIERO po kliknięciu, tak jak na prawdziwej stronie, bo
    inaczej test przechodziłby też dla aktuatora, który melduje publikację bez klikania.
    """

    url = "https://www.reddit.com/r/testsub/comments/abc123/"

    def __init__(
        self,
        *,
        account_on_page: str,
        has_composer: bool = True,
        collapsed: bool = False,
        lazy: bool = False,
    ) -> None:
        self.log: list[str] = []
        self._account = account_on_page
        self._composer = has_composer
        self._collapsed = collapsed
        self._lazy = lazy
        self.closed = False
        self._comments = [_FakeElement(author="earlier_user", thingid="t1_e0")]

    def goto(self, url: str, wait_until: str = "load", timeout: float | None = None):
        self.log.append(f"goto:{url}")
        return _FakeResponse(200)

    def locator(self, selector: str) -> _FakeAttributeLocator:
        if selector == WWW_CURRENT_USER:
            return self._one([_FakeElement(**{"display-name": self._account})], selector)
        if selector == "body":
            return self._one([_FakeElement()], selector)
        if selector == WWW_COMPOSER_HOST:
            host = self._one([_FakeElement()] if self._composer else [], selector)
            host._inner_locator = self._inside_composer  # type: ignore[method-assign]
            return host
        if selector == WWW_COMMENT:
            return self._one(list(self._comments), selector)
        if selector == WWW_COMPOSER_TRIGGER:
            trigger = self._one([_FakeElement()] if self._collapsed else [], selector)
            trigger.click = self._expand  # type: ignore[method-assign]
            return trigger
        raise AssertionError(f"aktuator pyta o nieznany selektor {selector!r}")

    def _inside_composer(self, selector: str) -> _FakeAttributeLocator:
        if selector == WWW_EDITOR_READY:
            return self._one([_FakeElement()], selector)
        if selector == WWW_INNER_TRIGGER:
            trigger = self._one([_FakeElement()], selector)
            trigger.click = self._expand  # type: ignore[method-assign]
            return trigger
        if selector == WWW_COMPOSER_TEXT:
            field = self._one([_FakeElement()], selector)
            field.is_visible = lambda: not self._collapsed  # type: ignore[method-assign]
            field.fill = self._fill  # type: ignore[method-assign]
            return field
        if selector == WWW_SUBMIT_BUTTON:
            button = self._one([_FakeElement()], selector)
            button.click = self._on_click  # type: ignore[method-assign]
            return button
        raise AssertionError(f"aktuator pyta kompozytor o nieznany selektor {selector!r}")

    def _expand(self, timeout: float | None = None) -> None:
        if self._lazy:
            self.log.append("load")
            self._lazy = False
            return
        self.log.append("expand")
        self._collapsed = False

    def _fill(self, text: str, timeout: float | None = None) -> None:
        if self._collapsed:
            raise _FakeTimeout("editor is collapsed")
        self.log.append(f"fill:{text}")

    def _on_click(self, timeout: float | None = None) -> None:
        self.log.append("click")
        self._comments.append(
            _FakeElement(author=self._account, thingid="t1_n1", permalink="/r/testsub/n1/")
        )

    def _one(self, elements: list[_FakeElement], selector: str) -> _FakeAttributeLocator:
        return _FakeAttributeLocator(elements, log=self.log, name=selector)

    def close(self) -> None:
        self.closed = True


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class _FakeContext:
    def __init__(self, page: _FakeWwwPage) -> None:
        self._page = page

    def new_page(self) -> _FakeWwwPage:
        return self._page


class _FakeBrowser:
    def __init__(self, page: _FakeWwwPage) -> None:
        self.contexts = [_FakeContext(page)]
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeDriver:
    def __init__(self, page: _FakeWwwPage) -> None:
        self.chromium = self
        self.browser = _FakeBrowser(page)

    def connect_over_cdp(self, endpoint: str, timeout: float | None = None) -> _FakeBrowser:
        return self.browser


def _actuator(page: _FakeWwwPage) -> CdpCommentActuator:
    driver = _FakeDriver(page)
    return CdpCommentActuator(
        "http://127.0.0.1:1",
        driver=lambda: driver,
        www_policy=lambda u: u,
    )


@pytest.mark.parametrize("entry", ["comment", "comment_www"])
def test_the_whole_www_path_runs_without_chrome_and_records_the_account_as_consent_names_it(
    entry,
) -> None:
    """KLEJ MIĘDZY CZĘŚCIAMI, a nie same części, i to jest powód istnienia tej atrapy.

    Dwie usterki z 21.09 przeżyły komplet zielonych testów, bo każda leżała dokładnie tutaj:
    odczyt dowodu pytał o selektor z nazwą konta małymi literami, a wynik zapisywał tę małą
    postać jako konto publikacji. Osobno wszystko było sprawdzone; razem nic.
    """
    page = _FakeWwwPage(account_on_page=ACCOUNT)

    outcome = getattr(_actuator(page), entry)(
        thread_url="https://www.reddit.com/r/testsub/comments/abc123/",
        parent_fullname="t3_abc123",
        text="Treść z atrapy.",
        account=ACCOUNT,
        timeout_s=5.0,
    )

    assert outcome.fullname == "t1_n1"
    assert outcome.account == ACCOUNT, "pod dowodem stoi nazwa konta ze zgody, nie jej odmiana"
    assert outcome.permalink == "https://www.reddit.com/r/testsub/n1/"
    assert "fill:Treść z atrapy." in page.log
    assert page.log.index("fill:Treść z atrapy.") < page.log.index("click"), (
        "najpierw treść, potem klik: odwrotna kolejność wysłałaby pusty komentarz"
    )
    assert page.closed, "kartę zamykamy zawsze, bo zostaje w Chrome kapsuły"


def test_the_earlier_comment_of_our_account_is_not_taken_for_the_new_one() -> None:
    """Gdyby aktuator liczył „jakikolwiek nasz komentarz", zameldowałby publikację natychmiast,
    pokazując jako dowód wpis sprzed tygodnia."""
    page = _FakeWwwPage(account_on_page=ACCOUNT)
    page._comments = [_FakeElement(author=ACCOUNT, thingid="t1_stary", permalink="/r/x/")]

    outcome = _actuator(page).comment_www(
        thread_url="https://www.reddit.com/r/testsub/comments/abc123/",
        parent_fullname="t3_abc123",
        text="Treść z atrapy.",
        account=ACCOUNT,
        timeout_s=5.0,
    )

    assert outcome.fullname == "t1_n1", "dowodem jest komentarz, którego przed kliknięciem nie było"


@pytest.mark.parametrize("entry", ["comment", "comment_www"])
def test_collapsed_editor_is_opened_before_text_and_submitted_once(entry) -> None:
    page = _FakeWwwPage(account_on_page=ACCOUNT, collapsed=True)
    result = getattr(_actuator(page), entry)(
        thread_url=page.url,
        parent_fullname="t3_abc123",
        text="Body",
        account=ACCOUNT,
        timeout_s=1,
    )
    assert result.fullname == "t1_n1"
    assert page.log[1:] == ["expand", "fill:Body", "click"]


def test_account_read_waits_instead_of_treating_initial_absence_as_logged_out() -> None:
    class ArrivingAccount(_FakeAttributeLocator):
        def count(self) -> int:
            return 0

        def get_attribute(self, name: str, timeout: float | None = None) -> str:
            assert name == "display-name"
            assert timeout == 5000
            return ACCOUNT

    locator = ArrivingAccount([], log=[], name=WWW_CURRENT_USER)
    assert _first_attribute(locator, "display-name", timeout_ms=5000) == ACCOUNT


def test_lazy_editor_is_loaded_then_opened_before_any_text_is_entered() -> None:
    page = _FakeWwwPage(account_on_page=ACCOUNT, collapsed=True, lazy=True)
    result = _actuator(page).comment_www(
        thread_url=page.url,
        parent_fullname="t3_abc123",
        text="Body",
        account=ACCOUNT,
        timeout_s=1,
    )
    assert result.fullname == "t1_n1"
    assert page.log[1:] == ["load", "expand", "fill:Body", "click"]


def test_unavailable_editor_never_reaches_text_or_submit() -> None:
    page = _FakeWwwPage(account_on_page=ACCOUNT, collapsed=True)

    def unavailable(timeout: float | None = None) -> None:
        raise _FakeTimeout("trigger unavailable")

    page._expand = unavailable
    with pytest.raises(CommentRefused) as failure:
        _actuator(page).comment_www(
            thread_url=page.url,
            parent_fullname="t3_abc123",
            text="Body",
            account=ACCOUNT,
            timeout_s=1,
        )
    assert failure.value.submit_started is False
    assert not any(item.startswith("fill:") or item == "click" for item in page.log)


@pytest.mark.parametrize("entry", ["comment", "comment_www"])
def test_a_page_of_another_account_is_blocked_before_anything_is_typed(entry) -> None:
    page = _FakeWwwPage(account_on_page="somebodyelse")

    with pytest.raises(CommentBlocked):
        getattr(_actuator(page), entry)(
            thread_url="https://www.reddit.com/r/testsub/comments/abc123/",
            parent_fullname="t3_abc123",
            text="Treść z atrapy.",
            account=ACCOUNT,
            timeout_s=5.0,
        )

    assert not [one for one in page.log if one.startswith("fill:")], "nic nie wpisano"
    assert "click" not in page.log
