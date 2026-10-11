"""Wyciąg kandydatów na przycisk dołączenia, mierzony bez Chrome (E3b).

CZEGO TEN PLIK PILNUJE. Sonda ma ZNALEŹĆ to, co na stronie jest, i NIE ORZEKAĆ, który element
jest przyciskiem. Różnica nie jest akademicka: sonda, która sama wybiera, przeniosłaby zgadywanie
selektorów z kodu aktuatora do kodu sondy, a artefakt wyglądałby na pomiar.

Testy stoją na dwóch kształtach znacznika: starego Reddita (przełącznik z dwoma opcjami) i
nowego (klasa bez słowa „join", etykieta w środku). Oba napisałem z pamięci i dlatego mierzą
WYŁĄCZNIE zachowanie wyciągu, nigdy tego, jak naprawdę wygląda Reddit; od tego jest sonda i
jej artefakt. Trzeci kształt, STRONĘ FIXTURE tego repozytorium, sprawdza suita fixture
(`fixtures/site/tests/test_membership.py`), bo tam ta strona naprawdę się renderuje.
"""

from __future__ import annotations

import pytest
from browser_supervisor.community_markup import candidates, community_url

OLD_SHAPE = """
<html><body>
  <div id="header-bottom-right"><span class="user"><a href="/user/Demo_Maintainer">Demo_Maintainer</a></span></div>
  <div class="titlebox">
    <div class="fancy-toggle-button toggle subscribe-button">
      <a class="option active add" href="#">subscribe</a>
      <a class="option remove" href="#" style="display:none">unsubscribe</a>
    </div>
    <span class="subscribers"><span class="number">154,003</span> readers</span>
    <p class="users-online">42 users here now</p>
  </div>
</body></html>
"""

NEW_SHAPE = """
<html><body>
  <button class="_2tU8R9NTqhvBrhoNAXWWcP" data-testid="subscribe">
    <span>Join</span>
  </button>
  <div class="_3-miAEojrCvx_4FQ8x3P-s">12.4k members</div>
</body></html>
"""


def test_the_old_shape_gives_both_options_with_their_labels() -> None:
    """Przełącznik starego Reddita to DWA elementy, jeden ukryty stylem.

    Sonda ma pokazać oba, bo to właśnie po nich poznaje się stan: który jest aktywny, ten mówi,
    czy konto należy do społeczności. Ukrycie stylem jest w HTML niewidoczne, więc rozstrzyga
    zrzut ekranu, który sonda zapisuje obok.
    """
    found = candidates(OLD_SHAPE)

    labels = sorted(one.text for one in found.join_like)
    assert "subscribe" in labels and "unsubscribe" in labels, found.join_like
    classes = " ".join(one.classes for one in found.join_like)
    assert "fancy-toggle-button" in classes and "active" in classes


def test_the_account_in_the_header_is_reported_separately() -> None:
    """Warunek „to konto, którego oczekujemy" ma być taki sam jak przy komentarzu."""
    found = candidates(OLD_SHAPE)

    assert [one.text for one in found.account_header] == ["Demo_Maintainer"]


def test_the_account_is_found_on_the_new_interface_too() -> None:
    """Sonda z 21.09 nie rozpoznała konta na stronie wątku na `www`, choć nazwa stała w HTML.

    Szukała wyłącznie starego selektora (`header-bottom-right`), a nowy interfejs trzyma to w
    `user-drawer-button`. Dla aktuatora „czy to nasze konto" jest warunkiem twardym, więc sonda
    ma umieć odpowiedzieć na obu interfejsach, zamiast milczeć o jednym.
    """
    html = """
    <html><body>
      <faceplate-tracker><button id="user-drawer-button" aria-label="Open profile menu">
        <span>Demo_Maintainer</span>
      </button></faceplate-tracker>
    </body></html>
    """

    found = candidates(html)

    assert any("Demo_Maintainer" in one.text for one in found.account_header), found.account_header


def test_member_counts_come_back_as_text_not_as_a_verdict() -> None:
    """Licznik jest POSZLAKĄ. Nie mówi, czy TO konto należy do społeczności, i bywa buforowany,
    więc sonda oddaje go jako napis, a nie jako liczbę do porównywania."""
    assert any("154,003" in one for one in candidates(OLD_SHAPE).member_counts)
    assert any("12.4k members" in one for one in candidates(NEW_SHAPE).member_counts)


def test_a_button_whose_class_says_nothing_is_still_found_by_its_label() -> None:
    """Nowy Reddit nazywa przycisk klasą bez znaczenia, a etykietę trzyma w środku.

    Gdyby sonda patrzyła tylko na klasy, na nowym interfejsie nie znalazłaby nic i wyglądałoby
    to jak strona bez przycisku, czyli jak odmowa.
    """
    found = candidates(NEW_SHAPE)

    # Kandydatem jest i `<button>`, i `<span>` z napisem w środku, i to jest w porządku: sonda
    # ma pokazać, co tam stoi, a nie wybrać. Wybiera człowiek czytający artefakt.
    tags = [one.tag for one in found.join_like]
    assert "button" in tags, found.join_like
    assert all(one.text == "Join" for one in found.join_like), found.join_like


def test_a_subscribe_form_is_reported_apart_from_buttons() -> None:
    """Formularz do `/api/subscribe` to inna droga niż kliknięcie w przełącznik i inaczej się ją
    obsługuje, więc nie miesza się go z przyciskami."""
    html = """
    <html><body>
      <form action="/api/subscribe" method="post"><button type="submit">join</button></form>
    </body></html>
    """

    found = candidates(html)

    assert [one.action for one in found.subscribe_forms] == ["/api/subscribe"]


def test_a_page_with_nothing_to_click_reports_nothing_instead_of_guessing() -> None:
    """Brak kandydatów jest WYNIKIEM: tak wygląda społeczność, do której dołączyć się nie da.

    Reguła przyjęta 21.09 mówi, że brak rozpoznanego przycisku to odmowa przed kliknięciem, więc
    ten wynik musi być odróżnialny od „nie patrzyliśmy".
    """
    found = candidates("<html><body><p>Ta społeczność jest prywatna.</p></body></html>")

    assert found.join_like == [] and found.subscribe_forms == []
    assert found.html_bytes > 0, "sonda przeczytała stronę, tylko nic na niej nie znalazła"


def test_the_address_is_built_from_the_name_and_refuses_anything_else() -> None:
    """Nazwa, nie adres, i składanie po naszej stronie: nie ma czego przemycić w ścieżce."""
    assert community_url("AI_Agents") == "https://old.reddit.com/r/AI_Agents/"
    assert community_url("r/AI_Agents") == "https://old.reddit.com/r/AI_Agents/"
    assert community_url("/r/AI_Agents/") == "https://old.reddit.com/r/AI_Agents/"
    for wrong in ("", "   ", "AI Agents", "https://evil.example/r/x", "r/x/../../y", "a"):
        with pytest.raises(ValueError):
            community_url(wrong)


def test_both_hosts_are_measured_and_nothing_else_is_allowed() -> None:
    """Pierwszy przebieg sondy 21.09: `old.reddit.com` oddał 403 i stronę „You've been blocked",
    identyczną dla dwóch różnych społeczności.

    Czytnik kapsuły ma w allowliście WYŁĄCZNIE `www.reddit.com` i działa, a aktuator komentarzy
    pisze na `old.reddit.com`. Który host odpowiada, jest więc osobnym pytaniem od tego, co stoi
    w znaczniku, i sonda ma odpowiedzieć na oba, zamiast zakładać jedno. Host spoza tej dwójki
    jest odmową, bo adres składamy sami i nie ma powodu, żeby dało się wskazać dowolny.
    """
    from browser_supervisor.community_markup import COMMUNITY_HOSTS

    assert COMMUNITY_HOSTS == ("www.reddit.com", "old.reddit.com")
    assert community_url("AI_Agents", host="www.reddit.com") == (
        "https://www.reddit.com/r/AI_Agents/"
    )
    with pytest.raises(ValueError):
        community_url("AI_Agents", host="evil.example")


# ---------------------------------------------------------------------------
# Sonda DOŁĄCZA SIĘ do działającej przeglądarki, nie uruchamia własnej
# ---------------------------------------------------------------------------


def test_the_probe_attaches_to_the_running_browser_and_starts_nothing(tmp_path) -> None:
    """Pytanie koordynatora 21.09, i jest to właściwe pytanie.

    Zalogowana sesja Reddita żyje w profilu DZIAŁAJĄCEJ kapsuły: jedna kapsuła, jeden profil,
    jeden Chrome. Drugi proces na tym samym katalogu jest zakazany, a świeży profil odpowiadałby
    na pytania o członkostwo widokiem anonima, czyli nie na te pytania. Sonda bierze więc adres
    z `DevToolsActivePort` tą samą drogą, co aktuator komentarza, i nie ma w sobie ani jednego
    wywołania startującego przeglądarkę.
    """
    import inspect

    from browser_supervisor import community_probe

    source = inspect.getsource(community_probe)
    assert "supervisor.start(" not in source, (
        "sonda uruchamia własną przeglądarkę; na profilu kapsuły to drugi Chrome na jednym "
        "katalogu, a na świeżym profilu odpowiedź dotyczy anonima"
    )
    assert "supervisor.stop(" not in source, "sonda zatrzymuje kapsułę, której nie uruchomiła"
    assert "devtools_port.verify(" in source, (
        "adres CDP bierze się z pliku bez sprawdzenia; po kill -9 plik zostaje z martwym portem, "
        "który mógł przejąć inny proces"
    )


def test_without_a_running_browser_the_probe_refuses_instead_of_measuring_nothing(
    tmp_path,
) -> None:
    """Brak działającej przeglądarki to ODMOWA ze zdaniem, nie pusty artefakt.

    Artefakt i tak powstaje, bo sonda, która się wywróciła i nie zostawiła nic, jest
    nieodróżnialna od strony, która nic nie pokazała; różnicę niesie `complete` i `note`.
    """
    from browser_supervisor.community_probe import ARTIFACT_NAME, run_community_probe
    from browser_supervisor.supervisor import BrowserSupervisor

    supervisor = BrowserSupervisor(
        binary=tmp_path / "chrome",
        profiles_dir=tmp_path / "profiles",
        capsule_id="capsule-test",
        expected_cdp_browser="Chrome/0.0.0.0",
        cdp_host="127.0.0.1",
    )
    artifacts = tmp_path / "artifacts"

    result = run_community_probe(supervisor, ["AI_Agents"], artifacts_dir=artifacts)

    assert result.complete is False
    assert "DevToolsActivePort" in result.note or "Brak" in result.note, result.note
    assert (artifacts / ARTIFACT_NAME).exists(), "sonda nie zostawiła artefaktu po nieudanym biegu"


# ---------------------------------------------------------------------------
# `--probe-url`: adres od człowieka przechodzi przez zamkniętą listę kształtów
# ---------------------------------------------------------------------------


def test_a_thread_address_on_either_host_is_allowed() -> None:
    """Pytanie, dla którego to powstało (koordynator, 21.09): czy `old.reddit.com` w ogóle
    odpowiada tej przeglądarce na stronę WĄTKU.

    Od tego zależy pilot, bo aktuator komentarzy pisze właśnie tam, a strona społeczności na tym
    hoście oddała 403. Sonda dostaje adres od CZŁOWIEKA, więc sprawdzenie kształtu musi być po
    naszej stronie; bez niego `--probe-url` byłoby przeglądarką na czyjeś życzenie.
    """
    from browser_supervisor.community_markup import authorize_page_url

    thread = "https://old.reddit.com/r/AI_Agents/comments/1abc2de/jakis_tytul/"
    assert authorize_page_url(thread) == thread
    assert authorize_page_url("https://www.reddit.com/r/devops/comments/abc123") == (
        "https://www.reddit.com/r/devops/comments/abc123"
    )
    assert authorize_page_url("https://www.reddit.com/r/devops/") == (
        "https://www.reddit.com/r/devops/"
    )


def test_anything_that_is_not_a_community_or_a_thread_is_refused() -> None:
    """Zamknięta lista hostów i zamknięta lista kształtów, tak samo jak w allowliście czytnika."""
    import pytest as _pytest
    from browser_supervisor.community_markup import authorize_page_url

    for wrong in (
        "",
        "http://www.reddit.com/r/devops/",
        "https://evil.example/r/devops/",
        "https://www.reddit.com@evil.example/r/devops/",
        "https://user:pw@www.reddit.com/r/devops/",
        "https://www.reddit.com:8443/r/devops/",
        "https://www.reddit.com/user/someone/",
        "https://www.reddit.com/r/devops/submit",
        "https://www.reddit.com/r/devops/?sort=new",
        "https://www.reddit.com/r/devops/comments/abc123/#reply",
        "https://www.reddit.com/settings/",
    ):
        with _pytest.raises(ValueError):
            authorize_page_url(wrong)


def test_the_probe_reads_the_live_dom_because_the_button_is_a_component() -> None:
    """ZMIERZONE 21.09 na żywej stronie: `www.reddit.com/r/AI_Agents/` odpowiada 200 i pokazuje
    człowiekowi niebieski przycisk „Join", a w zapisanym HTML nie ma `shreddit-join-button`.

    Nowy Reddit dogrywa go komponentem (`faceplate-partial`, shadow DOM), więc sam `page.content()`
    opisywałby stronę wcześniejszą niż ta, którą widzi człowiek. Sonda pyta więc też ŻYWY DOM o
    zamkniętą listę selektorów i przepisuje liczby oraz etykiety. Dalej nie orzeka, który z nich
    jest przyciskiem.
    """
    from browser_supervisor.community_probe import LIVE_SELECTORS, SETTLE_MS

    assert "shreddit-join-button" in LIVE_SELECTORS
    assert any("Join" in one for one in LIVE_SELECTORS)
    assert SETTLE_MS >= 1000, "bez chwili na dogranie komponentu sonda opisuje stronę sprzed niego"


# ---------------------------------------------------------------------------
# Wejście z wiersza poleceń, czyli to, co naprawdę uruchamia człowiek
# ---------------------------------------------------------------------------


def _fake_environment(tmp_path, monkeypatch) -> None:
    """Środowisko kapsuły bez kapsuły: katalog profili, binarka i plik wersji, wszystko puste.

    Plik wersji musi ISTNIEĆ i mieć pole build przeglądarki, bo `build_supervisor` czyta je od
    razu; to jest część tej samej umowy, co reszta: kapsuła bez przypiętej wersji nie wstaje.
    """
    lock = tmp_path / "versions.lock.toml"
    lock.write_text(
        '[browser.chrome_for_testing.expected_cdp]\nbrowser_field = "Chrome/0.0.0.0"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("WENEXT_PROFILES_DIR", str(tmp_path / "profiles"))
    monkeypatch.setenv("WENEXT_BROWSER_BIN", str(tmp_path / "chrome"))
    monkeypatch.setenv("WENEXT_VERSIONS_LOCK", str(lock))


def test_the_probe_runs_with_only_a_url_and_no_community(tmp_path, monkeypatch) -> None:
    """ZGŁOSZENIE KOORDYNATORA 21.09: sonda wątku padła PRZED startem, na `TypeError` z `None`.

    Obie flagi są opcjonalne i wolno podać samą `--probe-url`, a gałąź robiła `list(...)` na
    wartości domyślnej. Testowałem czystą funkcję sprawdzającą adres i nie testowałem SKLEJENIA
    z wierszem poleceń, czyli dokładnie tej samej luki, przez którą wcześniej przeszedł
    `AttributeError` w trasie poprawki: nietestowana glue między sprawdzonymi kawałkami.

    Test idzie przez `main`, bo to jest wejście, którego używa człowiek. Bez działającej
    przeglądarki sonda odmawia i wychodzi kodem 1; ważne jest to, że dochodzi DO odmowy zamiast
    wywracać się na rozpakowaniu argumentów.
    """
    from browser_supervisor.community_probe import ARTIFACT_NAME
    from browser_supervisor.main import main

    _fake_environment(tmp_path, monkeypatch)
    artifacts = tmp_path / "artifacts"

    code = main(
        [
            "--probe-url",
            "https://old.reddit.com/r/ecommerce/comments/1wixhex/changement/",
            "--artifacts",
            str(artifacts),
        ]
    )

    assert code == 1, "brak przeglądarki to niedokończony przebieg, czyli kod 1"
    assert (artifacts / "community-probe" / ARTIFACT_NAME).exists(), (
        "sonda nie zostawiła artefaktu; przebieg, który się wywrócił i nie zostawił nic, jest "
        "nieodróżnialny od strony, która nic nie pokazała"
    )


def test_the_probe_still_runs_with_only_a_community(tmp_path, monkeypatch) -> None:
    """Druga połowa tej samej reguły: samo `--community-probe` też ma działać.

    Poprawka „albo pusta lista" łatwo psuje ten kierunek, a wtedy naprawiona byłaby jedna flaga
    kosztem drugiej.
    """
    from browser_supervisor.main import main

    _fake_environment(tmp_path, monkeypatch)

    code = main(["--community-probe", "AI_Agents", "--artifacts", str(tmp_path / "a")])

    assert code == 1
