"""`/api/capsule*` — the "agent's browser" card: mode of the capsule, open a human's screen, done.

Plan B level 2 (`docs/OUTCOMES.md` §8, D-026), contract agreed with the dashboard (wenext-68):

    GET  /api/capsule                  status pill + capsule mode, no platform request
    POST /api/capsule/session          INTERACTIVE with a human reason + CONTROL ticket → href
    POST /api/capsule/session/confirm  measure, back to display-only, measure, resume parked jobs

Behind the operator boundary like everything in `api/`. Both writes are audited in their own
transaction with a closed `kind` (`CAPSULE_SESSION_OPENED` / `_CONFIRMED`) — the audit of a browser
that holds the founder's account must say who opened its screen and when.

Infrastructure that is down answers 503 with the reason (`CapsuleUnavailable`); an unconfigured
adapter (no `WENEXT_BROWSER_READER_URL`) answers `configured: false` and refuses the two POSTs
with 409 — a button that cannot work must not pretend to.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

import httpx
from community_observer import RedditBrowserSource
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane.api.waiting_jobs import resume_human_unblocked
from control_plane.audit import AuditWriter
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.observe import capsule_session
from control_plane.observe.capsule_client import (
    CapsuleClient,
    CapsuleConfig,
    CapsuleUnavailable,
    GatewayClient,
    capsule_subject,
)
from control_plane.workspace.account_state import note_capsule_login

router = APIRouter(prefix="/api", tags=["kapsuła"])

CAPSULE_PATH = "/api/capsule"
SESSION_PATH = "/api/capsule/session"
CONFIRM_PATH = "/api/capsule/session/confirm"

#: Process-local card memory (last session reading). One operator, one process (D-016).
_memory = capsule_session.CardMemory()
#: The X capsule's own memory (D-051): its screen opening is not a fact about Reddit's capsule.
_x_memory = capsule_session.CardMemory()


def _config(platform: str | None = None) -> CapsuleConfig | None:
    return CapsuleConfig.for_platform(os.environ, platform)


def _clients(config: CapsuleConfig) -> tuple[CapsuleClient, GatewayClient]:
    try:
        return (
            CapsuleClient(config.reader_url, token=config.reader_token),
            GatewayClient(config.gateway_url, issuer_token=config.gateway_issuer_token),
        )
    except CapsuleUnavailable as exc:
        # A base URL outside the compose service list (D-027) — configuration, not weather, but
        # the card must say so rather than crash with a 500.
        raise HTTPException(status_code=503, detail=str(exc)) from exc


#: Per session read from the card. The observer's own reader waits up to 90 s (its jobs have a
#: heartbeat); the card answers a human who clicked a button and the dashboard gives up at 90 s, so
#: `confirm` = one switch (20 s cap, `capsule_client.TIMEOUT`) + two reads at this cap ≈ 70 s worst.
PROBE_TIMEOUT = httpx.Timeout(25.0, connect=5.0)


def _probe() -> RedditBrowserSource | None:
    """The Reddit adapter through the capsule — the observer's object, with the card's timeout.

    Konstrukcja mieszka w `observe/capsule_session.py`, bo tej samej sondy używa teraz narzędzie
    `accounts.status`. Dwie kopie rozjechałyby się na pierwszej zmianie nazwy zmiennej, a rozjazd
    widać dopiero wtedy, gdy jedna strona mówi „nie skonfigurowano" przy działającej kapsule.
    """
    try:
        return capsule_session.probe_from_env(os.environ, timeout=PROBE_TIMEOUT)
    except ValueError as exc:
        # The reader URL names a host outside the compose service list (D-027).
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/capsule")
async def read_capsule(
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    config = _config()
    if config is None:
        return capsule_session.card_json(
            configured=False,
            state=None,
            session=None,
            error=(
                "WENEXT_BROWSER_READER_URL nie jest ustawiony — kapsuła nie czyta Reddita "
                "(opt-in, D-025)"
            ),
        )
    capsule, gateway = _clients(config)
    try:
        return await capsule_session.card_state(capsule, _memory, probe=_probe())
    finally:
        await capsule.aclose()
        await gateway.aclose()


class OpenSessionBody(BaseModel):
    """Optional: which platform the human is about to log into (26.09). Without it the screen
    opens as before, on whatever page the browser shows."""

    model_config = ConfigDict(extra="forbid")

    platform: str | None = Field(default=None, pattern="^(REDDIT|X)$")


@router.post("/capsule/session")
async def open_capsule_session(
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    body: OpenSessionBody | None = None,
) -> dict[str, Any]:
    platform = body.platform if body is not None else None
    config = _config(platform)
    if config is None:
        raise HTTPException(
            status_code=409, detail="kapsuła nie jest skonfigurowana (D-025 opt-in)"
        )
    # D-051: X signs in on the X capsule's screen when it has one; the Reddit card keeps its own.
    own_x_capsule = platform == "X" and config != _config()
    capsule, gateway = _clients(config)
    try:
        try:
            result = await capsule_session.open_session(
                capsule,
                gateway,
                _x_memory if own_x_capsule else _memory,
                operator_id=operator.operator_id,
                public_origin=config.stream_public_origin,
                platform=platform,
            )
        except CapsuleUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        await capsule.aclose()
        await gateway.aclose()
    pool = await pool_for(request.app)
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind="CAPSULE_SESSION_OPENED",
            organization_id=operator.organization_id,
            subject_ref=capsule_subject(os.environ, platform),
            actor_ref=f"operator:{operator.operator_id}",
            payload={"reason_code": capsule_session.HUMAN_SESSION_REASON},
        )
    return result


@router.post("/capsule/session/confirm")
async def confirm_capsule_session(
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    config = _config()
    if config is None:
        raise HTTPException(
            status_code=409, detail="kapsuła nie jest skonfigurowana (D-025 opt-in)"
        )
    pool = await pool_for(request.app)
    capsule, gateway = _clients(config)
    probe = _probe()

    async def resume() -> int:
        return int((await resume_human_unblocked(pool, operator))["resumed"])

    try:
        try:
            result = await capsule_session.confirm_session(
                capsule, gateway, _memory, probe=probe, resume_jobs=resume
            )
        except CapsuleUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        await capsule.aclose()
        await gateway.aclose()
        if probe is not None:
            await probe.aclose()
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind="CAPSULE_SESSION_CONFIRMED",
            organization_id=operator.organization_id,
            subject_ref=capsule_subject(os.environ, None),
            actor_ref=f"operator:{operator.operator_id}",
            payload={
                "count": int(result["resumed_jobs"]),
                "verdict": "SIGNED_IN"
                if result["status"] == capsule_session.SIGNED_IN
                else "SIGNED_OUT",
            },
        )
    # STAN KONTA IDZIE ZA RZECZYWISTOŚCIĄ (D-046). Potwierdzenie sesji jest jedynym momentem, w
    # którym wiemy z odczytu, na kogo zalogowana jest przeglądarka; do 20.09 ta wiedza nigdzie
    # nie trafiała i wiersz konta stał na PROVISIONED przy działającej kapsule.
    noted = await note_capsule_login(
        pool,
        organization_id=operator.organization_id,
        platform="REDDIT",
        logged_in=bool(result["display_only"]["logged_in"]),
        username=result["display_only"]["username"],
        actor_ref=f"operator:{operator.operator_id}",
    )
    result["konto_gotowe"] = noted.changed
    result["dlaczego_bez_zmiany"] = noted.reason
    # THE CARD'S THREE KEYS (26.09), the same ones `/api/x/session/confirm` answers with, so the
    # chat card of either platform reads one shape: connected or not, as whom, and one sentence.
    signed_in = result["status"] == capsule_session.SIGNED_IN
    result["polaczone"] = signed_in
    result["konto"] = result.get("account")
    result["detail"] = (
        f"Połączono konto Reddit {result.get('account')}."
        if signed_in
        else "Nie widzę jeszcze zalogowanej sesji Reddita. Dokończ logowanie w oknie "
        "przeglądarki agenta i kliknij ponownie „Gotowe, sprawdź połączenie”."
    )
    return result
