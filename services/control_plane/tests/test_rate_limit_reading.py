"""Limit konta Codex: co uznajemy za limit i kiedy się odnowi (zgłoszenie założyciela, 03:24).

TO NIE JEST AWARIA, TYLKO CZEKANIE. Rozmowa skończyła się zdaniem „You've hit your usage limit
(...) try again at 3:23 AM" i ekranem „Sesja jest zakończona. Zacznij nową", chociaż limit
odnawiał się kilka minut później. Te testy pilnują trzech rzeczy: czego NIE uznajemy za limit,
skąd bierzemy godzinę i co z tego widzi człowiek.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from control_plane.sessions.rate_limit import (
    BLIND_RETRY,
    RateLimit,
    backoff,
    is_plausible,
    looks_like_a_limit,
    resets_from_engine,
    resets_from_text,
    sentence,
)

NOW = datetime(2026, 9, 20, 1, 10, tzinfo=UTC)
REAL = "You've hit your usage limit. Upgrade to Pro (…) or try again at 3:23 AM."


def test_the_real_message_is_recognised() -> None:
    assert looks_like_a_limit(REAL)


def test_an_ordinary_answer_is_not_a_limit() -> None:
    """Znacznik w tekście tylko KAŻE ZAPYTAĆ silnik i sam z siebie nic nie rozstrzyga, ale i tak
    nie może łapać zwykłych zdań: sesja idąca w oczekiwanie przez cytat jest gorsza niż limit."""
    for text in (
        "",
        None,
        "Napisałem trzy propozycje odpowiedzi.",
        "Zaproponowałem, żeby ograniczyć liczbę postów dziennie.",
    ):
        assert not looks_like_a_limit(text), text


def test_the_engine_answer_wins_and_unknown_shapes_give_nothing() -> None:
    """Kształt odpowiedzi `account/rateLimits/read` nie jest przypięty kontraktem, więc czytamy
    defensywnie: znany klucz albo `None`, nigdy wymyślona godzina.

    Każde wywołanie podaje `now` WPROST. Test parsowania, który pyta o zegar ścienny, mierzy
    porę doby, w której go uruchomiono; ten sam test bez `now` zaczął padać o poranku, bo data
    z asercji zrobiła się przeszłością.
    """
    assert resets_from_engine(
        {"primary": {"resets_at": "2026-09-20T03:23:00Z"}},
        now=datetime(2026, 9, 20, 2, 0, tzinfo=UTC),
    ) == datetime(2026, 9, 20, 3, 23, tzinfo=UTC)
    assert resets_from_engine({"secondary": {"reset_in_seconds": 900}}, now=NOW) == NOW + timedelta(
        minutes=15
    )
    assert resets_from_engine({"nieznany": "kształt"}, now=NOW) is None
    assert resets_from_engine(None, now=NOW) is None
    assert resets_from_engine({"primary": {"resets_at": "nie-data"}}, now=NOW) is None


def test_the_hour_from_the_message_is_read_and_never_lands_in_the_past() -> None:
    """Komunikat o 23:50 mówiący „try again at 0:10" dotyczy dziesięciu minut, nie doby wstecz."""
    late = datetime(2026, 9, 20, 21, 50, tzinfo=UTC)
    when = resets_from_text("try again at 0:10 AM", now=late)
    assert when is not None and when > late
    assert (when - late) < timedelta(hours=6)
    assert resets_from_text("bez godziny") is None
    assert resets_from_text("try again at 99:99") is None


def test_the_sentence_says_whose_limit_it_is_when_it_ends_and_that_nothing_is_needed() -> None:
    limit = RateLimit(resets_at=NOW + timedelta(minutes=13), source="engine", raw=REAL)
    said = sentence(limit)

    assert "Konto Codex doszło do limitu" in said
    assert "dokończę tę turę sam" in said
    assert "Możesz zamknąć okno" in said
    # Ani słowa o kupowaniu czegokolwiek i żadnej nazwy pola.
    assert "Upgrade" not in said and "usage limit" not in said


def test_without_a_known_hour_the_sentence_admits_it_instead_of_inventing_one() -> None:
    blind = RateLimit(resets_at=NOW + BLIND_RETRY, source="blind", raw=REAL)
    said = sentence(blind)
    assert "Nie wiem dokładnie, kiedy się odnowi" in said
    assert "będę próbował dalej" in said


def test_a_number_that_is_an_epoch_is_not_treated_as_an_offset() -> None:
    """USTERKA ZMIERZONA NA ŻYWO 20.09, i to jest jej regresja.

    `account/rateLimits/read` zwrócił liczbę rzędu miliarda, czyli EPOKĘ w sekundach, a moduł
    potraktował ją jako „za ile sekund" i dodał do teraz. Wyszedł 15 czerwca 2083: rozmowa
    czekająca na limit miała wrócić za pięćdziesiąt sześć lat, wyglądając przez cały ten czas na
    czekającą. Rozpoznanie idzie teraz po WIELKOŚCI liczby, nie po nazwie pola.
    """
    soon = NOW + timedelta(hours=1)
    as_seconds = {"primary": {"reset_in_seconds": int(soon.timestamp())}}
    as_milliseconds = {"primary": {"reset_in_seconds": int(soon.timestamp() * 1000)}}

    assert resets_from_engine(as_seconds, now=NOW) == soon.replace(microsecond=0)
    assert resets_from_engine(as_milliseconds, now=NOW) == soon.replace(microsecond=0)


def test_a_reset_further_than_a_week_is_refused_instead_of_believed() -> None:
    """Limit konta odnawia się w godzinach. Termin za rok nie jest limitem, tylko pomyłką, a
    wołający ma po tym tekst komunikatu i piętnaście minut na ślepo."""
    year_ahead = NOW + timedelta(days=365)

    assert resets_from_engine({"primary": {"resets_at": year_ahead.isoformat()}}, now=NOW) is None
    assert resets_from_engine({"primary": {"reset_in_seconds": 400 * 24 * 3600}}, now=NOW) is None
    assert is_plausible(NOW + timedelta(hours=2), now=NOW) is True
    assert is_plausible(NOW - timedelta(hours=2), now=NOW) is False


def test_minutes_and_milliseconds_are_read_by_their_own_names() -> None:
    assert resets_from_engine({"primary": {"reset_in_minutes": 30}}, now=NOW) == NOW + timedelta(
        minutes=30
    )
    assert resets_from_engine(
        {"primary": {"reset_in_milliseconds": 90_000}}, now=NOW
    ) == NOW + timedelta(seconds=90)


#: KSZTAŁT ZMIERZONY NA ŻYWYM SILNIKU 20.09 (`output/ratelimits-2026-09-20.json`, przypięta
#: binarka 0.147.0). Wpisany tu w całości, bo `output/` nie jest śledzone, a test ma być
#: samowystarczalny. `primary` to okno pięciogodzinne (wyczerpane), `secondary` tygodniowe.
REAL_SHAPE = {
    "rateLimits": {
        "credits": {"balance": "0", "unlimited": False, "hasCredits": False},
        "limitId": "codex",
        "primary": {"resetsAt": 1789885996, "usedPercent": 100, "windowDurationMins": 300},
        "planType": "plus",
        "limitName": None,
        "secondary": {"resetsAt": 1790433639, "usedPercent": 32, "windowDurationMins": 10080},
        "individualLimit": None,
        "spendControlReached": False,
        "rateLimitReachedType": "rate_limit_reached",
    }
}


def test_the_real_shape_from_the_live_engine_is_read_from_the_exhausted_window() -> None:
    """Pierwszy raz, kiedy widzimy tę odpowiedź naprawdę, i pierwszy test na jej kształcie.

    Wyczerpane jest okno `primary` (100 procent), więc to ono blokuje pracę i jego `resetsAt`
    jest odpowiedzią. `secondary` zużyte w 32 procentach nie ma tu nic do powiedzenia, a jego
    termin wypada tydzień później: wybranie go kosztowałoby tydzień czekania.
    """
    moment = datetime(2026, 9, 20, 6, 33, 14, tzinfo=UTC)

    when = resets_from_engine(REAL_SHAPE, now=moment)

    assert when == datetime(2026, 9, 20, 6, 33, 16, tzinfo=UTC)
    assert when != datetime.fromtimestamp(1790433639, tz=UTC), "to jest okno tygodniowe"


def test_when_both_windows_are_exhausted_the_later_one_decides() -> None:
    """Tygodniowe okno nie przestaje obowiązywać dlatego, że minęło pięciogodzinne."""
    moment = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
    both = {
        "rateLimits": {
            "primary": {
                "resetsAt": int((moment + timedelta(minutes=5)).timestamp()),
                "usedPercent": 100,
            },
            "secondary": {
                "resetsAt": int((moment + timedelta(hours=30)).timestamp()),
                "usedPercent": 100,
            },
        }
    }

    assert resets_from_engine(both, now=moment) == (moment + timedelta(hours=30)).replace(
        microsecond=0
    )


def test_the_growing_gap_starts_at_two_minutes_and_stops_at_an_hour() -> None:
    """ZMIERZONE NA ŻYWO: rozmowa wznowiła się o 06:33:10, trafiła w limit o 06:33:14, a silnik
    podał odnowienie na 06:33:16. Ponowienie „dokładnie wtedy" weszłoby w pętlę co kilka sekund."""
    assert backoff(1) == timedelta(minutes=2)
    assert backoff(2) == timedelta(minutes=4)
    assert backoff(3) == timedelta(minutes=8)
    assert backoff(10) == timedelta(hours=1), "sufit, bo dłuższe czekanie kosztuje więcej niż próba"


# ---------------------------------------------------------------------------
# Jedna funkcja rozpoznaje epokę dla OBU dróg tej samej liczby
# ---------------------------------------------------------------------------


def test_an_epoch_is_recognised_by_magnitude_not_by_the_name_of_the_field() -> None:
    """`resetsAt` z silnika jedzie dwiema drogami: do planowania powrotu i na ekran połączeń.

    Gdy każda z nich czytała tę liczbę po swojemu, pierwsza zaplanowała powrót na rok 2083
    (D-045), a druga wysłała ekranowi liczbę tam, gdzie schemat dashboardu wymaga napisu ISO, i
    ekran napisał „to zepsuty odczyt" o połączeniu, które działało. Liczby poniżej są z pomiaru
    z 20.09 (`output/ratelimits-2026-09-20.json`), nie wymyślone.
    """
    from control_plane.sessions.rate_limit import moment_from_epoch

    seconds = moment_from_epoch(1789885996)
    assert seconds is not None and seconds.isoformat() == "2026-09-20T06:33:16+00:00"

    milliseconds = moment_from_epoch(1789885996123)
    assert milliseconds is not None and milliseconds.year == 2026
    assert milliseconds.isoformat().startswith("2026-09-20T06:33:16")


def test_a_number_too_small_to_be_an_epoch_is_not_guessed_at() -> None:
    """300 to nie jest chwila i nie wolno jej zamienić w chwilę.

    W odczycie limitów bywa i epoka, i odstęp w sekundach; rozpoznanie idzie po rzędzie
    wielkości, a co poniżej progu, zostaje nierozpoznane. Ścieżka tekstowa ma swój własny
    przekład odstępu na chwilę i to ona o tym decyduje, nie ta funkcja.
    """
    from control_plane.sessions.rate_limit import moment_from_epoch

    assert moment_from_epoch(300) is None
    assert moment_from_epoch(0) is None
    assert moment_from_epoch(-1) is None


def test_things_that_are_not_numbers_are_not_moments() -> None:
    """`True` jest w Pythonie liczbą i to jest dokładnie ta pułapka, przez którą warto napisać
    osobne zdanie: `isinstance(True, int)` jest prawdą, a chwilą `True` nie jest."""
    from control_plane.sessions.rate_limit import moment_from_epoch

    assert moment_from_epoch(True) is None
    assert moment_from_epoch("2026-09-20T06:33:16+00:00") is None
    assert moment_from_epoch(None) is None
    assert moment_from_epoch({"resetsAt": 1789885996}) is None


#: KSZTAŁT ZMIERZONY NA ŻYWO 29.09 (ślad sesji w produkcji): konto na planie darmowym, jedno
#: okno 30-dniowe wyczerpane w 100 procentach, jeden darmowy reset limitu do wykorzystania.
FREE_SHAPE = {
    "rateLimits": {
        "credits": {"balance": None, "unlimited": False, "hasCredits": False},
        "limitId": "codex",
        "primary": {"resetsAt": 1793115628, "usedPercent": 100, "windowDurationMins": 43200},
        "planType": "free",
        "limitName": None,
        "secondary": None,
        "individualLimit": None,
        "spendControlReached": False,
        "rateLimitReachedType": "rate_limit_reached",
    },
    "rateLimitResetCredits": {
        "credits": [
            {
                "id": "RateLimitResetCredit_x",
                "title": "Full reset (Monthly)",
                "status": "available",
                "resetType": "codexRateLimits",
            }
        ],
        "availableCount": 1,
    },
}


def test_a_thirty_day_window_is_believed_up_to_its_own_length() -> None:
    """28-29.09: odnowienie za 28 dni było prawdą, a `MAX_AHEAD` odrzucał je jako pomyłkę, więc
    człowiek przez dobę czytał „nie wiem, kiedy się odnowi”. Okno podaje swoją długość i do niej
    (plus doba) mu wierzymy; dalej nadal nie."""
    moment = datetime(2026, 9, 29, 20, 30, tzinfo=UTC)

    assert resets_from_engine(FREE_SHAPE, now=moment) == datetime.fromtimestamp(1793115628, tz=UTC)
    year = int((moment + timedelta(days=365)).timestamp())
    beyond = {"rateLimits": {"primary": {**FREE_SHAPE["rateLimits"]["primary"], "resetsAt": year}}}
    assert resets_from_engine(beyond, now=moment) is None, "rok to nadal pomyłka, nie okno"
    no_span = {"rateLimits": {"primary": {"resetsAt": 1793115628, "usedPercent": 100}}}
    assert resets_from_engine(no_span, now=moment) is None, "bez długości okna zostaje tydzień"


def test_the_account_facts_are_read_and_a_long_wait_says_the_date_and_the_reset() -> None:
    from control_plane.sessions.rate_limit import LONG_WAIT_CHECK, account_facts

    facts = account_facts(FREE_SHAPE)
    assert (facts.plan, facts.resets_available) == ("free", 1)
    assert account_facts(REAL_SHAPE).plan == "plus"
    assert account_facts(None).resets_available == 0

    moment = datetime(2026, 9, 29, 20, 30, tzinfo=UTC)
    resets = datetime.fromtimestamp(1793115628, tz=UTC)
    text = sentence(
        RateLimit(
            resets_at=resets,
            source="engine",
            raw="You've hit your usage limit.",
            plan="free",
            resets_available=1,
            check_at=moment + LONG_WAIT_CHECK,
        )
    )
    assert resets.astimezone().strftime("%d.%m o %H:%M") in text
    assert text.startswith("Konto Codex doszło do limitu na planie darmowym")
    assert "darmowy reset limitu" in text
    assert "Co 3 godziny" in text and "Możesz zamknąć okno" in text
    for upsell in ("Upgrade", "Plus", "kup", "płatn"):
        assert upsell not in text, "ekran podaje fakty, nie namawia do zakupu"
