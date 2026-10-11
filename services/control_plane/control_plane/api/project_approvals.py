"""Człowiek zatwierdza i poprawia propozycję Z EKRANU (D-046, karta SZKIC).

DLACZEGO OSOBNY PLIK, A NIE `api/approvals.py`. Tamten moduł zaczyna się od zdania, że nie ma w
nim Approve ani Edit, i to była świadoma granica zakresu poprzedniej fazy. Pivot ją przesunął:
rozmowa ma karty z przyciskami, a przycisk bez trasy jest obietnicą, która pęka po kliknięciu.
Zamiast przepisywać tamten nagłówek na nieprawdę, akcje projektowe mieszkają tutaj, pod ścieżką
projektu, i tamten plik zostaje tym, czym jest: odczytem kolejki całej organizacji.

TRZY RZECZY, KTÓRE TE DWIE TRASY TRZYMAJĄ:

  1. ZGODA CZŁOWIEKA IDZIE TĄ SAMĄ DROGĄ, CO ZGODA POWIERZENIA. Wpis do `approval_decisions`,
     potem wpis do kolejki publikatora, oba pod tym samym zamkiem projektu, którego używa
     `presence/approval.py`. Dwie drogi do jednej kolejki byłyby dwiema definicjami tego, co
     wolno opublikować.
  2. LIMITY PODPISU WIĄŻĄ TAKŻE CZŁOWIEKA. Zatwierdzenie z ekranu nie omija pace'u, który
     założyciel podpisał: gdyby omijało, „sześć publikacji na dobę” znaczyłoby „sześć, chyba że
     ktoś kliknie”.
  3. PUBLIKACJA TO NIE ZATWIERDZENIE. Karta wraca w stanie `publikuje`, nigdy `opublikowano`:
     skutek potwierdza dopiero publikator odczytem zwrotnym.

CZEGO TU NIE MA: odrzucenia. Propozycja, której człowiek nie chce, wygasa sama po swoim oknie
zgody, a kasowanie jej ręcznie kasowałoby jedyny ślad tego, co agent proponował.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any
from uuid import UUID

from community_os_contracts.execution import ActionProposalState, ApprovalHashInput
from community_os_contracts.transitions import IllegalTransition
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane.approval.store import ApprovalStore, ApprovalStoreError
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.pilot.publication import join_media, split_media
from control_plane.presence.approval import ACTION_OF_CAPABILITY, ACTION_OWN_POST
from control_plane.presence.human_approval import (
    HumanApprovalRefused,
    HumanSigner,
    approve_and_queue,
    payload_of,
    revision_row,
)
from control_plane.presence.store import PresenceStore
from control_plane.tools.cards import (
    DRAFT_NEW,
    DRAFT_PUBLISHING,
    NO_DELEGATION_NOTE,
    draft_actions,
    draft_card,
)
from control_plane.workspace import WorkspaceStore

_LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["approvals"])

APPROVALS_PATH = "/workspaces/{workspace_id}/projects/{project_id}/approvals/{revision_id}"

#: Ile znaków może mieć poprawka człowieka. Ta sama granica, co przy celu sesji: tekst z sieci
#: alokuje, a odpowiedź dłuższa niż to nie jest odpowiedzią.
MAX_TEXT = 4000


class ReviseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_TEXT)


async def _resolve_project(
    request: Request, operator: OperatorSession, workspace_id: UUID, project_id: UUID
) -> None:
    """THE gate, te same dwa kroki, co w każdej trasie projektowej."""
    pool = await pool_for(request.app)
    if (
        await WorkspaceStore(pool).workspace(
            organization_id=operator.organization_id, workspace_id=workspace_id
        )
        is None
    ):
        raise HTTPException(status_code=404, detail=f"nie ma workspace'u {workspace_id}")
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM projects WHERE organization_id = $1 AND workspace_id = $2 AND id = $3",
            operator.organization_id,
            workspace_id,
            project_id,
        )
    if not exists:
        raise HTTPException(status_code=404, detail=f"nie ma projektu {project_id}")


async def _revision_row(
    request: Request,
    operator: OperatorSession,
    workspace_id: UUID,
    project_id: UUID,
    revision_id: UUID,
) -> Any:
    """Rewizja, która NALEŻY do tego projektu. Cudza jest nieznaleziona, a nie zabroniona."""
    pool = await pool_for(request.app)
    row = await revision_row(
        pool,
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
        revision_id=revision_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"nie ma propozycji {revision_id}")
    return row


_payload_of = payload_of


#: Identyfikator rzeczy na Reddicie: `t3_abc123` to wątek, `t1_...` komentarz. Taki napis NIGDY
#: nie jest nazwą społeczności, i to jest jedyne, do czego ten wzorzec tu służy.
_FULLNAME = re.compile(r"^t[0-9]+_[A-Za-z0-9]+$")


async def _community_caption(
    pool: Any, *, organization_id: UUID, workspace_id: UUID, payload: dict[str, Any]
) -> str | None:
    """Nazwa społeczności do podpisu karty, albo `None`. NIGDY identyfikator celu.

    PO CO TO ISTNIEJE (zrzut rundy 2, 21.09). Obie trasy podawały w `spolecznosc` wprost
    `target_external_id`, więc po poprawce karta miała w podpisie `t3_e2edraft` zamiast
    `r/saas_e2e`. Tytuł naprawiono dzień wcześniej, pole obok zostało: ta sama rodzina pomyłki.

    DWA RODZAJE CELU, i dlatego to nie jest jedno podstawienie. WŁASNY POST celuje w społeczność,
    więc jego `target_external_id` JEST nazwą (`r/ecommerce`, `registry.py` składa z niej także
    adres `/submit`) i wolno jej użyć. ODPOWIEDŹ celuje w wątek, więc nazwę trzeba odczytać z
    bazy po wątku, który obserwowaliśmy.

    Czego ta funkcja nie robi: nie zgaduje nazwy z adresu ani z klucza projektu. Brak wiersza to
    brak podpisu, bo pusty podpis mówi „nie wiem", a identyfikator mówi nieprawdę.
    """
    target = str(payload.get("target_external_id") or "").strip()
    if target and not _FULLNAME.match(target):
        return target
    if not target:
        return None
    # KLUCZ PRZED NAZWĄ WYŚWIETLANĄ, i to nie jest obojętne. W bazie stoją obie postacie
    # (`community_key = "r/ecommerce"`, `display_name = "Reddit · r/ecommerce"`), a karta, którą
    # agent zbudował wcześniej w tej samej rozmowie, niesie KLUCZ, bo tak nazwał społeczność
    # człowiek. Dwie nazwy jednej społeczności w jednej rozmowie czytałoby się jak dwa miejsca.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT c.display_name, c.community_key
              FROM observed_threads t
              JOIN communities c
                ON c.id = t.community_id
               AND c.organization_id = t.organization_id
               AND c.workspace_id = t.workspace_id
             WHERE t.organization_id = $1 AND t.workspace_id = $2 AND t.external_id = $3
             LIMIT 1
            """,
            organization_id,
            workspace_id,
            target,
        )
    if row is None:
        return None
    return str(row["community_key"] or "").strip() or str(row["display_name"] or "").strip() or None


@router.post(APPROVALS_PATH + "/approve", status_code=202)
async def approve_revision(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    revision_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Zgoda człowieka na tę propozycję, a po niej wpis do kolejki publikatora.

    SAMA ZGODA JEST W `presence/human_approval.py`, bo tę samą drogę przechodzi karta w agencie
    człowieka (`tools/card_send.py`): dwie kopie tych kroków byłyby dwiema definicjami tego, co
    wolno opublikować. Ta trasa dokłada to, co jest jej własne: bramkę projektu, operatora z
    sesji i kartę w odpowiedzi.
    """
    await _resolve_project(request, operator, workspace_id, project_id)
    row = await _revision_row(request, operator, workspace_id, project_id, revision_id)
    payload = _payload_of(row)
    pool = await pool_for(request.app)
    reason = f"zgoda człowieka {operator.operator_id} na ekranie rozmowy"

    try:
        issued = await approve_and_queue(
            pool,
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            row=row,
            signer=HumanSigner(operator_id=operator.operator_id, user_id=operator.user_id),
            reason=reason,
        )
    except HumanApprovalRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    caption = await _community_caption(
        pool,
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        payload=payload,
    )
    # The card shows the text that goes on the platform; an attached image is named apart.
    shown_text, shown_media = split_media(str(payload.get("text_and_media") or ""))
    return {
        "karta": draft_card(
            revision=str(revision_id),
            # Ta sama zasada, co przy poprawce: `tytul` to tytuł, a nie identyfikator celu.
            title=_draft_title(payload, shown_text),
            community=caption,
            profile=None,
            text=shown_text or None,
            image=str(shown_media.artifact_id) if shown_media is not None else None,
            state=DRAFT_PUBLISHING,
            delegation_allows=True,
            actions=[],
            no_actions_because=(
                "Wniosek stoi w kolejce publikatora. „Opublikowano” pojawi się dopiero po "
                "potwierdzeniu skutku odczytem zwrotnym."
            ),
        ),
        "wniosek": str(issued.request_id),
    }


@router.post(APPROVALS_PATH + "/revise", status_code=201)
async def revise_revision(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    revision_id: UUID,
    body: ReviseBody,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Poprawka człowieka: NOWA rewizja tej samej propozycji, z nim jako autorem.

    Poprzednia zostaje w bazie jako `SUPERSEDED`, razem ze zgodą, która jej dotyczyła, jeżeli
    jakaś była. Nadpisanie tekstu w miejscu skasowałoby zapis tego, co agent naprawdę napisał, a
    to jest jedyny materiał, na którym da się oceniać jego pracę.
    """
    await _resolve_project(request, operator, workspace_id, project_id)
    row = await _revision_row(request, operator, workspace_id, project_id, revision_id)
    payload = _payload_of(row)
    pool = await pool_for(request.app)
    approvals = ApprovalStore(pool)

    consented = str(payload.get("text_and_media") or "")
    _, media = split_media(consented)
    corrected = body.text.strip()
    # The card shows the text without the image line (27.09), so a correction arrives without
    # it and the image the agent attached stays attached. Another image is another draft.
    text = (
        join_media(corrected, media)
        if media is not None and split_media(corrected)[1] is None
        else corrected
    )
    if text == consented.strip():
        raise HTTPException(status_code=409, detail="poprawka nie zmienia treści")
    shown_text, shown_media = split_media(text)

    new_input = _hash_input_with_text(payload, text)
    actor = f"operator:{operator.operator_id}"
    state = str(row["state"])
    try:
        if state == ActionProposalState.APPROVED.value:
            # PO ZGODZIE edycja ma swoją własną, opisaną ścieżkę: poprzednia rewizja idzie w
            # SUPERSEDED, a zgoda, która jej dotyczyła, przestaje cokolwiek autoryzować.
            _, successor = await approvals.edit_into_new_revision(
                revision_id=revision_id,
                organization_id=operator.organization_id,
                workspace_id=workspace_id,
                new_hash_input=new_input,
                actor_ref=actor,
            )
            ready = await approvals.move_revision(
                successor.id,
                organization_id=operator.organization_id,
                workspace_id=workspace_id,
                target=ActionProposalState.READY_FOR_APPROVAL,
                actor_ref=actor,
            )
        else:
            # PRZED ZGODĄ nie ma strzałki do SUPERSEDED (L2775: jedyne wejście do tego stanu to
            # APPROVED). Poprawka jest więc tym, czym naprawdę jest: człowiek odrzuca wersję
            # agenta i pisze własną jako KOLEJNĄ rewizję tej samej propozycji. Historia zostaje
            # w komplecie, bo to ona jest jedynym materiałem do oceny pracy agenta.
            await approvals.move_revision(
                revision_id,
                organization_id=operator.organization_id,
                workspace_id=workspace_id,
                target=ActionProposalState.REJECTED,
                actor_ref=actor,
            )
            ready = await approvals.record_revision(
                organization_id=operator.organization_id,
                workspace_id=workspace_id,
                proposal_id=row["proposal_id"],
                revision_no=int(row["revision"]) + 1,
                hash_input=new_input,
                state=ActionProposalState.READY_FOR_APPROVAL,
                # OKNO ZGODY SIĘ NIE PRZEDŁUŻA (L5539): poprawka jest poprawką tej samej
                # propozycji, a nie nową sprawą z nowym terminem.
                expires_at=row["expires_at"],
            )
    except ApprovalStoreError as exc:
        raise HTTPException(status_code=409, detail=f"poprawka nie przeszła: {exc}") from exc
    except IllegalTransition as exc:
        # POWTÓRZONA POPRAWKA, czyli drugie kliknięcie na karcie, której ekran jeszcze nie
        # odświeżył. Rewizja stoi już w REJECTED albo SUPERSEDED, a maszyna stanów nie ma z nich
        # strzałki; `IllegalTransition` nie dziedziczy po `ApprovalStoreError`, więc lądowało
        # wyżej jako 500 z pełnym śladem wyjątku w logu. Nic się przy tym nie psuło i nic nie
        # ginęło, ale 500 znaczy „serwer się wywrócił", a tu wywrócił się tylko człowiek w
        # kliknięcie (zgłoszenie wenext-ce, odtworzenie: e2e
        # `clicking_the_stale_correction_again_makes_no_second_proposal`).
        raise HTTPException(
            status_code=409,
            detail=(
                "ta wersja szkicu nie jest już aktualna: ktoś ją w międzyczasie poprawił albo "
                "zatwierdził. Odśwież rozmowę i popraw najnowszą wersję."
            ),
        ) from exc

    # CZY W OGÓLE JEST DOKĄD TO WYDAĆ. Karta obiecywała „opublikuj" ZAWSZE, bo `delegation_allows`
    # stało tu jako `True`, a przycisk powstawał bez pytania o powierzenie. W projekcie bez
    # podpisanego zakresu człowiek dostawał więc przycisk, który po kliknięciu odmawia: trasa
    # zgody sprawdza delegację i odpowiada 409. Karta ma mówić to samo, co zrobi kliknięcie
    # (zgłoszenie wenext-ce ze zrzutów, 20.09). Pytanie jest to samo, co w `approve_revision`,
    # więc odpowiedź bierze się z tego samego miejsca.
    delegation = await PresenceStore(pool).delegation_for(
        organization_id=operator.organization_id, project_id=project_id
    )
    may_publish = delegation is not None and delegation.live
    caption = await _community_caption(
        pool,
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        payload=payload,
    )
    card = draft_card(
        revision=str(ready.id),
        # TYTUŁ, A NIE IDENTYFIKATOR CELU. Stało tu `target_external_id`, czyli `t3_e2edraft`, i
        # ten sam napis jechał w `tytul` oraz w `spolecznosc`: na ekranie stał dwa razy
        # identyfikator zamiast tytułu. Własny post ma prawdziwy tytuł w treści (0023), a
        # odpowiedź pod cudzym wątkiem nie ma tytułu i nie należy go zmyślać.
        title=_draft_title(payload, shown_text, fallback="Poprawiona odpowiedź"),
        # PODPIS TO NAZWA SPOŁECZNOŚCI, NIE CEL. Zrzut rundy 2 pokazał tu `t3_e2edraft`: tytuł
        # naprawiono dzień wcześniej, a jedno pole dalej została ta sama pomyłka.
        community=caption,
        profile=None,
        text=shown_text,
        image=str(shown_media.artifact_id) if shown_media is not None else None,
        state=DRAFT_NEW,
        delegation_allows=may_publish,
        # JEDNA LISTA AKCJI DLA JEDNEJ KARTY, wspólna z rejestrem narzędzi. Trasa budowała ją tu
        # ręcznie i wpisała do niej sam `opublikuj`, więc po poprawce w projekcie bez powierzenia
        # karta nie miała ŻADNEGO przycisku i człowiek nie mógł poprawić szkicu drugi raz, choć
        # poprawianie powierzenia nie wymaga (zmierzone przez wenext-ce na e2e, 21.09).
        actions=draft_actions(
            str(ready.id),
            allowed=may_publish,
            workspace_id=workspace_id,
            project_id=project_id,
            text=shown_text,
        ),
        no_actions_because=None if may_publish else NO_DELEGATION_NOTE,
    )
    # KARTA MUSI TRAFIĆ DO ŚLADU ROZMOWY, inaczej nie ma jej skąd wziąć (zgłoszenie wenext-ce z
    # dowodem z bazy, 20.09: po poprawce baza miała dwie rewizje, a ekran pokazywał starą z
    # adnotacją „istnieje nowsza"). Karty są PROJEKCJĄ śladu, więc zmiana, której w śladzie nie
    # ma, dla ekranu nie istnieje. Zdarzenie jest STATUSEM, a nie wynikiem narzędzia, bo zrobił
    # to CZŁOWIEK: udawanie, że poprawił agent, byłoby fałszem w jedynym miejscu, które pamięta.
    shown = await _note_in_conversation(request, operator, payload, card, shown_text)
    return {"karta": card, "poprzednia": str(revision_id), "w_rozmowie": shown}


async def _note_in_conversation(
    request: Request,
    operator: OperatorSession,
    payload: dict[str, Any],
    card: dict[str, Any],
    text: str,
) -> bool:
    """Dopisz poprawkę do rozmowy, z której pochodzi propozycja. Zwraca, czy się udało.

    Rozmowę wskazuje `source_fingerprint` rewizji (`session:<uuid>`), bo to ona jest jedynym
    wiązaniem propozycji z rozmową. Propozycja z innego źródła (pilot) rozmowy nie ma i nie ma
    wtedy czego dopisywać; brak wiązania nie może przewrócić zapisanej już poprawki.

    BYŁO TU `contextlib.suppress(Exception)` I TO BYŁ BŁĄD SAM W SOBIE. Miejsce, którego całym
    zadaniem jest „żeby człowiek to zobaczył", nie może po cichu nie zadziałać: poprawka
    zostawała w bazie, trasa odpowiadała normalnie, a rozmowa się o niczym nie dowiadywała.
    Teraz wynik jedzie do wołającego jako `w_rozmowie`, a niepowodzenie zostawia ślad w logu.
    Sam wyjątek dalej nie przewraca żądania, bo poprawka JEST zapisana i powiedzenie o niej
    „nie udało się" byłoby zdaniem nieprawdziwym o stanie bazy.
    """
    from control_plane.sessions.store import EVENT_STATUS, SessionStore

    fingerprint = str(payload.get("source_fingerprint") or "")
    if not fingerprint.startswith("session:"):
        return False
    try:
        session_id = UUID(fingerprint.split(":", 1)[1])
    except ValueError:
        return False
    store = SessionStore(await pool_for(request.app))
    try:
        await store.append_event(
            organization_id=operator.organization_id,
            session_id=session_id,
            kind=EVENT_STATUS,
            payload={
                "detail": "Człowiek poprawił treść szkicu.",
                "powod": "POPRAWKA_CZLOWIEKA",
                "znakow": len(text),
                "karty": [card],
            },
        )
    except Exception:
        _LOG.exception(
            "poprawka rewizji nie trafiła do rozmowy %s; szkic jest zapisany, ale go nie widać",
            session_id,
        )
        return False
    return True


def _draft_title(payload: dict[str, Any], text: str, *, fallback: str = "Odpowiedź") -> str:
    """Tytuł karty SZKIC.

    Własny post niesie swój tytuł W TREŚCI, oddzielony separatorem (0023), więc bierzemy go
    stamtąd. Odpowiedź pod cudzym wątkiem tytułu nie ma i nie wolno go zmyślać z pierwszego
    zdania: człowiek zobaczyłby na karcie napis, którego nigdzie nie napisał. `fallback` mówi
    wtedy, co się z tą odpowiedzią właśnie stało.

    O TYM, CZY TO WŁASNY POST, ROZSTRZYGA `ACTION_OF_CAPABILITY`, czyli ta sama mapa, której
    używa `approve_revision` linijkę wyżej. Pierwsza wersja tej funkcji porównywała `action_type`
    z `SemanticCapability.OWN_POST`, a taki element nie istnieje: zdolności nazywają się tu
    `SUBMIT_POST` i `PUBLISH_CONTENT`, więc każde wywołanie kończyło się `AttributeError`, czyli
    500 na KAŻDEJ zgodzie i KAŻDEJ poprawce. Zmierzone suitą 21.09 (9 porażek) po zgłoszeniu
    wenext-ce z e2e. Wniosek na przyszłość jest prostszy niż sama pomyłka: jeśli obok stoi
    sprawdzony sposób zadania tego samego pytania, zadaj je tak samo.
    """
    if ACTION_OF_CAPABILITY.get(str(payload.get("action_type"))) == ACTION_OWN_POST:
        from control_plane.pilot.publication import split_own_post

        title, _ = split_own_post(text)
        if title.strip():
            return title.strip()
    return fallback


def _hash_input_with_text(payload: dict[str, Any], text: str) -> ApprovalHashInput:
    """Ten sam odcisk zgody, z jedną zmienioną rzeczą: treścią.

    Pola przepisane z payloadu poprzedniej rewizji, bo zgoda ma dotyczyć TEJ SAMEJ akcji pod tym
    samym celem. Gdyby cokolwiek poza tekstem powstawało tu na nowo, poprawka byłaby inną akcją
    udającą tę samą.
    """
    from community_os_contracts.enums import SemanticCapability

    return ApprovalHashInput(
        organization=UUID(str(payload["organization"])),
        workspace=UUID(str(payload["workspace"])),
        assignment_version=UUID(str(payload["assignment_version"])),
        account=UUID(str(payload["account"])),
        platform=str(payload["platform"]),
        action_type=SemanticCapability(str(payload["action_type"])),
        target_external_id=str(payload["target_external_id"]),
        target_url=str(payload["target_url"]),
        source_fingerprint=str(payload["source_fingerprint"]),
        text_and_media=text,
        intelligence_profile_version=UUID(str(payload["intelligence_profile_version"])),
        plan_version=UUID(str(payload["plan_version"])),
        memory_snapshot=UUID(str(payload["memory_snapshot"])),
        adapter_version=str(payload["adapter_version"]),
    )
