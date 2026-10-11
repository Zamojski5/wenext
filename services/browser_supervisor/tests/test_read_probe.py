"""Sonda czytania bez logowania: co liczy się za odpowiedź, a co za jej brak (P6 etap A).

Sama sonda otwiera prawdziwe strony i dlatego nie da się jej uruchomić w tym pakiecie testów.
Da się natomiast zmierzyć REGUŁY, po których czyta wynik, i to one decydują, czy artefakt mówi
prawdę:

  * **200 to nie to samo co treść.** Strona renderowana po stronie klienta odpowiada 200 i zero
    znaków tekstu. Uznanie jej za czytelną oznaczałoby adapter zbudowany na pustej skorupie,
    który raportowałby pustkę jako nieobecność.
  * **Ściana logowania to wynik, nie awaria.** Przekierowanie na `/uas/login` jest odpowiedzią
    platformy i ma trafić do artefaktu jako odpowiedź.
  * **Słowo „login” w treści nie czyni ściany.** Dopasowanie idzie po adresie i tytule, nigdy po
    tekście strony; artykuł o logowaniu nie jest ścianą logowania.
  * **Limit żądań jest twardy.** To pomiar zdolności, nie zbieranie danych, i różnica ma być
    widoczna w kodzie, a nie tylko w intencji.
"""

from __future__ import annotations

import pytest
from browser_supervisor.read_probe import (
    DEFAULT_TARGETS,
    MAX_TARGETS,
    Observation,
    ProbeResult,
    Target,
    _looks_like_a_login_wall,
    run_read_probe,
    summary_lines,
)


def _seen(**overrides) -> Observation:
    base = {
        "platform": "X",
        "kind": "profile",
        "url": "https://example.test/a",
        "status": 200,
        "text_bytes": 5000,
    }
    return Observation(**{**base, **overrides})


def test_a_page_with_content_is_readable() -> None:
    assert _seen().readable


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("403", {"status": 403}),
        ("pusta skorupa", {"text_bytes": 0}),
        ("prawie pusta", {"text_bytes": 199}),
        ("ściana logowania", {"login_wall": True}),
        ("nic nie przyszło", {"status": None}),
    ],
)
def test_what_does_not_count_as_readable(label, overrides) -> None:
    assert not _seen(**overrides).readable, label


@pytest.mark.parametrize(
    "final_url",
    [
        "https://www.linkedin.com/uas/login?session_redirect=%2Ffeed%2F",
        "https://x.com/i/flow/login",
        "https://example.test/signin",
        "https://example.test/authwall",
    ],
)
def test_a_redirect_to_a_login_page_is_a_wall(final_url) -> None:
    assert _looks_like_a_login_wall(final_url, "cokolwiek")


def test_a_title_that_asks_you_to_sign_in_is_a_wall() -> None:
    assert _looks_like_a_login_wall("https://example.test/x", "Zaloguj się, aby zobaczyć")
    assert _looks_like_a_login_wall("https://example.test/x", "Sign in | LinkedIn")


def test_an_article_about_logging_in_is_not_a_wall() -> None:
    """Dopasowanie po adresie i tytule, nigdy po treści. Inaczej każdy tekst o logowaniu
    zostałby zapisany jako odmowa platformy."""
    assert not _looks_like_a_login_wall(
        "https://example.test/blog/how-we-built-login", "Jak zbudowaliśmy uwierzytelnianie"
    )


def test_the_request_cap_is_enforced_and_not_merely_documented(tmp_path) -> None:
    too_many = [Target("X", "profile", f"https://example.test/{n}") for n in range(MAX_TARGETS + 1)]
    with pytest.raises(ValueError) as caught:
        run_read_probe(object(), too_many, artifacts_dir=tmp_path)
    assert "sonda zdolności" in str(caught.value)


def test_the_default_targets_ask_three_questions_per_platform() -> None:
    """Profil, post i wyszukiwanie: trzy różne odpowiedzi znaczą trzy różne adaptery."""
    by_platform: dict[str, set[str]] = {}
    for target in DEFAULT_TARGETS:
        by_platform.setdefault(target.platform, set()).add(target.kind)
    assert by_platform == {
        "X": {"profile", "post", "search"},
        "LINKEDIN": {"profile", "post", "search"},
    }
    assert len(DEFAULT_TARGETS) <= MAX_TARGETS


def test_an_incomplete_run_says_so_in_the_summary() -> None:
    """Sonda, która się wywróciła, nie jest platformą, która nic nie odpowiedziała."""
    result = ProbeResult(started_at="teraz", complete=False, note="kapsuła nie wstała")
    assert any("NIEPEŁNE" in line for line in summary_lines(result))


def test_the_summary_names_the_status_rather_than_only_a_verdict() -> None:
    result = ProbeResult(started_at="teraz", complete=True, observations=[_seen(status=403)])
    line = summary_lines(result)[0]
    assert "403" in line
