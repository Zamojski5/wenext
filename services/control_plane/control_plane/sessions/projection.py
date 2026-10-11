"""Karty i postęp jako PROJEKCJA śladu sesji i stanu produktu (D-046).

KONTRAKT: `docs/produkt/KARTY-ROZMOWY.md`. Ten moduł jest jego implementacją po stronie serwera,
a `tools/cards.py` trzyma kształt samej karty.

DWIE RZECZY, KTÓRE DECYDUJĄ O TYM, CZY TO DZIAŁA:

  1. ŚLAD MÓWI, JAKIE KARTY ISTNIEJĄ; PRODUKT MÓWI, W JAKIM SĄ STANIE. Karta powstaje, bo agent
     wywołał narzędzie, i to jest fakt z przeszłości, którego nic nie zmieni. Ale „czy to konto
     jest połączone" i „czy tę społeczność obserwujemy" to stan DZISIEJSZY, i gdyby karta niosła
     stan z chwili wywołania, po zalogowaniu konta w innej zakładce pokazywałaby „brak
     połączenia" do końca świata. Dlatego stan jest odświeżany z bazy przy każdym odczycie.
  2. POSTĘP DOTYCZY OSTATNIEJ TURY, nie całej rozmowy. Licznik liczy etapy ROZPOCZĘTE, nie
     zaplanowane: planu z góry nikt nie ma, a licznik „3 z 8", w którym ósemka jest zgadnięta,
     kłamie dwa razy, raz na początku i raz na końcu.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from control_plane.tools.cards import (
    ACCOUNT_CONNECTED,
    ACCOUNT_CONNECTING,
    ACCOUNT_DISCONNECTED,
    ACCOUNT_UNAVAILABLE,
    CARD_ACCOUNT,
    CARD_COMMUNITY,
    CARD_DELEGATION,
    CARD_DRAFT,
    CARD_ENGINE,
    COMMUNITY_OBSERVED,
    COMMUNITY_OBSERVING,
    COMMUNITY_PROPOSED,
    DELEGATION_SIGNED,
    DRAFT_NEW,
    DRAFT_PUBLISHED,
    DRAFT_PUBLISHING,
    NO_DELEGATION_NOTE,
    OBSERVATION_MEANS,
    STATE_ERROR,
    action,
)

#: Rodziny narzędzi i etap, którym są dla człowieka. Narzędzie spoza tej tabeli nie tworzy etapu
#: i nie psuje licznika: postęp pokazuje to, co umie nazwać.
STAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "poznaję projekt",
        (
            "project.read",
            "documents.list",
            "documents.read",
            "memory.search",
            "sessions.read",
            "communities.list",
        ),
    ),
    # Obsługa produktu z rozmowy (D-046). Bez tych dwóch wierszy scenariusz z briefu przechodził
    # BEZ POSTĘPU: agent sprawdzał konta i dołączał do społeczności, a ekran nie miał czego
    # pokazać, bo żadne z tych narzędzi nie nazywało etapu.
    ("sprawdzam konta", ("accounts.status", "accounts.connect")),
    ("dobieram społeczności", ("communities.observe", "communities.join", "communities.leave")),
    ("czytam rozmowy", ("threads.search", "thread.read", "comments.read")),
    ("oceniam", ("observations.read", "publications.list")),
    ("przygotowuję", ("draft.create", "drafts.publish", "delegation.propose")),
)

_STAGE_OF: dict[str, str] = {tool: stage for stage, tools in STAGES for tool in tools}

ITEM_PENDING = "oczekuje"
ITEM_RUNNING = "trwa"
ITEM_DONE = "zrobione"
ITEM_ERROR = "blad"


def _payload(event: Any) -> dict[str, Any]:
    payload = getattr(event, "payload", None)
    return payload if isinstance(payload, dict) else {}


def cards_from_trace(events: list[Any]) -> list[dict[str, Any]]:
    """Karty w kolejności pierwszego wystąpienia, każda w stanie z OSTATNIEGO wystąpienia.

    Jedna karta na przedmiot, bo `karta_id` bierze się z przedmiotu, a nie z numeru zdarzenia.
    Dlatego ponowne wywołanie tego samego narzędzia nie tworzy drugiej karty.
    """
    known: dict[str, dict[str, Any]] = {}
    for event in events:
        # KAŻDE ZDARZENIE, KTÓRE NIESIE KARTY, a nie tylko wynik narzędzia. Poprawkę tekstu
        # robi CZŁOWIEK trasą `…/approvals/{rewizja}/revise`, a nie agent narzędziem; gdyby
        # projekcja czytała wyłącznie TOOL_RESULT, nowa wersja szkicu nie miałaby skąd się wziąć
        # i człowiek widziałby swoją starą treść z adnotacją, że istnieje nowsza (zgłoszenie
        # wenext-ce z dowodem z bazy, 20.09).
        for card in _payload(event).get("karty") or []:
            if not isinstance(card, dict) or not card.get("karta_id"):
                continue
            identifier = str(card["karta_id"])
            first_seq = known.get(identifier, {}).get("od_seq", getattr(event, "seq", 0))
            known[identifier] = {**card, "od_seq": first_seq}
    return list(known.values())


async def refresh_states(
    pool: Any,
    cards: list[dict[str, Any]],
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
) -> list[dict[str, Any]]:
    """Podmień stany kart na DZISIEJSZE, czytając produkt.

    Karta w stanie `blad` zostaje w błędzie: to jest zdanie o tym, co się wydarzyło przy próbie,
    a nie o tym, jak jest teraz, i skasowanie go przy odświeżeniu ukryłoby jedyny ślad odmowy.

    `project_id` jest tu po to, żeby odświeżyć ODPOWIEDŹ NA PYTANIE „czy wolno to wydać".
    Karta SZKIC niosła `delegacja_pozwala` i przycisk z chwili, w której powstała, a powierzenie
    da się w międzyczasie odwołać albo wstrzymać. Zmierzone na zrzutach 20.09 (zgłoszenie
    wenext-ce): projekt bez podpisanej delegacji pokazywał „Opublikuj" na karcie ze śladu, a
    kliknięcie kończyło się odmową z trasy zgody.
    """
    accounts: dict[str, str] = {}
    communities: dict[str, bool] = {}
    drafts: dict[str, dict[str, Any]] = {}
    may_publish = False
    delegation = None
    wanted_accounts = [one for one in cards if one.get("rodzaj") == CARD_ACCOUNT]
    wanted_communities = [one for one in cards if one.get("rodzaj") == CARD_COMMUNITY]
    # KAŻDA karta szkicu, także ta z nieczytelną rewizją. Pytanie „czy wolno to wydać" dotyczy
    # PROJEKTU, nie rewizji, więc wiązanie go z listą czytelnych identyfikatorów kazałoby zdjąć
    # przycisk przy żywym powierzeniu tylko dlatego, że karta ma w polu coś innego niż UUID.
    has_drafts = any(one.get("rodzaj") == CARD_DRAFT for one in cards)
    has_delegation = any(one.get("rodzaj") == CARD_DELEGATION for one in cards)
    # UUID-y, nie napisy: parametr jedzie do `= ANY($3::uuid[])`, a tablica w asyncpg nie
    # konwertuje elementów tak łagodnie jak pojedyncza wartość. Rewizja nie do przeczytania jako
    # UUID jest pomijana, bo to znaczy, że karta w śladzie ma coś innego niż identyfikator.
    wanted_drafts: list[UUID] = []
    for one in cards:
        if one.get("rodzaj") != CARD_DRAFT or not one.get("rewizja"):
            continue
        try:
            wanted_drafts.append(UUID(str(one["rewizja"])))
        except ValueError:
            continue
    project_accounts: dict[str, str] = {}
    async with pool.acquire() as conn:
        if wanted_accounts:
            rows = await conn.fetch(
                "SELECT platform, external_account_id, state FROM social_accounts "
                "WHERE organization_id = $1",
                organization_id,
            )
            accounts = {
                f"{r['platform']}:{r['external_account_id']}": str(r["state"]) for r in rows
            }
            if any(not one.get("konto") for one in wanted_accounts):
                # The first account of each platform assigned to THIS project, for the cards that
                # asked for a new one. Scoped by project, never "any account of the platform".
                for r in await conn.fetch(
                    "SELECT a.platform, a.external_account_id "
                    "FROM project_account_assignments x JOIN social_accounts a "
                    "ON a.organization_id = x.organization_id AND a.id = x.social_account_id "
                    "WHERE x.organization_id = $1 AND x.workspace_id = $2 AND x.project_id = $3 "
                    "AND x.unassigned_at IS NULL ORDER BY x.assigned_at",
                    organization_id,
                    workspace_id,
                    project_id,
                ):
                    project_accounts.setdefault(str(r["platform"]), str(r["external_account_id"]))
        if wanted_communities:
            rows = await conn.fetch(
                # `last_polled_at` jedzie razem ze stanem obserwacji, bo karta rozróżnia
                # „włączyłem" od „mam stamtąd wątki", a to jest różnica, którą widać dopiero po
                # pierwszym odczycie. Jedno zapytanie na oba pola: drugie byłoby drugim
                # miejscem, w którym trzeba pamiętać o tej samej granicy workspace'u.
                "SELECT platform, community_key, observe_enabled, last_polled_at "
                "FROM communities WHERE organization_id = $1 AND workspace_id = $2",
                organization_id,
                workspace_id,
            )
            communities = {
                f"{r['platform']}:{r['community_key']}": (
                    bool(r["observe_enabled"]),
                    r["last_polled_at"] is not None,
                )
                for r in rows
            }
        if has_drafts or has_delegation:
            # JEDNO PYTANIE O POWIERZENIE, tylko gdy są szkice. Ta sama reguła, którą stosuje
            # trasa zgody (`api/project_approvals.py`): żywa delegacja tego projektu. Gdyby
            # karta liczyła to po swojemu, ekran i trasa mówiłyby dwie różne rzeczy o jednym
            # kliknięciu, a człowiek dowiedziałby się o różnicy dopiero po nim.
            from control_plane.presence.store import PresenceStore

            delegation = await PresenceStore(pool).delegation_for(
                organization_id=organization_id, project_id=project_id
            )
            may_publish = (
                delegation is not None and delegation.live
                and delegation.mode != "RESEARCH"
            )
        if wanted_drafts:
            # JEDNO ZAPYTANIE NA WSZYSTKIE SZKICE. Rewizja wie tylko, w jakim jest stanie; o tym,
            # co się z nią stało DALEJ, wie wniosek publikacyjny (jeden na rewizję, unikat w
            # 0007), a o skutku na platformie dowód wykonania.
            rows = await conn.fetch(
                """
                SELECT r.id AS revision_id, r.state AS revision_state, r.proposal_id,
                       q.status, q.detail, q.public_visibility,
                       q.public_visibility_checked_at, e.permalink
                  FROM action_proposal_revisions r
                  LEFT JOIN publication_requests q
                    ON q.proposal_revision_id = r.id AND q.organization_id = r.organization_id
                  LEFT JOIN execution_evidence e
                    ON e.attempt_id = q.attempt_id AND e.organization_id = r.organization_id
                 WHERE r.organization_id = $1 AND r.workspace_id = $2
                   AND r.id = ANY($3::uuid[])
                """,
                organization_id,
                workspace_id,
                wanted_drafts,
            )
            drafts = {str(r["revision_id"]): dict(r) for r in rows}

    refreshed: list[dict[str, Any]] = []
    for card in cards:
        if card.get("stan") == STATE_ERROR:
            refreshed.append(card)
            continue
        if card.get("rodzaj") == CARD_DELEGATION:
            refreshed.append(_delegation_now(card, delegation))
            continue
        if card.get("rodzaj") == CARD_ACCOUNT:
            platform = str(card.get("platforma") or "")
            login = str(card.get("konto") or "")
            if not login and not card.get("brak_konta"):
                # KARTA NOWEGO KONTA (X z rozmowy, 26.09) nie zna uchwytu: pozna go dopiero
                # sprawdzenie sesji, które zapisze konto i przypisze je do projektu. Po tym
                # karta ma pokazać TO konto, a nie wisieć jako „niepołączone” na zawsze.
                login = project_accounts.get(platform, "")
            state = accounts.get(f"{platform}:{login}") if login else None
            stan = _ACCOUNT_STATE.get(state or "", ACCOUNT_DISCONNECTED)
            if stan in (ACCOUNT_DISCONNECTED, ACCOUNT_CONNECTING):
                # AKCJE KARTY ZOSTAJĄ TAKIE, JAKIE ZBUDOWAŁO NARZĘDZIE: „połącz” z platformą i
                # „sprawdź” ze ścieżką tej platformy. Do 26.09 projekcja podmieniała je na samo
                # „połącz” bez platformy, więc drugi przycisk znikał po pierwszym odświeżeniu.
                akcje = card.get("akcje") or [
                    action("polacz", "POST", "/api/capsule/session", {"platform": platform})
                ]
            else:
                akcje = []
            extra = (
                {"konto": login, "polaczone_jako": login}
                if stan == ACCOUNT_CONNECTED and login
                else {}
            )
            refreshed.append({**card, **extra, "stan": stan, "akcje": akcje})
            continue
        if card.get("rodzaj") == CARD_COMMUNITY:
            key = f"{card.get('platforma')}:{card.get('klucz')}"
            known = communities.get(key)
            # CZŁONKOSTWO KONTA NIE MA SIĘ SKĄD WZIĄĆ, więc projekcja go nie zgaduje: `dolaczam`
            # i `dolaczono` istnieją w słowniku i nie padają tutaj, dopóki nie ma wykonawcy ani
            # miejsca, w którym potwierdzony skutek jest zapisany (E3b). Karta, która wyliczyłaby
            # ten stan z obserwacji, byłaby tym samym kłamstwem, tyle że w drugim pliku.
            if known is None:
                refreshed.append(
                    {
                        **card,
                        "stan": COMMUNITY_PROPOSED,
                        "obserwacja": False,
                        "akcje": _observe_action(card),
                    }
                )
                continue
            observing, read_already = known
            if not observing:
                refreshed.append(
                    {
                        **card,
                        "stan": COMMUNITY_PROPOSED,
                        "obserwacja": False,
                        "akcje": _observe_action(card),
                    }
                )
                continue
            refreshed.append(
                {
                    **card,
                    "stan": COMMUNITY_OBSERVED if read_already else COMMUNITY_OBSERVING,
                    "obserwacja": True,
                    "co_to_znaczy": OBSERVATION_MEANS,
                    "akcje": [],
                }
            )
            continue
        if card.get("rodzaj") == CARD_DRAFT:
            refreshed.append(
                _draft_now(card, drafts.get(str(card.get("rewizja"))), may_publish=may_publish)
            )
            continue
        if card.get("rodzaj") == CARD_ENGINE:
            # Stan silnika bierze się z wiersza połączenia, nie z chwili, w której tura odkryła
            # jego brak. Bez tego karta pokazywałaby „brak połączenia" po zalogowaniu, aż do
            # następnej tury, czyli dokładnie w momencie, w którym człowiek właśnie coś zrobił.
            from control_plane.sessions.engine_card import card_for_organization

            fresh, _ = await card_for_organization(
                pool, organization_id=organization_id, workspace_id=workspace_id
            )
            refreshed.append({**fresh, "od_seq": card.get("od_seq", 0)})
            continue
        refreshed.append(card)
    return refreshed


def _delegation_now(card: dict[str, Any], delegation: Any) -> dict[str, Any]:
    """A click takes effect immediately; render the actual scope, not the old proposal."""
    if delegation is None:
        if card.get("stan") == DELEGATION_SIGNED:
            return {**card, "stan": STATE_ERROR, "akcje": [],
                    "powod": "Praca w tle nie ma już aktywnego powierzenia."}
        return card
    for proposed in card.get("akcje", []):
        body = proposed.get("payload") or {}
        if body.get("replaces") == str(delegation.id):
            return card
    if delegation.expired:
        # 0033: termin minął i produkt sam zatrzymał pracę w tle. To nie jest wstrzymanie, które
        # da się wznowić przyciskiem; dalsza praca wymaga nowego podpisu z nowym terminem.
        return {**card, "stan": STATE_ERROR, "akcje": [],
                "wygasa": delegation.expires_at.isoformat() if delegation.expires_at else None,
                "powod": "Powierzenie wygasło, więc praca w tle stoi. Dalsza praca wymaga "
                "nowego podpisu z nowym terminem."}
    if not delegation.live:
        return {**card, "stan": STATE_ERROR, "akcje": [],
                "powod": "Praca w tle jest wstrzymana. Zakres możesz sprawdzić w Aktywności."}
    from control_plane.presence.budget import effective_limits

    limits = effective_limits(delegation).to_json()
    if delegation.budget.scheduled_returns is not None:
        limits["zaplanowane_powroty"] = delegation.budget.scheduled_returns
    return {
        **card, "stan": DELEGATION_SIGNED, "akcje": [],
        "spolecznosci": list(delegation.scope.communities),
        "dzialania": list(delegation.scope.actions), "limity": limits,
        "uzasadnienie": None,
        "wygasa": delegation.expires_at.isoformat() if delegation.expires_at else None,
    }


#: Stany wniosku publikacyjnego (0007) w przekładzie na stan karty. `QUEUED` i `PUBLISHING` to dla
#: człowieka jedno: „poszło, czekam”. Cztery pozostałe to niepowodzenia o różnych powodach, więc
#: karta idzie w `blad`, a powód bierze się z `detail` wniosku.
_DRAFT_FROM_REQUEST = {
    "QUEUED": DRAFT_PUBLISHING,
    "PUBLISHING": DRAFT_PUBLISHING,
    "PUBLISHED": DRAFT_PUBLISHED,
    "UNKNOWN": STATE_ERROR,
    "BLOCKED": STATE_ERROR,
    "SKIPPED": STATE_ERROR,
    "STOPPED": STATE_ERROR,
}

#: Rewizje, które przestały być aktualne. Karta zostaje w `szkic`, bo tekst dalej jest do
#: przeczytania, ale BEZ akcji: w zastąpionej wersji nie ma czego zatwierdzać ani poprawiać.
#: Osobnego stanu nie wprowadzam sam, bo słownik stanów jest wspólny z ekranem i rośnie po
#: uzgodnieniu, a nie przy okazji.
_REVISION_IS_HISTORY = ("REJECTED", "SUPERSEDED", "EXPIRED")


def _visibility_sentence(verdict: str | None, checked_at: Any) -> str | None:
    """Widoczność publikacji jednym zdaniem. `UNVERIFIED` mówi „nie sprawdzono”, nie „nie ma”."""
    if verdict is None:
        return None
    moment = "" if checked_at is None else f", sprawdzone {checked_at:%Y-%m-%d %H:%M}"
    if verdict == "CONFIRMED":
        return f"Widoczna publicznie{moment}."
    if verdict == "REMOVED":
        return f"Zniknęła z widoku publicznego{moment}."
    return "Nikt jeszcze nie sprawdzał, czy jest widoczna publicznie."


def _publishable(card: dict[str, Any], may_publish: bool) -> dict[str, Any]:
    """Karta SZKIC z odświeżoną odpowiedzią na „czy wolno to wydać".

    Karta w błędzie i karta już wydana nie mają tu nic do zyskania: pierwsza mówi o tym, co się
    wydarzyło, druga nie ma przycisków. Zmieniamy więc tylko szkic, i zmieniamy w OBIE strony,
    bo powierzenie da się zarówno odwołać, jak i podpisać, a karta ma mówić to, co zrobi
    kliknięcie.
    """
    if card.get("stan") != DRAFT_NEW:
        return card
    if may_publish:
        return card
    return {
        **card,
        "delegacja_pozwala": False,
        "akcje": [one for one in (card.get("akcje") or []) if one.get("nazwa") != "opublikuj"],
        "dlaczego_bez_akcji": card.get("dlaczego_bez_akcji") or NO_DELEGATION_NOTE,
    }


def _draft_now(
    card: dict[str, Any], row: dict[str, Any] | None, *, may_publish: bool
) -> dict[str, Any]:
    """Stan karty SZKIC z BAZY, nie z chwili, w której narzędzie ją zwróciło.

    Bez tego karta zostawała na „publikuje” po tym, jak publikator naprawdę opublikował, i
    wyglądała jak zawieszona. Kolejność pytań jest istotna: najpierw wniosek publikacyjny, bo on
    wie najwięcej, a dopiero przy jego braku stan samej rewizji.

    `may_publish` odświeża DRUGĄ rzecz, której karta nie miała jak wiedzieć: czy projekt ma
    dziś żywe powierzenie. Dotyczy tylko szkicu, który jeszcze nigdzie nie poszedł; po wydaniu
    wniosku przyciski i tak znikają, bo nie ma czego zatwierdzać drugi raz. Jest WYMAGANY i nie
    ma wartości domyślnej: `False` po cichu znaczyłoby „bez powierzenia" i zdejmowałoby przycisk
    każdemu, kto zapomniał zapytać, a to jest ta sama nieprawda, tylko w drugą stronę.
    """
    card = _publishable(card, may_publish)
    if row is None:
        return card
    # Pola techniczne z podkreśleniem NIE JADĄ NA EKRAN: `_without_replaced_drafts` zdejmuje je
    # tuż przed zwróceniem listy. Są tu po to, żeby decyzja „pokaż albo schowaj" miała z czego
    # powstać, a karta nie rosła o pola, których ekran nie używa.
    card = {**card, "_propozycja": str(row.get("proposal_id") or "")}
    status = row.get("status")
    if status is None:
        state = str(row.get("revision_state") or "")
        if state in _REVISION_IS_HISTORY:
            return {
                **card,
                "stan": DRAFT_NEW,
                "powod": None,
                "akcje": [],
                "dlaczego_bez_akcji": (
                    "Ta wersja została zastąpiona; pracujemy na nowszej wersji tej propozycji."
                ),
                "_zastapiona": True,
            }
        if state == "APPROVED":
            # Zgoda jest, wniosku nie ma. Nie zgaduję, że publikacja trwa: w tym produkcie wniosek
            # powstaje razem ze zgodą, więc jego brak jest niezgodnością do pokazania, nie do
            # zamaskowania stanem pośrednim.
            return {
                **card,
                "stan": STATE_ERROR,
                "powod": "propozycja jest zatwierdzona, ale nie ma wniosku publikacyjnego",
                "akcje": [],
            }
        return card
    fresh = _DRAFT_FROM_REQUEST.get(str(status), STATE_ERROR)
    updated = {**card, "stan": fresh, "akcje": [], "powod": None}
    if fresh == STATE_ERROR:
        updated["powod"] = str(row.get("detail") or f"publikacja zatrzymana w stanie {status}")
        return updated
    if fresh == DRAFT_PUBLISHED:
        updated["adres"] = row.get("permalink")
        updated["widocznosc"] = _visibility_sentence(
            row.get("public_visibility"), row.get("public_visibility_checked_at")
        )
    updated["dlaczego_bez_akcji"] = (
        "Wniosek stoi w kolejce publikatora; „opublikowano” pojawi się po potwierdzeniu skutku."
        if fresh == DRAFT_PUBLISHING
        else None
    )
    return updated


#: To samo mapowanie, co w `tools/registry.py`, i pilnuje go test: gdyby karta z narzędzia i
#: karta z projekcji mówiły o jednym koncie dwa różne stany, ekran migałby przy każdym odczycie.
_ACCOUNT_STATE = {
    "READY": ACCOUNT_CONNECTED,
    "VERIFYING": ACCOUNT_CONNECTING,
    "PROVISIONED": ACCOUNT_DISCONNECTED,
    "REAUTH_REQUIRED": ACCOUNT_DISCONNECTED,
    "PAUSED": ACCOUNT_UNAVAILABLE,
    "QUARANTINED": ACCOUNT_UNAVAILABLE,
}


def _observe_action(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Jedyna akcja, jaką karta społeczności dziś oferuje: włącz obserwację.

    „Dołącz kontem" NIE stoi obok, i nie dlatego, że o nim zapomniano: nie ma trasy, która by je
    wykonała, a akcja bez trasy to przycisk, który po kliknięciu musi przeprosić. Kontrakt kart
    wymaga trasy przy każdej akcji właśnie po to, żeby taki przycisk nie mógł powstać.
    """
    return [
        action(
            "obserwuj",
            "POST",
            "/api/communities",
            {"platform": card.get("platforma"), "community_key": card.get("klucz")},
        )
    ]


async def cards_for(
    pool: Any, events: list[Any], *, organization_id: UUID, workspace_id: UUID, project_id: UUID
) -> list[dict[str, Any]]:
    """Karty tej rozmowy: tożsamość i kolejność ze śladu, stan z produktu."""
    from_trace = cards_from_trace(events)
    if not from_trace:
        return []
    fresh = await refresh_states(
        pool,
        from_trace,
        organization_id=organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    return sorted(_without_replaced_drafts(fresh), key=lambda one: one.get("od_seq", 0))


def _without_replaced_drafts(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Zastąpiona wersja szkicu znika DOPIERO wtedy, gdy widać jej następczynię.

    Dwie karty tej samej propozycji, jedna zastąpiona i jedna aktualna, to dla człowieka jedna
    rzecz pokazana dwa razy. Ale zastąpiona bez następczyni musi zostać: wtedy jest jedynym
    śladem tego, co napisał agent, a jej zniknięcie wyglądałoby na utratę pracy.
    """
    current = {
        one.get("_propozycja")
        for one in cards
        if one.get("rodzaj") == CARD_DRAFT and not one.get("_zastapiona") and one.get("_propozycja")
    }
    out: list[dict[str, Any]] = []
    for card in cards:
        if (
            card.get("rodzaj") == CARD_DRAFT
            and card.get("_zastapiona")
            and card.get("_propozycja") in current
        ):
            continue
        out.append({key: value for key, value in card.items() if not key.startswith("_")})
    return out


def progress_for(events: list[Any]) -> dict[str, Any] | None:
    """Postęp OSTATNIEJ tury, albo `None`, gdy ta tura nie zawołała nic, co umiemy nazwać."""
    last_message = 0
    for event in events:
        if getattr(event, "kind", None) == "USER_MESSAGE":
            last_message = max(last_message, getattr(event, "seq", 0))
    turn = [one for one in events if getattr(one, "seq", 0) > last_message]

    stages: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    last_line = ""
    for event in turn:
        kind = getattr(event, "kind", None)
        if kind not in ("TOOL_CALL", "TOOL_RESULT"):
            continue
        payload = _payload(event)
        tool = str(payload.get("tool") or "")
        stage = _STAGE_OF.get(tool)
        if stage is None:
            continue
        if stage not in stages:
            stages[stage] = {
                "etykieta": stage,
                "stan": ITEM_PENDING,
                "podlinia": None,
                "zrodla": [],
            }
            order.append(stage)
        item = stages[stage]
        summary = payload.get("summary")
        if kind == "TOOL_CALL":
            item["stan"] = ITEM_RUNNING
            for source in _sources_of(payload.get("arguments")):
                if source not in item["zrodla"]:
                    item["zrodla"].append(source)
        else:
            failed = payload.get("status") == "failed" or payload.get("error")
            item["stan"] = ITEM_ERROR if failed else ITEM_DONE
            if summary:
                item["podlinia"] = str(summary)
        if summary:
            last_line = str(summary)

    if not order:
        return None
    items = [stages[name] for name in order]
    done = len([one for one in items if one["stan"] == ITEM_DONE])
    running = next((one for one in items if one["stan"] == ITEM_RUNNING), None)
    return {
        "naglowek": running["etykieta"] if running else items[-1]["etykieta"],
        "licznik": {"zrobione": done, "wszystkie": len(items)},
        "pozycje": items,
        "linia_zdarzenia": last_line or None,
    }


def _sources_of(arguments: Any) -> list[str]:
    """Chipy źródeł: nazwy społeczności i wątków z argumentów wywołania."""
    if not isinstance(arguments, dict):
        return []
    found: list[str] = []
    for key in ("community", "communities", "thread_id", "threads"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
        elif isinstance(value, list):
            found.extend(str(one) for one in value if isinstance(one, str) and one.strip())
    return found
