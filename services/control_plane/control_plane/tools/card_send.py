"""Sending a draft from the card in the person's own agent: ONLY THEIR CLICK CAN DO IT (11.10).

The founder asked for "Wyślij" on the card in Codex, and agreed to one condition: the button is
the human's, and the model can never press it. That is the whole design, and it is layered,
because no single layer is a proof.

THREE TOOLS, ALL APP-ONLY (`_meta.ui.visibility: ["app"]`, MCP Apps SEP-1865):

  * `room.send_token`   the card asks for a one-time confirmation when the person presses "Wyślij";
  * `room.send_draft`   the card sends, with that confirmation, after the person pressed "Tak";
  * `room.send_status`  the card asks what became of the queue entry (read only).

THREAT MODEL, IN FIVE LINES (what each layer buys and what it does not):

  1. The server cannot tell a view's `tools/call` from a model's: both arrive as the same stdio
     frame. So "app-only" is the HOST's promise (the spec says it MUST keep such tools out of the
     agent's list and reject calls it did not originate; Codex implements it, PR 24700), and this
     server adds what it can: they are listed ONLY for a Codex host, never for Claude Code (not an
     Apps host: it would show them to the model) and never for a Wenext-run session, `catalog.list`
     and `catalog.describe` never name them, and a call from any other host is "no such tool".
  2. A call without a valid token is refused. The token is minted by `room.send_token`, bound to
     (revision, payload digest, account, what the click will do about the scope), lives three
     minutes, works once, and is burned by the first presentation, right or wrong.
  3. The token travels only in the result's `_meta` (the spec: "not intended for model context"),
     never in `content` or `structuredContent`, and never in the session trace (`mcp_server`
     redacts it from the call row; the result row never carries `_meta`).
  4. SIGNING a scope is reachable only behind that token (`presence/card_consent.py`): the model
     has no tool that signs, and `delegation.propose` still only prepares.
  5. RESIDUAL, stated plainly: an agent that ignores the host's filter, learns both names and
     calls the two tools in order can send. It is the stance of the plugin profile (`PLUGIN_TOOLS`):
     guardrails for a cooperative agent, not a boundary against one with a shell and the database.
     What it cannot do is send by accident, by a prompt injection that names one tool, or from a
     session the host does not run.

WHICH ACCOUNTS A SIGNING CLICK COVERS (11.10): every account of the project the platform matrix lets
write, so one consent serves the project. They are read from the database by the server
(`card_consent.assigned_accounts`) and NEVER from the card: the tools take a revision id, a digest
and a token and nothing else (`additionalProperties: false`), the plan names the accounts to the
person, and the token's basis binds that list, so an account assigned after the sentence was shown
turns the send into `ZAKRES_ZMIENIONY` instead of widening what the person agreed to.

REFUSALS THAT HEAL (11.10): the card answers `ZAKRES_KOLEJKA` by asking `room.send_token` again
until the queue is clear, and `ZAKRES_ZMIENIONY` / `POTWIERDZENIE` by asking once more and showing
the new sentence. Asking mints nothing that sends: every path ends at a confirmation that waits for
a click, and `room.send_draft` is reachable from the card's "Tak" button alone (`apps_ui`, tested
in node).

SAME CHECKS AS THE DASHBOARD, BECAUSE IT IS THE SAME CODE: `presence/human_approval.py` is the
body of the `approve` route, moved so both callers share it (state, window, digest as shown,
signed scope, pace, one transaction for the consent and the queue entry, the publisher nudge).
"Wysłano" is never the answer: the result is the request in the publisher's queue, and the card
says "Opublikowano" only when `room.send_status` reports PUBLISHED.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from control_plane.opportunities.store import OpportunityStore, presence_path
from control_plane.pilot.publication import ConsentError
from control_plane.presence.approval import ACTION_OF_CAPABILITY, ACTION_OWN_POST
from control_plane.presence.card_consent import (
    CONSENT_WIDEN,
    REFUSED_QUEUE,
    CardConsentRefused,
    ConsentAccount,
    ConsentPlan,
    assigned_accounts,
    plan_consent,
    requests_waiting_on,
    sign_for_card,
)
from control_plane.presence.human_approval import (
    HumanApprovalRefused,
    approve_and_queue,
    ensure_waiting,
    payload_of,
    revision_row,
)
from control_plane.presence.store import PresenceRefusal, PresenceStore
from control_plane.workspace.platforms import support_for

#: How long a confirmation lives. The person presses "Wyślij", reads one sentence and presses "Tak,
#: wyślij": seconds. Three minutes is room for reading, and short enough that a token that leaked
#: into a log is already dead.
TOKEN_TTL = timedelta(minutes=3)
#: Outstanding confirmations kept at once. One person has a handful of drafts; a flood of mints is
#: not a person, and the oldest are dropped first.
MAX_OUTSTANDING = 16

#: The `_meta` key that carries the confirmation back to the card, and nowhere else.
META_TOKEN = "wenext/potwierdzenie"

#: Tool arguments that must never be written to the trace: tool name to argument names.
SECRET_ARGS: dict[str, tuple[str, ...]] = {"room.send_draft": ("token",)}

_SHA256 = re.compile(r"[0-9a-f]{64}")

CODE_NOT_FOUND = "NIE_ZNALEZIONO"
CODE_CONFIRMATION = "POTWIERDZENIE"
CODE_SCOPE_MOVED = "ZAKRES_ZMIENIONY"
CODE_SIGNING = "PODPIS"
CODE_ACTION = "DZIALANIE"


class TokenRefused(Exception):
    """The confirmation is not good. `code` is for the card, `detail` for the person."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Grant:
    """What one confirmation was minted for. Nothing in it is chosen by the caller of `send`."""

    revision_id: UUID
    payload_hash: str
    account_id: UUID
    #: `ConsentPlan.basis()`: what the click was told it would do about the scope.
    basis: str
    expires_at: datetime


class SendTokens:
    """One-time confirmations, in memory, for the life of the tool server.

    In memory on purpose. The server is one process per host session and the card talks to that
    same process, so a restart between "Wyślij" and "Tak, wyślij" is a refusal that costs a second
    click, and nothing about a confirmation is ever written to a table. Only the SHA-256 of a token
    is kept, so a memory dump of the dictionary is not a list of working tokens either.
    """

    def __init__(
        self,
        *,
        ttl: timedelta = TOKEN_TTL,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._grants: dict[str, Grant] = {}

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _prune(self) -> None:
        now = self._clock()
        for key in [k for k, grant in self._grants.items() if grant.expires_at <= now]:
            del self._grants[key]

    def mint(
        self, *, revision_id: UUID, payload_hash: str, account_id: UUID, basis: str
    ) -> tuple[str, datetime]:
        """A fresh token for ONE revision; an earlier one for the same revision stops working."""
        self._prune()
        for key in [k for k, g in self._grants.items() if g.revision_id == revision_id]:
            del self._grants[key]
        while len(self._grants) >= MAX_OUTSTANDING:
            del self._grants[next(iter(self._grants))]
        token = secrets.token_urlsafe(32)
        expires_at = self._clock() + self._ttl
        self._grants[self._key(token)] = Grant(
            revision_id=revision_id,
            payload_hash=payload_hash,
            account_id=account_id,
            basis=basis,
            expires_at=expires_at,
        )
        return token, expires_at

    def redeem(self, token: str, *, revision_id: UUID) -> Grant:
        """The grant behind `token`, consumed. Any presentation of a known token burns it."""
        grant = self._grants.pop(self._key(token), None)
        if grant is None:
            raise TokenRefused(
                CODE_CONFIRMATION,
                "Brak ważnego potwierdzenia kliknięciem: nieznane albo już zużyte. Wysyłkę "
                "zatwierdza człowiek w karcie, agent tego nie robi.",
            )
        if grant.expires_at <= self._clock():
            raise TokenRefused(CODE_CONFIRMATION, "Potwierdzenie wygasło. Spróbuj ponownie.")
        if grant.revision_id != revision_id:
            raise TokenRefused(
                CODE_CONFIRMATION,
                "To potwierdzenie dotyczy innego szkicu. Spróbuj ponownie.",
            )
        return grant


def redacted_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """`args` as they may be written to the trace: the secret arguments of `name` replaced."""
    secret = SECRET_ARGS.get(name)
    if not secret:
        return args
    return {k: ("[pominięte w śladzie]" if k in secret else v) for k, v in args.items()}


CARD_SEND_TOOL_SPECS = (
    {
        "name": "room.send_token",
        "summary": "TYLKO DLA KARTY: potwierdzenie wysyłki, wydawane po kliknięciu człowieka.",
        "detail": (
            "Narzędzie dla karty Pokoju w agencie, niewidoczne dla modelu (visibility: app). "
            "Sprawdza szkic tak, jak robi to zatwierdzenie w dashboardzie (czeka na zgodę, okno "
            "nie minęło, odcisk jest tym, który człowiek widział, konto jest przypisane) i "
            "mówi, co kliknięcie zrobi z zakresem: wyśle w podpisanym zakresie, podpisze "
            "minimalny zakres dla wszystkich kont projektu, którym platforma pozwala pisać, albo "
            "rozszerzy istniejący o te konta (nazwane w odpowiedzi, pole `konta`; serwer czyta je "
            "z przypisań projektu, karta ich nie podaje). Potwierdzenie jedzie tylko "
            "w `_meta` wyniku, ważne trzy minuty, jednorazowe. Niczego nie wysyła i niczego "
            "nie podpisuje; nie ma `writes`, żeby powtórne wywołanie wydało nowe potwierdzenie, "
            "a nie wynik poprzedniego."
        ),
        "state": "DOSTEPNE",
        "input_schema": {
            "type": "object",
            "properties": {
                "revision_id": {"type": "string"},
                "odcisk": {
                    "type": "string",
                    "description": "Odcisk szkicu z karty (to, co człowiek zobaczył).",
                },
            },
            "required": ["revision_id", "odcisk"],
            "additionalProperties": False,
        },
        "tags": ("karta",),
    },
    {
        "name": "room.send_draft",
        "summary": "TYLKO DLA KARTY: wyślij szkic do publikatora po kliknięciu człowieka.",
        "detail": (
            "Narzędzie dla karty Pokoju w agencie, niewidoczne dla modelu (visibility: app). "
            "Wymaga potwierdzenia z room.send_token, jednorazowego i związanego z tym szkicem, "
            "jego odciskiem, kontem i tym, co kliknięcie zrobi z zakresem. Bez niego odmawia. "
            "Jeśli plan mówi o podpisie, podpisuje ten zakres tą samą drogą, co formularz "
            "Obecności, i zatwierdza szkic tą samą drogą, co przycisk w dashboardzie. WYNIK TO "
            "OCZEKUJE: wniosek stoi w kolejce publikatora, a „opublikowano” wolno powiedzieć "
            "dopiero po odczycie zwrotnym (room.send_status)."
        ),
        "state": "DOSTEPNE",
        "writes": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "revision_id": {"type": "string"},
                "token": {"type": "string", "description": "Potwierdzenie z room.send_token."},
            },
            "required": ["revision_id", "token"],
            "additionalProperties": False,
        },
        "tags": ("karta", "działanie"),
    },
    {
        "name": "room.send_status",
        "summary": "TYLKO DLA KARTY: co się stało z wnioskiem wysłanym z karty.",
        "detail": (
            "Narzędzie dla karty Pokoju w agencie, niewidoczne dla modelu (visibility: app). "
            "Czyta wniosek tego projektu z kolejki publikatora: status, adres opublikowanej "
            "odpowiedzi (dopiero po odczycie zwrotnym) i powód, gdy nie wyszło."
        ),
        "state": "DOSTEPNE",
        "input_schema": {
            "type": "object",
            "properties": {"request_id": {"type": "string"}},
            "required": ["request_id"],
            "additionalProperties": False,
        },
        "tags": ("karta",),
    },
)


class CardSendTools:
    """Mixin of `ToolRegistry`: the three app-only tools behind the card's "Wyślij"."""

    # `_send_tokens` is a field of the registry (a dataclass); see `registry.ToolRegistry`.

    def _presence_address(self) -> str | None:
        """Where the person resumes or widens a scope, when the launcher knows the dashboard."""
        import os

        capability = self.capability  # type: ignore[attr-defined]
        base = (os.environ.get("WENEXT_DASHBOARD_URL") or "").strip().rstrip("/")
        path = presence_path(capability.workspace_id, capability.project_id)
        return f"{base}{path}" if base else None

    async def _card_draft(self, revision_id: UUID) -> Any:
        capability = self.capability  # type: ignore[attr-defined]
        return await revision_row(
            self.pool,  # type: ignore[attr-defined]
            organization_id=capability.organization_id,
            workspace_id=capability.workspace_id,
            project_id=capability.project_id,
            revision_id=revision_id,
        )

    async def _card_community(self, row: Any, payload: dict[str, Any], action: str) -> str | None:
        """The community the draft is for, when its platform scopes by community, else `None`."""
        support = support_for(str(row["platform"]))
        if support is not None and not support.scoped_by_community:
            return None
        target = str(payload.get("target_external_id") or "").strip()
        if not target:
            return None
        if action == ACTION_OWN_POST:
            return target  # an own post aims at the community itself (`r/name`)
        capability = self.capability  # type: ignore[attr-defined]
        opportunity = await OpportunityStore(self.pool).for_target(  # type: ignore[attr-defined]
            organization_id=capability.organization_id,
            project_id=capability.project_id,
            external_id=target,
        )
        if opportunity is not None and opportunity.community:
            return str(opportunity.community)
        async with self.pool.acquire() as conn:  # type: ignore[attr-defined]
            found = await conn.fetchval(
                "SELECT c.community_key FROM observed_threads t JOIN communities c "
                "ON c.id = t.community_id AND c.organization_id = t.organization_id "
                "WHERE t.organization_id = $1 AND t.workspace_id = $2 "
                "AND (t.external_id = $3 OR ('t3_' || t.external_id) = $3) LIMIT 1",
                capability.organization_id,
                capability.workspace_id,
                target,
            )
        return str(found) if found else None

    async def _card_plan(self, row: Any) -> ConsentPlan:
        """What a click on this draft will do about the scope. Raises `CardConsentRefused`."""
        from .registry import ToolError

        capability = self.capability  # type: ignore[attr-defined]
        payload = payload_of(row)
        action = ACTION_OF_CAPABILITY.get(str(payload.get("action_type")))
        if action is None:
            raise ToolError(
                f"działanie {payload.get('action_type')} nie ma odpowiednika w zakresie "
                "powierzenia, więc z karty go nie wyślę"
            )
        delegation = await PresenceStore(self.pool).delegation_for(  # type: ignore[attr-defined]
            organization_id=capability.organization_id, project_id=capability.project_id
        )
        plan = plan_consent(
            delegation,
            # The draft's own account and every account the project has, both read from the
            # database rows above: nothing here comes from the card.
            account=ConsentAccount(
                account_id=row["social_account_id"],
                platform=str(row["platform"]),
                handle=str(row["account_handle"]),
            ),
            assigned=await assigned_accounts(
                self.pool,  # type: ignore[attr-defined]
                organization_id=capability.organization_id,
                workspace_id=capability.workspace_id,
                project_id=capability.project_id,
            ),
            community_key=await self._card_community(row, payload, action),
            action=action,
        )
        if (
            plan.kind == CONSENT_WIDEN
            and plan.replaces is not None
            and await requests_waiting_on(
                self.pool,  # type: ignore[attr-defined]
                organization_id=capability.organization_id,
                delegation_id=plan.replaces,
            )
        ):
            raise CardConsentRefused(
                REFUSED_QUEUE,
                "Poprzednia odpowiedź jeszcze czeka w kolejce publikatora, a nowy podpis "
                "zatrzymałby ją. Poczekaj, aż wyjdzie, i spróbuj ponownie.",
            )
        return plan

    def _refusal(self, reason: str, code: str, **extra: Any) -> dict[str, Any]:
        from .registry import unavailable

        return unavailable(reason, kod=code, **extra)

    async def _room_send_token(self, args: dict[str, Any]) -> dict[str, Any]:
        from .registry import ToolError, done

        revision_id = self._uuid(args.get("revision_id"), "revision_id")  # type: ignore[attr-defined]
        shown = str(args.get("odcisk") or "").strip().lower()
        if not _SHA256.fullmatch(shown):
            raise ToolError("odcisk szkicu to 64 znaki szesnastkowe, takie jak na karcie")
        row = await self._card_draft(revision_id)
        if row is None:
            return self._refusal("Nie ma takiej propozycji w tym projekcie.", CODE_NOT_FOUND)
        try:
            ensure_waiting(row, shown)
            plan = await self._card_plan(row)
        except HumanApprovalRefused as exc:
            return self._refusal(exc.detail, exc.code)
        except CardConsentRefused as exc:
            return self._refusal(exc.detail, exc.code, obecnosc_adres=self._presence_address())
        token, expires_at = self._send_tokens.mint(  # type: ignore[attr-defined]
            revision_id=revision_id,
            payload_hash=str(row["payload_hash"]),
            account_id=row["social_account_id"],
            basis=plan.basis(),
        )
        return {
            **done(
                szkic=str(revision_id),
                konto=row["account_handle"],
                platforma=str(row["platform"]),
                zakres=plan.to_json(),
                wygasa_potwierdzenie=expires_at.isoformat(),
            ),
            # ONLY the card reads this, and nothing that is written to a trace or shown as text.
            "_mcp_meta": {META_TOKEN: token},
        }

    async def _room_send_draft(self, args: dict[str, Any]) -> dict[str, Any]:
        from .registry import pending

        capability = self.capability  # type: ignore[attr-defined]
        revision_id = self._uuid(args.get("revision_id"), "revision_id")  # type: ignore[attr-defined]
        token = str(args.get("token") or "")
        if not token:
            return self._refusal(
                "Brak potwierdzenia kliknięciem. Wysyłkę zatwierdza człowiek w karcie, agent tego "
                "nie robi.",
                CODE_CONFIRMATION,
            )
        # THE TOKEN IS CONSUMED FIRST, before anything is looked at or written: whatever else
        # happens, this confirmation is spent, and a second send needs a second click.
        try:
            grant = self._send_tokens.redeem(token, revision_id=revision_id)  # type: ignore[attr-defined]
        except TokenRefused as exc:
            return self._refusal(exc.detail, exc.code)

        row = await self._card_draft(revision_id)
        if row is None:
            return self._refusal("Nie ma takiej propozycji w tym projekcie.", CODE_NOT_FOUND)
        try:
            ensure_waiting(row, grant.payload_hash)
            if row["social_account_id"] != grant.account_id:
                raise HumanApprovalRefused(
                    409,
                    "konto tego szkicu zmieniło się od chwili, gdy go pokazano",
                    code="ODCISK",
                )
            plan = await self._card_plan(row)
        except HumanApprovalRefused as exc:
            return self._refusal(exc.detail, exc.code)
        except CardConsentRefused as exc:
            return self._refusal(exc.detail, exc.code, obecnosc_adres=self._presence_address())
        if plan.basis() != grant.basis:
            # The person agreed to the sentence they were shown. A scope that changed since
            # (revoked, replaced, paused) is a different sentence.
            return self._refusal(
                "Zakres zmienił się od chwili, gdy pokazano potwierdzenie. Spróbuj ponownie.",
                CODE_SCOPE_MOVED,
            )

        signed = False
        if plan.signs:
            try:
                await sign_for_card(
                    self.pool,  # type: ignore[attr-defined]
                    plan,
                    organization_id=capability.organization_id,
                    workspace_id=capability.workspace_id,
                    project_id=capability.project_id,
                    host=str(capability.host),
                )
                signed = True
            except PresenceRefusal as exc:
                return self._refusal(str(exc), CODE_SIGNING)
            except ConsentError as exc:  # not exactly one operator to file the signature under
                return self._refusal(
                    f"{exc}. Podpisz zakres w Obecności.",
                    CODE_SIGNING,
                    obecnosc_adres=self._presence_address(),
                )
        try:
            issued = await approve_and_queue(
                self.pool,  # type: ignore[attr-defined]
                organization_id=capability.organization_id,
                workspace_id=capability.workspace_id,
                project_id=capability.project_id,
                row=row,
                signer=None,
                reason=f"zgoda człowieka kliknięciem w karcie agenta ({capability.host})",
                expected_payload_hash=grant.payload_hash,
            )
        except HumanApprovalRefused as exc:
            return self._refusal(
                exc.detail + (" Zakres został podpisany, ale nic nie wyszło." if signed else ""),
                exc.code,
                podpisano=signed,
            )
        return pending(
            "wniosek stoi w kolejce publikatora; „opublikowano” dopiero po odczycie zwrotnym",
            wniosek=str(issued.request_id),
            szkic=str(revision_id),
            podpisano=signed,
            zakres=plan.to_json(),
        )

    async def _room_send_status(self, args: dict[str, Any]) -> dict[str, Any]:
        from .registry import done

        request_id = self._uuid(args.get("request_id"), "request_id")  # type: ignore[attr-defined]
        capability = self.capability  # type: ignore[attr-defined]
        async with self.pool.acquire() as conn:  # type: ignore[attr-defined]
            row = await conn.fetchrow(
                """
                SELECT q.id, q.status, q.detail, q.published_at, q.public_visibility,
                       e.permalink, q.target_url
                  FROM publication_requests q
                  LEFT JOIN execution_evidence e
                    ON e.attempt_id = q.attempt_id AND e.organization_id = q.organization_id
                 WHERE q.organization_id = $1 AND q.workspace_id = $2 AND q.project_id = $3
                   AND q.id = $4
                """,
                capability.organization_id,
                capability.workspace_id,
                capability.project_id,
                request_id,
            )
        if row is None:
            return self._refusal("Nie ma takiego wniosku w tym projekcie.", CODE_NOT_FOUND)
        return done(
            wniosek={
                "id": str(row["id"]),
                "status": row["status"],
                "widocznosc": row["public_visibility"],
                "opublikowano": row["published_at"].isoformat() if row["published_at"] else None,
                # Only a PUBLISHED request has evidence; before that there is no live address.
                "adres": row["permalink"] if row["status"] == "PUBLISHED" else None,
                "powod": row["detail"],
            }
        )
