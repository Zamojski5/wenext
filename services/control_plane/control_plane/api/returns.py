"""`GET …/projects/{id}/return` i korekta jednym zdaniem (P4 i P5, przepływ powrotu).

DWIE TRASY I JEDNA ZASADA. Odczyt mówi, co się wydarzyło, odkąd człowieka nie było, i notuje
wizytę; zapis przyjmuje jedno zdanie korekty i wysyła je do sesji. Obie liczą wszystko z tabel:
nie ma tu wywołania modelu ani jednej liczby, której nie dałoby się wskazać palcem w bazie.

ODCZYT NOTUJE WIZYTĘ, I TO JEST ZAMIERZONE. Wejście na ekran JEST wizytą; osobny przycisk
„oznacz jako przeczytane" byłby kolejną rzeczą do kliknięcia i kolejnym stanem do pomylenia.
Karencja z `returns/store.py` sprawia, że odświeżenie strony nie kasuje okna w trakcie czytania.

KOREKTA JEST ZWYKŁĄ WIADOMOŚCIĄ (E5, audyt UX 20.09 punkt 5). Zdanie idzie do sesji jako
wypowiedź człowieka i to jest CAŁE działanie tej trasy.

CO TU BYŁO DO 20.09 I DLACZEGO ZNIKŁO. Zdanie zapisywało się dodatkowo jako `PREFERENCE`, gdy
ZACZYNAŁO SIĘ od jednego z sześciu słów („nie", „za", „zamiast", „mniej", „więcej", „bez").
Reguła po pierwszym słowie nie rozumie „skróć to o połowę" ani „trzymaj się r/SaaS", za to
zapisuje jako wolę na przyszłość zdanie „nie zdążyłem przeczytać". Wymagała też od człowieka,
żeby znał listę słów sterujących pamięcią produktu, czego nikt nigdy nikomu nie powiedział.

CO JEST ZAMIAST. Sens zdania rozstrzyga agent, który je czyta, i to on zapisuje `PREFERENCE`
przez `memory.write`, WSKAZUJĄC tę wypowiedź człowieka (`payload.confirmed_at_seq`). Mechanizm
przypisania jest ten sam, co przy hipotezie z D-044, i nie jest słabszy od poprzedniego: tamten
przypisywał człowiekowi zdanie na podstawie jego pierwszego słowa, ten wymaga wskazania palcem
zdarzenia w śladzie, w którym człowiek to napisał.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane.audit import AuditWriter
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.returns import ReturnStore, ReturnView, Window
from control_plane.sessions import SessionStore
from control_plane.sessions.handlers import schedule_turn
from control_plane.workspace import WorkspaceStore

router = APIRouter(prefix="/api", tags=["powrót"])

RETURN_PATH = "/workspaces/{workspace_id}/projects/{project_id}/return"

#: Najdłuższa korekta. Jedno zdanie, nie esej: dłuższa wiadomość należy do rozmowy w projekcie,
#: gdzie widać ją w kontekście, a nie do pola pod podsumowaniem nieobecności.
MAX_CORRECTION_LENGTH = 400


class CorrectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_CORRECTION_LENGTH)


async def _project(
    request: Request, operator: OperatorSession, workspace_id: UUID, project_id: UUID
):
    """Przestrzeń tej organizacji i projekt w niej, albo 404. Brama obu tras."""
    store = WorkspaceStore(await pool_for(request.app))
    workspace = await store.workspace(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"nie ma workspace'u {workspace_id}")
    project = await store.get_project(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if project is None:
        raise HTTPException(status_code=404, detail=f"nie ma projektu {project_id}")
    return project


def window_payload(window: Window) -> dict[str, Any]:
    return {
        "od": window.since.isoformat() if window.since else None,
        "do": window.until.isoformat(),
        "pierwsza_wizyta": window.first_visit,
        "zdanie": window.sentence(),
    }


def view_payload(view: ReturnView) -> dict[str, Any]:
    return {
        "okno": window_payload(view.window),
        "obudzenia": view.wakeups,
        "sesje": list(view.sessions),
        "decyzje": list(view.decisions),
        "publikacje": list(view.publications),
        "korekty": list(view.corrections),
        "incydenty": list(view.incidents),
        "uwagi": list(view.notes),
        "cisza": view.quiet(),
        "obserwujacy": list(view.audience),
    }


@router.get(RETURN_PATH)
async def read_return(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    mark: bool = True,
) -> dict[str, Any]:
    """Co się wydarzyło, odkąd tej osoby tu nie było.

    `mark=false` czyta BEZ notowania wizyty. Jest po to, żeby raport walidacji mógł policzyć te
    same liczby, nie przesuwając okna człowiekowi, który jeszcze nie zajrzał.
    """
    await _project(request, operator, workspace_id, project_id)
    pool = await pool_for(request.app)
    returns = ReturnStore(pool)
    if mark:
        window = await returns.visit(
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=operator.user_id,
        )
    else:
        from datetime import UTC, datetime

        window = await returns.window_of(
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=operator.user_id,
            now=datetime.now(UTC),
        )
    view = await returns.view(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
        window=window,
    )
    return view_payload(view)


@router.post(RETURN_PATH + "/correction", status_code=202)
async def write_correction(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    body: CorrectionBody,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Jedno zdanie po powrocie: do bieżącej sesji, a gdy żadna nie żyje, do nowej.

    TA TRASA NICZEGO NIE ZAPAMIĘTUJE. Wysyła wypowiedź człowieka do rozmowy i budzi turę.
    Zapisanie jej jako woli na przyszłość należy do agenta, który ją przeczyta i wskaże ją
    palcem w śladzie (`memory.write` z `payload.confirmed_at_seq`). Gdyby zapisywała tu,
    robiłaby to bez rozumienia zdania, czyli po pierwszym słowie, i to jest dokładnie ten
    mechanizm, który E5 usuwa.
    """
    await _project(request, operator, workspace_id, project_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="korekta nie może być pustym zdaniem")

    pool = await pool_for(request.app)
    sessions = SessionStore(pool)
    existing = await sessions.for_project(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
        limit=1,
    )
    live = existing[0] if existing and not existing[0].is_terminal else None

    if live is not None:
        session = live
        event = await sessions.append_event(
            organization_id=operator.organization_id,
            session_id=session.id,
            kind="USER_MESSAGE",
            payload={"text": text, "context": "korekta po powrocie"},
        )
        seq = event.seq
        started = False
    else:
        session = await sessions.create(
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            goal=text,
            budget={},
            # Połączenie bierzemy z poprzedniej sesji projektu: człowiek wybrał je raz i korekta
            # nie jest miejscem na wybieranie silnika. Gdy poprzedniej nie ma, zostaje NULL,
            # a dyspozytor powie wprost, że nie ma na czym uruchomić sesji.
            codex_connection_id=existing[0].codex_connection_id if existing else None,
        )
        seq = 1
        started = True

    queue = PostgresJobTransport(JobStore(pool), organization_id=operator.organization_id)
    await schedule_turn(
        queue,
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        session_id=session.id,
        reason="korekta-po-powrocie",
    )

    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind="PROJECT_RETURN_CORRECTION",
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            subject_ref=f"project:{project_id}",
            actor_ref=f"operator:{operator.operator_id}",
            payload={"project_id": str(project_id), "session_id": str(session.id)},
        )

    return {
        "session_id": str(session.id),
        "nowa_sesja": started,
        "seq": seq,
        # `zapamietane` zostaje w odpowiedzi jako `None` i to nie jest zapomniane pole: ekran
        # czyta ten kształt, a zdjęcie klucza zamieniłoby „nic nie zapisano" w „nie wiadomo".
        # Co zostanie zapamiętane, widać po turze, w ustaleniach projektu.
        "zapamietane": None,
        "uwaga": (
            "Zdanie poszło do rozmowy jako Twoja wypowiedź. Agent ją przeczyta i sam "
            "rozstrzygnie, czy to jest wskazówka na teraz, czy Twoja wola na przyszłość; "
            "zapisane ustalenia widać w projekcie i możesz je wycofać."
        ),
    }
