"""Limit konta Codex: rozpoznanie i godzina odnowienia (D-045, zgłoszenie założyciela 03:24).

CO SIĘ WYDARZYŁO. Rozmowa w Studio skończyła się zdaniem „You've hit your usage limit. Upgrade to
Pro (...) or try again at 3:23 AM" i ekranem „Sesja jest zakończona. Zacznij nową". Limit odnowił
się o 3:23, a sesja była martwa i człowiek nie wiedział, co z tym zrobić. To NIE JEST awaria
produktu ani koniec pracy: to jest czekanie, a czekanie ma stan, godzinę i powrót.

DWA STOPNIE ROZPOZNANIA, i drugi jest ważniejszy. Sam tekst nie wystarcza: model potrafi
zacytować takie zdanie w odpowiedzi („dostałem wczoraj komunikat o limicie"), a sesja nie może
iść w oczekiwanie przez cytat. Dlatego znacznik w tekście tylko PYTA, a odpowiada silnik przez
`account/rateLimits/read`. Gdy odczyt się nie uda (brak zalogowanego konta, stary app-server),
decyduje tekst, bo wtedy jedyną alternatywą jest uznanie limitu za awarię, a to jest gorsze.

GODZINA ODNOWIENIA, W TEJ KOLEJNOŚCI: z danych silnika (`resets_at`, `reset_at`, „za ile sekund"),
potem z samego komunikatu („try again at 3:23 AM"), a gdy nie ma ani jednego, próba za piętnaście
minut. Nigdy nie zgadujemy „pewnie za godzinę": każdy z tych trzech przypadków mówi na ekranie co
innego, bo co innego znaczy dla człowieka.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

#: Zwroty, po których PODEJRZEWAMY limit konta. Lista jest zamknięta i celowo wąska: każdy z tych
#: zwrotów pochodzi z komunikatu app-servera albo platformy, a nie z języka, którym pisze model.
MARKERS: tuple[str, ...] = (
    "usage limit",
    "rate limit",
    "rate-limited",
    "try again at",
    "upgrade to pro",
    "quota exceeded",
)

#: Gdy nie wiadomo, kiedy limit się odnowi. Piętnaście minut, bo tyle kosztuje pomyłka w drugą
#: stronę: jedno niepotrzebne zapytanie do silnika, a nie godzina martwej sesji.
BLIND_RETRY = timedelta(minutes=15)

#: Jak daleko w przód wolno zaplanować powrót. Limit konta odnawia się w godzinach, najwyżej w
#: dniach; termin dalszy niż tydzień nie jest limitem, tylko błędem przeliczenia.
#:
#: ZMIERZONE NA ŻYWO 20.09, i to jest powód istnienia tej stałej: `account/rateLimits/read`
#: zwrócił liczbę, którą ten moduł potraktował jako „za ile sekund", a była EPOKĄ. Dodanie jej do
#: teraz dało 15 czerwca 2083, więc rozmowa czekająca na limit miała wrócić za pięćdziesiąt sześć
#: lat. Tekst silnika mówił w tym samym czasie „try again at 8:33 AM".
MAX_AHEAD = timedelta(days=7)

#: Najdalej, jak wolno uwierzyć oknu, którego DŁUGOŚĆ silnik podał sam (`windowDurationMins`).
#: ZMIERZONE NA ŻYWO 28-29.09: konto na planie darmowym ma okno 30 dni (43200 minut), a jego
#: odnowienie za 28 dni było prawdą, którą `MAX_AHEAD` odrzucał jako pomyłkę; człowiek przez dobę
#: czytał „nie wiem, kiedy się odnowi”, choć silnik to wiedział. Długość okna plus doba zapasu,
#: najwyżej tyle; bez podanej długości zostaje tydzień.
MAX_WINDOW_AHEAD = timedelta(days=45)

#: Od tej odległości odnowienia czekanie jest DŁUGIE (dni, tygodnie): zwykłe okno pięciogodzinne
#: czeka się do końca, a przy długim sprawdzamy limit co `LONG_WAIT_CHECK`, bo człowiek może użyć
#: resetu limitu albo zmienić plan konta, i wtedy praca ma ruszyć od razu.
LONG_WAIT_FROM = timedelta(hours=24)
LONG_WAIT_CHECK = timedelta(hours=3)

#: Od tej wartości liczba przestaje być „za ile", a zaczyna być „kiedy". 1e9 sekund to rok 2001,
#: a żaden limit nie odnawia się za trzydzieści lat; 1e12 milisekund to ten sam moment w innej
#: jednostce. Rozpoznanie po WIELKOŚCI, a nie po nazwie pola, bo nazwa bywa myląca
#: (`reset_at_seconds` to epoka, `resets_in_seconds` to odstęp) i to na niej się przewróciliśmy.
EPOCH_SECONDS_FROM = 1_000_000_000
EPOCH_MILLISECONDS_FROM = 1_000_000_000_000

#: „try again at 3:23 AM", „try again at 15:05". Godzina z komunikatu jest OSTATNIĄ deską ratunku,
#: bo nie niesie strefy: przyjmujemy strefę serwera, co przy różnicy stref może dać pomyłkę o
#: godziny. Dlatego dane z silnika idą pierwsze, a ta ścieżka mówi o sobie w polu `source`.
_AT_TIME = re.compile(r"try again at\s+(\d{1,2}):(\d{2})\s*(am|pm)?", re.IGNORECASE)


@dataclass(frozen=True)
class RateLimit:
    """Że limit jest, kiedy mija i skąd to wiemy."""

    resets_at: datetime
    source: str
    raw: str
    #: Plan konta z odczytu limitów (`planType`), gdy silnik go podał.
    plan: str | None = None
    #: Ile darmowych resetów limitu czeka na koncie (`rateLimitResetCredits.availableCount`).
    resets_available: int = 0
    #: Kiedy sprawdzimy znowu, gdy odnowienie jest dalej niż `LONG_WAIT_CHECK`; inaczej `None`.
    check_at: datetime | None = None

    @property
    def minutes(self) -> int:
        return max(0, int((self.resets_at - datetime.now(UTC)).total_seconds() // 60))


@dataclass(frozen=True)
class AccountFacts:
    """Co odczyt limitów mówi o samym koncie, poza godziną: plan i dostępne resety."""

    plan: str | None
    resets_available: int


def account_facts(limits: object) -> AccountFacts:
    """Plan konta i liczba dostępnych resetów limitu z odpowiedzi `account/rateLimits/read`."""
    if not isinstance(limits, dict):
        return AccountFacts(plan=None, resets_available=0)
    root = limits.get("rateLimits")
    plan = root.get("planType") if isinstance(root, dict) else None
    credits = limits.get("rateLimitResetCredits")
    count = credits.get("availableCount") if isinstance(credits, dict) else None
    available = count if isinstance(count, int) and not isinstance(count, bool) else 0
    return AccountFacts(
        plan=plan if isinstance(plan, str) else None, resets_available=max(0, available)
    )


def looks_like_a_limit(text: str | None) -> bool:
    """Czy w tym tekście w ogóle jest o co pytać silnik. Sam w sobie NICZEGO nie rozstrzyga."""
    lowered = (text or "").lower()
    return any(marker in lowered for marker in MARKERS)


def is_plausible(
    when: datetime | None, *, now: datetime | None = None, ahead: timedelta = MAX_AHEAD
) -> bool:
    """Czy ten termin w ogóle może być odnowieniem limitu.

    Dwie granice, obie z rzeczywistości: termin w przeszłości znaczy, że limit już minął, a więc
    nie ma na co czekać; termin dalszy niż `MAX_AHEAD` nie jest limitem konta, tylko pomyłką w
    jednostce albo w epoce. Sesja, która dostanie taki termin, nie wróci nigdy, a wygląda na
    czekającą, i to jest najgorszy z możliwych stanów: cichy.
    """
    if when is None:
        return False
    moment = now or datetime.now(UTC)
    return moment - timedelta(minutes=1) <= when <= moment + ahead


def _seconds_of(name: str, value: float) -> float | None:
    """Ile sekund ODSTĘPU znaczy ta liczba, gdy nie jest epoką. `None` = nie wiem."""
    if "minute" in name:
        return value * 60.0
    if "millisecond" in name or name.endswith("_ms") or "_ms_" in name:
        return value / 1000.0
    if "second" in name or name.endswith("_s") or "_in" in name:
        return value
    return None


#: Minimalny odstęp do ponowienia. ZMIERZONE NA ŻYWO 20.09: rozmowa wznowiła się o 06:33:10,
#: trafiła w limit o 06:33:14, a silnik podał odnowienie na 06:33:16, czyli dwie sekundy do
#: przodu. Bez tego odstępu sesja weszłaby w pętlę prób co kilka sekund, a każda z nich kosztuje
#: start app-servera i jedno pytanie do platformy.
MIN_GAP = timedelta(minutes=2)

#: Sufit odstępu przy kolejnych nieudanych próbach. Godzina, bo dłuższe czekanie przy limicie
#: pięciogodzinnego okna zaczyna kosztować więcej niż jedna próba.
MAX_GAP = timedelta(hours=1)


def backoff(attempt: int) -> timedelta:
    """Ile najmniej czekać przy N-tej z rzędu próbie, która trafiła w limit.

    Podwajanie od `MIN_GAP`, do sufitu. Pierwsza próba po limicie dostaje dwie minuty, bo
    odnowienie podane co do sekundy bywa o sekundy do przodu i ponowienie „dokładnie wtedy"
    trafia w ten sam limit.
    """
    steps = max(0, attempt - 1)
    grown = MIN_GAP * (2**steps)
    return min(grown, MAX_GAP)


def _windows(limits: object) -> list[tuple[float, float, float]]:
    """Okna limitu z odpowiedzi w ZNANYM kształcie: trójki (resetsAt, usedPercent, minuty okna).

    Kształt zmierzony 20.09 na przypiętej binarce (`output/ratelimits-2026-09-20.json`):
    `rateLimits.primary` i `rateLimits.secondary`, każde z `resetsAt` (epoka w sekundach),
    `usedPercent` i `windowDurationMins`. Pusta lista znaczy „to nie jest ten kształt".
    """
    root = limits.get("rateLimits") if isinstance(limits, dict) else None
    source = root if isinstance(root, dict) else limits
    if not isinstance(source, dict):
        return []
    found: list[tuple[float, float, float]] = []
    for name in ("primary", "secondary"):
        window = source.get(name)
        if not isinstance(window, dict):
            continue
        when = window.get("resetsAt")
        used = window.get("usedPercent")
        span = window.get("windowDurationMins")
        if isinstance(when, int | float) and not isinstance(when, bool):
            found.append(
                (
                    float(when),
                    float(used) if isinstance(used, int | float) else 0.0,
                    float(span)
                    if isinstance(span, int | float) and not isinstance(span, bool)
                    else 0.0,
                )
            )
    return found


def _window_ahead(minutes: float) -> timedelta:
    """Jak daleko wolno uwierzyć odnowieniu okna o podanej długości: długość plus doba, w
    granicach od `MAX_AHEAD` do `MAX_WINDOW_AHEAD`."""
    if minutes <= 0:
        return MAX_AHEAD
    return min(MAX_WINDOW_AHEAD, max(MAX_AHEAD, timedelta(minutes=minutes) + timedelta(days=1)))


def resets_from_engine(limits: object, *, now: datetime | None = None) -> datetime | None:
    """Godzina odnowienia z odpowiedzi `account/rateLimits/read`, albo `None`.

    Kształt tej odpowiedzi NIE JEST przypięty kontraktem, bo nie ma jej w publicznym SDK, więc
    czytamy defensywnie. Liczbę rozpoznajemy PO WIELKOŚCI, nie po nazwie: wartość rzędu miliarda
    jest epoką w sekundach, rzędu biliona epoką w milisekundach, a mniejsza odstępem, którego
    jednostkę podpowiada nazwa. Poprzednia wersja pytała tylko o nazwę i dodała epokę do teraz,
    co dało rok 2083 i rozmowę, która nie wróciłaby nigdy.

    Termin niewiarygodny (w przeszłości albo dalej niż tydzień) jest ODRZUCANY, bo „silnik nie
    powiedział" jest prawdziwsze niż godzina, w którą nikt nie wierzy, a wołający ma po tym
    tekst komunikatu i piętnaście minut na ślepo.
    """
    moment = now or datetime.now(UTC)

    # ZNANY KSZTAŁT PIERWSZY. Wyczerpane jest okno z `usedPercent` co najmniej 100 i to ono
    # blokuje pracę; gdy wyczerpane są oba, odblokowanie przychodzi z PÓŹNIEJSZYM z nich, bo
    # tygodniowe okno nie przestaje obowiązywać dlatego, że minęło pięciogodzinne. Gdy żadne nie
    # jest wyczerpane, bierzemy najwcześniejsze wiarygodne: limit jest faktem z komunikatu, a my
    # szukamy tylko godziny.
    windows = _windows(limits)
    if windows:
        exhausted = [(when, span) for when, used, span in windows if used >= 100.0]
        candidates = sorted(exhausted, reverse=True) or sorted(
            (when, span) for when, _, span in windows
        )
        for epoch, span in candidates:
            when = _from_epoch(epoch)
            if is_plausible(when, now=moment, ahead=_window_ahead(span)):
                return when
        return None

    for key, value in _walk(limits):
        name = key.lower()
        if isinstance(value, str) and ("reset" in name or "renew" in name):
            when = _parse_iso(value)
            if is_plausible(when, now=moment):
                return when
            continue
        if isinstance(value, int | float) and not isinstance(value, bool):
            if "reset" not in name and "renew" not in name:
                continue
            number = float(value)
            if number >= EPOCH_SECONDS_FROM:
                when = moment_from_epoch(number)
            else:
                seconds = _seconds_of(name, number)
                when = None if seconds is None else moment + timedelta(seconds=seconds)
            if is_plausible(when, now=moment):
                return when
    return None


def moment_from_epoch(value: object) -> datetime | None:
    """Liczba z odczytu limitów jako chwila w UTC, albo `None`, gdy to nie jest epoka.

    JEDNO MIEJSCE NA TO ROZPOZNANIE. Ta sama liczba jedzie dwiema drogami: do planowania powrotu
    po limicie i na ekran połączeń Codex. Gdy każda z nich czytała ją po swojemu, jedna zaplanowała
    powrót na rok 2083 (D-045), a druga wysłała ekranowi liczbę tam, gdzie schemat dashboardu
    wymaga napisu ISO, i ekran napisał „to zepsuty odczyt" o połączeniu, które działało.

    Rozpoznanie po RZĘDZIE WIELKOŚCI, nie po nazwie pola: sekundy od 2001 roku w górę, milisekundy
    od tysiąca razy tyle. Wszystko poniżej nie jest epoką i nie zgadujemy, czym jest.
    """
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    number = float(value)
    if number >= EPOCH_MILLISECONDS_FROM:
        return _from_epoch(number / 1000.0)
    if number >= EPOCH_SECONDS_FROM:
        return _from_epoch(number)
    return None


def _from_epoch(seconds: float) -> datetime | None:
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def resets_from_text(text: str | None, *, now: datetime | None = None) -> datetime | None:
    """Godzina z komunikatu („try again at 3:23 AM"), w strefie serwera, albo `None`.

    Gdy podana godzina już minęła, znaczy to następną dobę: komunikat o 23:50 mówiący „try again
    at 0:10" dotyczy dziesięciu minut, a nie prawie doby wstecz.
    """
    match = _AT_TIME.search(text or "")
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    suffix = (match.group(3) or "").lower()
    if suffix == "pm" and hour < 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    moment = (now or datetime.now(UTC)).astimezone()
    candidate = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= moment:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def sentence(limit: RateLimit) -> str:
    """Co człowiek ma przeczytać w rozmowie. Bez „upgrade", bez nazw pól, z godziną i obietnicą.

    Obietnica jest tu najważniejsza: „dokończę tę turę sam" znaczy, że człowiek może zamknąć
    okno. Poprzednia wersja tego ekranu kazała mu zacząć nową rozmowę, co było nieprawdą i
    kosztowało go kontekst tamtej.
    """
    when = limit.resets_at.astimezone().strftime("%H:%M")
    if limit.source == "blind":
        return (
            "Konto Codex doszło do limitu. Nie wiem dokładnie, kiedy się odnowi, więc spróbuję "
            f"ponownie około {when} i będę próbował dalej. Możesz zamknąć okno."
        )
    if limit.check_at is not None and limit.check_at < limit.resets_at:
        # DŁUGIE CZEKANIE (28-29.09: plan darmowy, okno 30 dni). Data, nie sama godzina, i co
        # może skrócić czekanie, bez namawiania do zakupu: to decyzja człowieka, nie ekranu.
        day = limit.resets_at.astimezone().strftime("%d.%m o %H:%M")
        plan = " na planie darmowym" if limit.plan == "free" else ""
        reset = (
            f" Na koncie czeka darmowy reset limitu (ustawienia Codex na chatgpt.com): "
            f"{limit.resets_available}."
            if limit.resets_available
            else ""
        )
        hours = int(LONG_WAIT_CHECK.total_seconds() // 3600)
        return (
            f"Konto Codex doszło do limitu{plan}; odnowi się {day}.{reset} Co {hours} godziny "
            "sprawdzam, czy limit nie został zdjęty wcześniej, i wtedy wracam sam. Możesz "
            "zamknąć okno."
        )
    return (
        f"Konto Codex doszło do limitu. Odnowi się o {when}; wtedy dokończę tę turę sam. "
        "Możesz zamknąć okno."
    )


def _walk(value: object, prefix: str = "") -> list[tuple[str, object]]:
    """Spłaszczona odpowiedź silnika: pary (nazwa, wartość), także z zagnieżdżeń."""
    out: list[tuple[str, object]] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            name = f"{prefix}_{key}" if prefix else str(key)
            out.append((name, inner))
            out.extend(_walk(inner, name))
    elif isinstance(value, list):
        for item in value:
            out.extend(_walk(item, prefix))
    return out


def _parse_iso(value: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)
