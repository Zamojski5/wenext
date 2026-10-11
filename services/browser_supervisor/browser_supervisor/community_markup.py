"""Co stoi w ZNACZNIKU strony społeczności: same fakty, żadnego werdyktu (E3b).

CZYSTA CZĘŚĆ SONDY, BEZ JEDNEJ ZALEŻNOŚCI. Ten moduł nie importuje nic poza biblioteką
standardową, i to jest jego cała racja bytu: ta sama funkcja ma działać w kontenerze kapsuły,
w teście pakietu `browser_supervisor` i w teście strony fixture, która o kapsule nic nie wie i
nie ma jej czym zainstalować. Sonda z Playwrightem siedzi obok, w `community_probe.py`.

CO TO ZNACZY „bez werdyktu". Funkcja zwraca KANDYDATÓW z ich klasami, tekstem i adresami, a
wybór, który z nich jest przyciskiem, należy do człowieka czytającego artefakt. Sonda, która
sama orzeka „to jest ten przycisk", przeniosłaby zgadywanie selektorów z kodu aktuatora do kodu
sondy, a artefakt wyglądałby na pomiar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

#: Słowa, po których element jest KANDYDATEM na przycisk dołączenia. Dopasowanie po klasie, id,
#: adresie i tekście, bez rozstrzygania: `subscribe` to słowo starego Reddita, `join` nowego,
#: polskie formy są tu dlatego, że ta sama sonda ma sens na stronie stub i na fixture.
JOIN_WORDS = ("subscribe", "unsubscribe", "join", "leave", "dolacz", "dołącz", "opusc", "opuść")

#: Słowa, po których element jest kandydatem na LICZNIK członków. Licznik jest poszlaką, nie
#: dowodem członkostwa: bywa buforowany i nie mówi, czy TO konto należy do społeczności.
#: `readers` jest tu, bo tak liczbę nazywa STARY Reddit, i bez tego słowa sonda oddałaby pustą
#: listę o stronie, na której licznik stoi na wierzchu.
COUNT_WORDS = ("subscriber", "member", "reader", "czytelnic", "członk", "czlonk")

#: Słowa w ADRESIE FORMULARZA, po których uznajemy go za drogę zapisu do społeczności. Osobna
#: lista od `JOIN_WORDS`, bo formularze nazywa się rzeczownikiem, nie czasownikiem: stary Reddit
#: ma `/api/subscribe`, a strona fixture tego repozytorium `/c/<nazwa>/membership`. Zmierzone na
#: renderowanej stronie fixture, nie wymyślone: przy samej liście czasowników sonda nie widziała
#: tam żadnego formularza.
FORM_WORDS = (
    *("subscribe", "unsubscribe", "join", "leave"),
    *("membership", "subscription", "czlonkostwo", "członkostwo"),
)

#: Znaczniki, których TEKST bierzemy pod uwagę przy szukaniu przycisku. Etykieta bywa jedynym
#: śladem znaczenia: nowy Reddit nazywa przycisk klasą bez sensu, a napis „Join" trzyma w
#: `<span>` w środku. Akapitów i sekcji tu nie ma celowo: pasowałby każdy rodzic aż do `<body>`,
#: a sonda zwracałaby całą stronę jako kandydata.
TEXTUAL_TAGS = ("a", "button", "span", "input", "label")

#: Najdłuższy tekst, który jeszcze uznajemy za ETYKIETĘ. Zdanie o dołączaniu w akapicie nie jest
#: przyciskiem, a bez tego progu byłoby nim każde wystąpienie słowa na stronie.
LABEL_CHARS = 40

#: Atrybuty, w których szukamy słów. Nie tylko klasa i adres: nowe interfejsy niosą znaczenie w
#: `data-testid` i `aria-label`, a klasę mają wygenerowaną i nic nieznaczącą.
MEANING_ATTRS = ("class", "id", "href", "action", "data-testid", "aria-label", "title", "name")

#: Gdzie Reddit trzyma nazwę zalogowanego konta. DWA ŚWIATY, bo to dwa różne interfejsy: stary
#: ma `#header-bottom-right span.user a`, na którym stoi aktuator komentarzy, a nowy trzyma to
#: w `user-drawer-button` i w powiązanych z nim elementach. Sonda z 21.09 nie rozpoznała konta
#: na stronie WĄTKU na www, choć w HTML stała nazwa konta: szukała wyłącznie starego selektora.
#: Pytanie „czy to nasze konto" jest dla aktuatora warunkiem twardym, więc sonda ma umieć na nie
#: odpowiedzieć na obu interfejsach, zamiast milczeć o jednym z nich.
ACCOUNT_MARKERS = ("header-bottom-right", "user-drawer-button", "user-dropdown")

#: Ile znaków tekstu elementu zapisujemy. Tyle, żeby dało się poznać etykietę, i nie więcej: w
#: artefakcie nie ma powodu trzymać cudzej treści.
TEXT_CHARS = 120

_NUMBER_NEAR_WORD = re.compile(
    r"([\d][\d\s.,kKmM]{0,14})\s*(subscribers?|members?|readers?|czytelnik\w*|członk\w*)",
    re.IGNORECASE,
)


@dataclass
class Element:
    """Jeden element kandydujący, tak jak stoi w HTML."""

    tag: str
    classes: str = ""
    element_id: str = ""
    href: str = ""
    action: str = ""
    text: str = ""
    #: Po czym uznaliśmy go za kandydata: `klasa`, `id`, `adres` albo `tekst`.
    matched: str = ""


@dataclass
class Candidates:
    """Wyciąg z jednej strony. Same fakty ze znacznika, żadnego werdyktu."""

    join_like: list[Element] = field(default_factory=list)
    subscribe_forms: list[Element] = field(default_factory=list)
    account_header: list[Element] = field(default_factory=list)
    member_counts: list[str] = field(default_factory=list)
    html_bytes: int = 0


class _Collector(HTMLParser):
    """Zbiera elementy, które mogą być przyciskiem, formularzem zapisu albo nagłówkiem konta.

    STDLIB, NIE BIBLIOTEKA. Ta sama klasa ma działać w kontenerze kapsuły i w teście na stronie
    stub, bez dokładania zależności do obrazu, w którym liczy się każdy megabajt.

    Tekst elementu zbieramy DO JEGO ZAMKNIĘCIA, także z zagnieżdżonych znaczników: stary Reddit
    trzyma etykietę w `<a>` wewnątrz `<div class="fancy-toggle-button">`, a nowy w `<span>`
    wewnątrz `<button>`, więc patrzenie tylko na tekst bezpośredni gubiłoby połowę przypadków.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[Element] = []
        self.forms: list[Element] = []
        self.account: list[Element] = []
        self._open: list[tuple[str, Element | None, list[str], dict[str, str]]] = []
        self._body: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: (value or "") for name, value in attrs}
        classes = values.get("class", "")
        element_id = values.get("id", "")
        href = values.get("href", "")
        action = values.get("action", "")
        haystack = " ".join(values.get(name, "") for name in MEANING_ATTRS).lower()

        element: Element | None = None
        matched = ""
        if any(word in haystack for word in JOIN_WORDS):
            matched = "klasa/id/adres"
        if tag == "form" and any(word in action.lower() for word in FORM_WORDS):
            matched = matched or "adres formularza"
        if any(marker in haystack for marker in ACCOUNT_MARKERS):
            matched = matched or "nagłówek konta"
        if matched:
            element = Element(
                tag=tag,
                classes=classes,
                element_id=element_id,
                href=href,
                action=action,
                matched=matched,
            )
        self._open.append((tag, element, [], values))

    def handle_endtag(self, tag: str) -> None:
        while self._open:
            open_tag, element, chunks, values = self._open.pop()
            text = " ".join(" ".join(chunks).split())[:TEXT_CHARS]
            # ELEMENT ROZPOZNANY PO SAMEJ ETYKIECIE. Stary Reddit trzyma dwie opcje przełącznika
            # w `<a class="option add">`, gdzie ani klasa, ani `href="#"` nic nie mówią, a cała
            # treść jest w napisie „subscribe". Bez tej ścieżki sonda oddałaby sam kontener i nie
            # byłoby po czym poznać stanu.
            if (
                element is None
                and open_tag in TEXTUAL_TAGS
                and len(text) <= LABEL_CHARS
                and any(word in text.lower() for word in JOIN_WORDS)
            ):
                # ATRYBUTY JADĄ RAZEM Z ETYKIETĄ. To po klasie `active` poznaje się, która
                # opcja przełącznika jest teraz pokazana, więc element bez klas byłby
                # znaleziskiem bez treści.
                element = Element(
                    tag=open_tag,
                    classes=values.get("class", ""),
                    element_id=values.get("id", ""),
                    href=values.get("href", ""),
                    action=values.get("action", ""),
                    matched="tekst",
                )
            if element is not None:
                element.text = text
                if element.matched == "nagłówek konta":
                    self.account.append(element)
                elif element.tag == "form" and any(
                    word in element.action.lower() for word in FORM_WORDS
                ):
                    self.forms.append(element)
                else:
                    self.found.append(element)
            if open_tag == tag:
                break

    def handle_data(self, data: str) -> None:
        self._body.append(data)
        for _tag, _element, chunks, _values in self._open:
            chunks.append(data)

    @property
    def body_text(self) -> str:
        return " ".join(" ".join(self._body).split())


def candidates(html: str) -> Candidates:
    """Kandydaci ze strony, bez orzekania, który z nich jest przyciskiem.

    Tekst elementu bierzemy pod uwagę OSOBNO od klasy i adresu, bo nowy Reddit nazywa przycisk
    klasą bez słowa `join`, a etykietę trzyma w środku. Element wpadający z obu powodów naraz
    pojawia się raz: pytanie brzmi „co tu jest", nie „ile razy pasuje".
    """
    collector = _Collector()
    collector.feed(html or "")
    collector.close()

    by_text: list[Element] = []
    for element in collector.found:
        if not element.matched:
            continue
        if element.matched != "tekst" and any(word in element.text.lower() for word in JOIN_WORDS):
            element.matched = f"{element.matched} + tekst"
        by_text.append(element)

    counts = [
        " ".join(match.group(0).split())[:60]
        for match in _NUMBER_NEAR_WORD.finditer(collector.body_text)
    ]
    return Candidates(
        join_like=by_text,
        subscribe_forms=collector.forms,
        account_header=collector.account,
        member_counts=counts[:10],
        html_bytes=len(html or ""),
    )


#: Dwa hosty, bo dziś to one są pytaniem. Aktuator komentarzy pisze na `old.reddit.com` (prosty
#: HTML), a czytnik ma w allowliście WYŁĄCZNIE `www.reddit.com` i działa. Pierwszy przebieg sondy
#: 21.09 dostał z `old.reddit.com` status 403 i stronę „You've been blocked / network security",
#: identyczną dla dwóch różnych społeczności. Sonda mierzy więc OBA i zapisuje, który odpowiada:
#: to jest fakt o dostępności, a nie o markupie, i ma prawo być różny dla różnych hostów.
COMMUNITY_HOSTS = ("www.reddit.com", "old.reddit.com")


def community_name(slug: str) -> str:
    """Sama nazwa społeczności, sprawdzona. Nazwa, nie adres, i składanie po naszej stronie: ta
    sama zasada, co w `writer.authorize_submit_url`, dzięki której nie ma czego przemycić."""
    name = (slug or "").strip().strip("/")
    for prefix in ("r/", "/r/"):
        if name.lower().startswith(prefix):
            name = name[len(prefix) :]
    name = name.strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_]{2,40}", name):
        raise ValueError(f"nazwa społeczności nie jest nazwą subreddita: {slug!r}")
    return name


#: Kształty ścieżek, które sonda w ogóle wolno jej otworzyć. Strona społeczności i strona wątku,
#: nic więcej: sonda mierzy DOSTĘPNOŚĆ dwóch miejsc, a nie chodzi po platformie.
_PAGE_PATHS = (
    re.compile(r"^/r/[A-Za-z0-9_]{2,21}/?$"),
    re.compile(r"^/r/[A-Za-z0-9_]{2,21}/comments/[a-z0-9]{1,12}(/[A-Za-z0-9_%-]*)?/?$"),
)


def authorize_page_url(url: str) -> str:
    """Adres, który sonda wolno otworzyć, albo `ValueError` ze zdaniem.

    Ta sama zasada, co w allowliście czytnika i w politykach aktuatora: zamknięta lista hostów i
    zamknięta lista kształtów ścieżek. Sonda dostaje adres od CZŁOWIEKA, nie składa go z nazwy,
    więc sprawdzenie musi być tu; bez niego `--probe-url` byłoby przeglądarką na czyjeś życzenie.
    """
    from urllib.parse import urlsplit

    parts = urlsplit((url or "").strip())
    if parts.scheme != "https":
        raise ValueError(f"tylko https, nie {parts.scheme!r}")
    if parts.hostname not in COMMUNITY_HOSTS:
        raise ValueError(f"host spoza listy: {parts.hostname!r}")
    if parts.username or parts.password or parts.port:
        raise ValueError("adres z poświadczeniami albo portem nie przechodzi")
    if parts.query or parts.fragment:
        raise ValueError("adres z zapytaniem albo kotwicą nie przechodzi")
    if not any(shape.match(parts.path) for shape in _PAGE_PATHS):
        raise ValueError(f"ścieżka poza listą kształtów: {parts.path!r}")
    return f"https://{parts.hostname}{parts.path}"


def community_url(slug: str, *, host: str = "old.reddit.com") -> str:
    """Adres strony społeczności na wskazanym hoście, składany z samej nazwy."""
    if host not in COMMUNITY_HOSTS:
        raise ValueError(f"host spoza listy: {host!r}")
    return f"https://{host}/r/{community_name(slug)}/"
