"""Karty rozmowy: jeden kształt dla narzędzi i dla projekcji (D-046).

KONTRAKT JEST W `docs/produkt/KARTY-ROZMOWY.md`, a ten moduł jest jego jedyną implementacją po
stronie serwera. Narzędzie, które coś robi, zwraca kartę w wyniku; projekcja śladu czyta te same
karty i podaje je ekranowi. Gdyby każde z nich budowało własny słownik, karta w wyniku narzędzia
i karta na ekranie rozjechałyby się przy pierwszej zmianie nazwy pola, a rozjazd byłby widoczny
dopiero u człowieka.

NAZWY PO ANGIELSKU, WARTOŚCI I KLUCZE PO POLSKU, i to nie jest niekonsekwencja (D-010).
Identyfikatory w kodzie czyta programista; `stan: "dolaczono"` i klucz `uzasadnienie` czyta ekran
i człowiek. Ta sama zasada, co przy `WYKONANE` i `NIEDOSTEPNE` w katalogu narzędzi.

TRZY REGUŁY, KTÓRE TEN MODUŁ EGZEKWUJE ZA WSZYSTKICH:

  1. Akcja zawsze ma `nazwa`, `trasa` i `payload`. Ekran nie buduje ciała żądania z własnej
     wiedzy o produkcie; przycisk, który musiałby zgadnąć, co wysłać, nie ma prawa powstać.
  2. `blad` zawsze ma `powod`. Karta w błędzie bez powodu jest gorsza niż brak karty.
  3. Odcisk liczy się ze STANÓW, nie z treści. Poprawiony szkic zmienia tekst, a nie stan, i nie
     ma powodu przeładowywać strony pod człowiekiem, który właśnie czyta.
"""

from __future__ import annotations

from typing import Any

CARD_ACCOUNT = "KONTO"
CARD_COMMUNITY = "SPOLECZNOSC"
CARD_DRAFT = "SZKIC"
CARD_DELEGATION = "POWIERZENIE"
#: Silnik, czyli połączenie Codex organizacji. Osobny rodzaj od KONTA, bo to są dwie różne
#: rzeczy i mylenie ich jest pierwszym, co widzi nowy użytkownik: KONTO to profil na platformie
#: społecznościowej, SILNIK to subskrypcja, na której w ogóle biegnie praca.
CARD_ENGINE = "SILNIK"

#: Stany kart. Zamknięte słowniki, osobne dla każdego rodzaju, tak jak w kontrakcie.
ACCOUNT_DISCONNECTED = "brak_polaczenia"
ACCOUNT_CONNECTING = "lacze"
ACCOUNT_CONNECTED = "polaczone"
#: Konto wstrzymane albo w kwarantannie. CZWARTY STAN, nie wyjątek od braku połączenia, i to
#: rozróżnienie jest po to, żeby karta nie kłamała przyciskiem: `brak_polaczenia` znaczy
#: „kliknij, a pomoże”, a tutaj kliknięcie nie pomoże, bo logowanie niczego nie naprawi. Karta w
#: tym stanie przychodzi BEZ akcji (uzgodnione z ekranem 20.09: pusta lista akcji to brak
#: przycisku, nie przycisk wyszarzony).
ACCOUNT_UNAVAILABLE = "niedostepne"

#: Stany SILNIKA. Ta sama czwórka co przy koncie z nazwy, ale inne znaczenia: `lacze` znaczy
#: „trwa logowanie urządzenia i człowiek ma przed sobą kod", a nie „czekamy na potwierdzenie".
ENGINE_DISCONNECTED = "brak_polaczenia"
ENGINE_CONNECTING = "lacze"
ENGINE_CONNECTED = "polaczone"

#: Stany SPOŁECZNOŚCI. PIĘĆ, nie trzy, i podział przebiega w poprzek jednego słowa, które do
#: 20.09 znaczyło dwie rzeczy naraz (audyt UX, punkt 3). OBSERWACJA to czytanie publicznych
#: wątków: nie wymaga naszego konta i nie zostawia po sobie śladu w społeczności. CZŁONKOSTWO to
#: zapisanie NASZEGO KONTA do społeczności na platformie: prawdziwe działanie na zewnątrz, które
#: widzi moderator i które regulamin platformy ocenia. Produkt umiał pierwsze, a mówił człowiekowi
#: drugie, i to jest dokładnie ten rodzaj nieprawdy, po którym nie wiadomo już, czemu wierzyć.
COMMUNITY_PROPOSED = "propozycja"
#: Obserwacja włączona, ale jeszcze nic nie przeczytano. Osobny stan od `obserwowana`, bo różnica
#: jest widoczna dla człowieka: „włączyłem" to jeszcze nie „mam stamtąd wątki".
COMMUNITY_OBSERVING = "obserwuje"
COMMUNITY_OBSERVED = "obserwowana"
#: Dwa stany członkostwa. Dziś nieosiągalne: żadna platforma w macierzy nie ma `join_community`,
#: bo nic w drzewie nie zapisuje naszego konta do społeczności. Stoją tu, a nie powstaną później,
#: żeby karta i ekran miały jeden słownik, kiedy wykonawca członkostwa się pojawi.
COMMUNITY_JOINING = "dolaczam"
COMMUNITY_JOINED = "dolaczono"

#: Zdanie na karcie obserwowanej społeczności. Jest na niej dlatego, że „obserwowana" samo z
#: siebie nie mówi człowiekowi, czy produkt gdzieś go zapisał, a to jest pierwsze pytanie, jakie
#: ma prawo zadać po tym, jak przez jakiś czas odpowiadaliśmy na nie źle.
OBSERVATION_MEANS = (
    "Czytam tu publiczne wątki. Nie zapisuję tam Twojego konta i nikt w tej społeczności nie "
    "widzi, że ją obserwujemy."
)

DRAFT_NEW = "szkic"
DRAFT_PUBLISHING = "publikuje"
DRAFT_PUBLISHED = "opublikowano"

DELEGATION_TO_SIGN = "do_podpisu"
DELEGATION_SIGNED = "podpisana"
DELEGATION_REJECTED = "odrzucona"

#: Wspólny dla wszystkich rodzajów.
STATE_ERROR = "blad"

#: Co karta SZKIC mówi, gdy `opublikuj` nie może się pojawić. Pusta lista akcji odpowiada na
#: pytanie „co mogę zrobić”, ale nie na „dlaczego nic”, a to drugie jest tym, które zadaje
#: człowiek. Stoi TUTAJ, a nie w rejestrze narzędzi, bo kartę SZKIC buduje też trasa poprawki:
#: dwa zdania o tej samej rzeczy w dwóch plikach rozjechałyby się przy pierwszej zmianie.
NO_DELEGATION_NOTE = (
    "Publikacja w projekcie dzieje się w zakresie, który ktoś podpisał. Ten projekt nie ma "
    "żywego powierzenia, więc nie ma dokąd wydać tej propozycji."
)


def action(name: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict:
    """Jedna akcja karty. `payload` bywa pusty, ale nigdy go nie brakuje."""
    return {
        "nazwa": name,
        "trasa": {"metoda": method, "sciezka": path},
        "payload": payload or {},
    }


def card(
    kind: str,
    card_id: str,
    state: str,
    *,
    reason: str | None = None,
    actions: list[dict[str, Any]] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Zbuduj kartę. `powod` jest wymagany w stanie `blad` i zabroniony poza nim."""
    if state == STATE_ERROR and not reason:
        raise ValueError(f"karta {kind}/{card_id} w stanie blad bez powodu")
    if state != STATE_ERROR and reason:
        raise ValueError(f"karta {kind}/{card_id} w stanie {state} z powodem; powod jest dla bledu")
    return {
        "karta_id": card_id,
        "rodzaj": kind,
        "stan": state,
        "powod": reason,
        "akcje": actions or [],
        **fields,
    }


def account_card(
    *,
    platform: str,
    login: str,
    profile: str | None,
    state: str,
    login_url: str | None = None,
    actions: list[dict[str, Any]] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Karta KONTO. `karta_id` z platformy i loginu, bo to jest jej przedmiot.

    `**fields` przechodzi na wierzch karty i służy przypadkom, które mają do powiedzenia coś
    ponad stan: projekt bez przypisanego konta niesie tędy zdanie i odsyłacz na ekran, który to
    naprawia. Ekran czyta pola zależne od rodzaju z worka po nazwie, więc nowy klucz niczego nie
    psuje, a brak klucza niczego nie wymusza.
    """
    return card(
        CARD_ACCOUNT,
        f"konto:{platform}:{login}",
        state,
        actions=actions,
        platforma=platform,
        konto=login,
        profil=profile,
        adres_logowania=login_url,
        **fields,
    )


def engine_card(
    *,
    state: str,
    connection: str | None = None,
    label: str | None = None,
    login_url: str | None = None,
    code: str | None = None,
    reason: str | None = None,
    actions: list[dict[str, Any]] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Karta SILNIK: połączenie Codex, na którym ma ruszyć rozmowa.

    JEDNA NA ROZMOWĘ, bo pytanie brzmi „czy ta praca ma na czym ruszyć", a nie „ile połączeń ma
    organizacja". Identyfikator jest stały (`silnik`), więc kolejne tury nie mnożą kart, a stan
    odświeża się z wiersza połączenia przy każdym odczycie.

    `adres_logowania` i `kod` mają sens TYLKO w stanie `lacze`: logowanie urządzenia pokazuje
    adres i kod, które człowiek musi przepisać, i to jest jedyne, co ma wtedy do zrobienia.
    """
    return card(
        CARD_ENGINE,
        "silnik",
        state,
        reason=reason,
        actions=actions,
        polaczenie=connection,
        nazwa=label,
        adres_logowania=login_url,
        kod=code,
        **fields,
    )


def community_card(
    *,
    key: str,
    platform: str,
    name: str | None,
    rationale: str | None,
    state: str,
    observing: bool = False,
    means: str | None = None,
    reason: str | None = None,
    actions: list[dict[str, Any]] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Karta SPOŁECZNOŚĆ. `karta_id` z klucza, więc dwa wywołania o tej samej społeczności dają
    jedną kartę.

    `obserwacja` JEDZIE OBOK STANU, a nie zamiast niego (uzgodnione z ekranem, wenext-ce 20.09).
    Stan odpowiada na pytanie „na czym stoimy", a to jest jedno pytanie na dwie osie: kiedy
    członkostwo zacznie być osiągalne, `dolaczono` przy wyłączonej obserwacji znaczy „zapisałem
    tam Twoje konto i nic stamtąd nie czytam", i to musi być widać, a nie domyślać się ze stanu.
    """
    return card(
        CARD_COMMUNITY,
        f"spolecznosc:{platform}:{key}",
        state,
        reason=reason,
        actions=actions,
        klucz=key,
        platforma=platform,
        nazwa=name,
        uzasadnienie=rationale,
        obserwacja=observing,
        co_to_znaczy=means,
        **fields,
    )


def draft_card(
    *,
    revision: str,
    title: str,
    community: str | None,
    profile: str | None,
    text: str | None,
    state: str,
    delegation_allows: bool,
    reason: str | None = None,
    actions: list[dict[str, Any]] | None = None,
    no_actions_because: str | None = None,
    image: str | None = None,
) -> dict[str, Any]:
    """Karta SZKIC. `karta_id` z rewizji, bo to rewizja jest przedmiotem zgody.

    `dlaczego_bez_akcji` istnieje, bo pusta lista akcji odpowiada na pytanie „co mogę zrobić”,
    ale nie na pytanie „dlaczego nic”. Drugie jest tym, które człowiek zadaje, patrząc na szkic,
    którego nie da się wydać.

    `obraz` (27.09) to artefakt zrzutu dołączonego do własnego posta na X, tylko gdy jest:
    `tresc` jest wtedy tekstem bez linii obrazu, a zgoda obejmuje jedno i drugie.
    """
    return card(
        CARD_DRAFT,
        f"szkic:{revision}",
        state,
        reason=reason,
        actions=actions,
        rewizja=revision,
        tytul=title,
        spolecznosc=community,
        profil=profile,
        tresc=text,
        delegacja_pozwala=delegation_allows,
        dlaczego_bez_akcji=no_actions_because,
        **({"obraz": image} if image else {}),
    )


def draft_actions(
    revision_id: str,
    *,
    allowed: bool,
    workspace_id: Any,
    project_id: Any,
    text: str | None = None,
) -> list[dict[str, Any]]:
    """Akcje karty SZKIC. `popraw` ZAWSZE, `opublikuj` tylko wtedy, gdy powierzenie pozwala.

    Wyszarzony przycisk byłby obietnicą, że wystarczy kliknąć; przycisk celujący w trasę, której
    nie ma, byłby tą samą obietnicą, tylko odkrytą po kliknięciu. `popraw` niesie OBECNĄ treść,
    bo ekran ma czym wypełnić pole edycji, a nie zgadywać, co agent napisał.

    STOI W KONTRAKCIE KART, A NIE W REJESTRZE NARZĘDZI, bo kartę SZKIC buduje też trasa poprawki,
    a ona miała własną listę akcji z samym `opublikuj`. Skutek zmierzony przez wenext-ce na e2e
    21.09: po poprawce w projekcie bez powierzenia karta nie miała ŻADNEGO przycisku, więc
    człowiek nie mógł poprawić szkicu drugi raz, mimo że poprawianie nie wymaga powierzenia.
    Dwie listy akcji dla jednej karty to dwie definicje tego, co wolno.
    """
    base = f"/api/workspaces/{workspace_id}/projects/{project_id}/approvals/{revision_id}"
    akcje = [action("popraw", "POST", f"{base}/revise", {"text": text or ""})]
    if allowed:
        akcje.append(action("opublikuj", "POST", f"{base}/approve"))
    return akcje


def delegation_card(
    *,
    communities: list[str],
    allowed_actions: list[str],
    limits: dict[str, Any],
    state: str,
    rationale: str | None = None,
    actions: list[dict[str, Any]] | None = None,
    expires_at: str | None = None,
    goal: str | None = None,
    accounts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Karta POWIERZENIE. Jedna na projekt, bo tyle powierzeń projekt ma naraz.

    `wygasa` (0033) jest terminem, który podpis obejmie: człowiek widzi go na karcie, zanim
    kliknie, a ten sam moment jedzie w ciele podpisu. `cel` i `konta` odpowiadają na pytania
    „po co" i „którym kontem", które karta bez nich zostawiała rozmowie.
    """
    return card(
        CARD_DELEGATION,
        "powierzenie",
        state,
        actions=actions,
        spolecznosci=communities,
        dzialania=allowed_actions,
        limity=limits,
        uzasadnienie=rationale,
        wygasa=expires_at,
        cel=goal,
        konta=accounts or [],
    )


def fingerprint(cards: list[dict[str, Any]]) -> str:
    """`karta_id:stan` każdej karty, po kolei, sklejone `|`.

    Uzgodnione z ekranem (wenext-ce, 20.09): strumień niesie TYLKO ten napis, a ekran po jego
    zmianie przeładowuje stronę, zamiast rysować karty drugi raz z drugiej kopii reguł. Dlatego
    odcisk MUSI być stabilny dla niezmienionych kart i różny po każdej zmianie stanu, i dlatego
    nie wolno liczyć go z treści.
    """
    return "|".join(f"{one.get('karta_id')}:{one.get('stan')}" for one in cards)
