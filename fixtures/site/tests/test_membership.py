"""Dołączenie konta do społeczności i ODCZYT ZWROTNY tego faktu (E3b).

PO CO TO W FIXTURE. Do 20.09 produkt mówił „Dołączono" o włączonej obserwacji, czyli o czytaniu
publicznych wątków, i nie umiał zapisać konta do społeczności. Rozdzielenie nazw usunęło
nieprawdę, ale samego działania nie dodało. Aktuator kapsuły ma je wykonać, a zanim dotknie
czyjegokolwiek prawdziwego konta, ma być mierzony TUTAJ.

CZYM TA ŚCIEŻKA RÓŻNI SIĘ OD KOMENTARZA W TEJ SAMEJ FIXTURE. Komentarz zostaje samą PRÓBĄ
zapisu: treść pochodzi z ziarna i nie wolno jej ruszać, bo `content_hash` ma być stabilny, a
dowodem publikacji na prawdziwej platformie i tak jest odczyt zwrotny. Członkostwo musi dać się
odczytać z powrotem, bo inaczej nie ma na czym zmierzyć zdania „dołączono": to właśnie ten
odczyt odróżnia wysłanie żądania od skutku. Stan jest w pamięci, obok sesji, więc treść z
ziarna zostaje nietknięta.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import log_in

COMMUNITY = "/c/infra-praktycy"


def test_joining_requires_an_account(client: TestClient) -> None:
    """Bez sesji nie ma kogo zapisać, a ściana logowania stoi tu tak samo jak nad resztą `/c`."""
    response = client.post(f"{COMMUNITY}/membership")

    assert response.status_code == 302
    assert response.headers["location"].startswith("/login?next=")
    assert client.get("/debug/writes").json()["count"] == 0


def test_a_community_says_outright_that_the_account_is_not_a_member(client: TestClient) -> None:
    log_in(client)

    page = client.get(COMMUNITY)

    assert page.status_code == 200
    assert "Nie należysz do tej społeczności" in page.text
    assert 'data-testid="join-community"' in page.text
    assert "Jesteś członkiem" not in page.text


def test_joining_is_readable_back_from_the_page(client: TestClient) -> None:
    """Sedno całego elementu: po dołączeniu STRONA mówi, że konto jest członkiem.

    Aktuator nie ma innego sposobu, żeby odróżnić „wysłałem żądanie" od „jestem członkiem", i
    to jest ta sama różnica, którą produkt przez jakiś czas zacierał.
    """
    log_in(client)

    posted = client.post(f"{COMMUNITY}/membership")
    page = client.get(COMMUNITY)

    assert posted.status_code == 303
    assert posted.headers["location"] == f"{COMMUNITY}?membership=1"
    assert "Jesteś członkiem" in page.text
    assert 'data-testid="leave-community"' in page.text
    assert 'data-testid="join-community"' not in page.text


def test_joining_twice_leaves_the_same_state_and_is_not_an_error(client: TestClient) -> None:
    """Prawdziwa platforma odpowiada na powtórzenie tym samym stanem.

    Aktuator, który zobaczyłby tu błąd, uznałby udane dołączenie za porażkę i powtórzyłby
    działanie na cudzej społeczności.
    """
    log_in(client)

    first = client.post(f"{COMMUNITY}/membership")
    second = client.post(f"{COMMUNITY}/membership")

    assert first.status_code == 303 and second.status_code == 303
    assert "Jesteś członkiem" in client.get(COMMUNITY).text


def test_leaving_takes_the_account_out_again(client: TestClient) -> None:
    log_in(client)
    client.post(f"{COMMUNITY}/membership")

    client.post(f"{COMMUNITY}/membership", data={"leave": "1"})
    page = client.get(COMMUNITY)

    assert "Nie należysz do tej społeczności" in page.text
    assert 'data-testid="join-community"' in page.text


def test_membership_is_recorded_as_a_write_attempt_with_its_own_kind(client: TestClient) -> None:
    """Ślad prób zapisu jest tym, z czego ćwiczenia czytają, co system pod testem zrobił."""
    log_in(client)

    client.post(f"{COMMUNITY}/membership")
    client.post(f"{COMMUNITY}/membership", data={"leave": "1"})

    writes = client.get("/debug/writes").json()["writes"]
    kinds = [one["kind"] for one in writes]
    assert kinds == ["COMMUNITY_JOIN", "COMMUNITY_LEAVE"], writes
    assert writes[0]["target"] == "infra-praktycy"
    assert writes[0]["username"] == "fixture-user"


def test_membership_of_an_unknown_community_is_a_refusal(client: TestClient) -> None:
    log_in(client)

    response = client.post("/c/nie-ma-takiej/membership")

    assert response.status_code == 404
    assert client.get("/debug/writes").json()["count"] == 0


def test_membership_does_not_change_the_content_hash(client: TestClient) -> None:
    """Treść pochodzi z ziarna i członkostwo jej nie rusza.

    Gdyby ruszało, każde ćwiczenie zmieniałoby fixture pod następnym, a determinizm tej strony
    jest tym, co pozwala porównywać przebiegi między dniami.
    """
    before = client.get("/api/public/content-hash").json()["content_hash"]
    log_in(client)
    client.post(f"{COMMUNITY}/membership")

    assert client.get("/api/public/content-hash").json()["content_hash"] == before


def test_the_probe_reads_this_page_the_way_the_actuator_will(client: TestClient) -> None:
    """Wyciąg sondy (E3b) czyta TĘ stronę, a nie moje wyobrażenie o niej.

    `browser_supervisor/community_markup.py` nie importuje niczego poza biblioteką standardową
    właśnie po to: ta sama funkcja ma działać w kontenerze kapsuły i tutaj, gdzie strona naprawdę
    się renderuje. Testy po tamtej stronie stoją na kształtach napisanych z pamięci, więc mierzą
    zachowanie wyciągu; ten jeden mierzy je na HTML-u, którego nikt nie wymyślił.

    Gdyby szablon społeczności się zmienił tak, że sonda przestaje widzieć przycisk, ten test
    powie o tym tego samego dnia, a nie przy pierwszym prawdziwym dołączeniu.
    """
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    where = root / "services/browser_supervisor/browser_supervisor/community_markup.py"
    spec = importlib.util.spec_from_file_location("community_markup_for_fixture", where)
    markup = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = markup
    spec.loader.exec_module(markup)

    log_in(client)
    before = markup.candidates(client.get(COMMUNITY).text)
    client.post(f"{COMMUNITY}/membership")
    after = markup.candidates(client.get(COMMUNITY).text)

    assert any("Dołącz" in one.text for one in before.join_like), before.join_like
    assert any("membership" in one.action for one in before.subscribe_forms), before.subscribe_forms
    # PO DOŁĄCZENIU WIDAĆ CO INNEGO, i to jest dokładnie to, po czym aktuator pozna skutek.
    assert any("Opuść" in one.text for one in after.join_like), after.join_like
    assert not any("Dołącz" in one.text for one in after.join_like), after.join_like
