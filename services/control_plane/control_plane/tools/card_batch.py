"""Sending a whole reviewed batch with ONE click: "Send all" in the card (v11, 13.10).

THE FOUNDER (13.10): "I do not want to click twenty times a day." The answer is not to remove the
human: X removed 42 thousand accounts for AI replies with no human in the loop, and the public
promise is "Nothing posts without your click". It is ONE click per REVIEWED batch. The card lists
every draft that could go (platform, who it answers, the first line, a tick for each, all ticked),
says the schedule the server will use and the limits, and "Yes, send these N" sends exactly what is
ticked. `tools/card_send.py` holds the threat model of a single send and every line of it still
holds here; this module is the batch, built the way that one is.

TWO TOOLS, BOTH APP-ONLY (`_meta.ui.visibility: ["app"]`, listed only for a Codex host, never for
Claude Code, never for a Wenext-run or hosted session, never in `catalog.list` or
`catalog.describe`):

  * `room.batch_token`   the question: validates EVERY draft exactly as `room.send_token` does
                         and answers with the plan (the order and the moment of each reply) in
                         `structuredContent` and a one-time token in `_meta`;
  * `room.send_batch`    the act, behind that token alone: approves and queues each draft through
                         `approve_and_queue`, each WITH ITS MOMENT (`not_before`), so the batch is
                         spaced out inside the scope's own limits (`presence/batch_plan.py`).

The card follows what became of each request with `room.send_status`, the existing read.

THREAT MODEL, AS AN ADDITION TO `card_send.py` (1 to 5), `card_actions.py` (6 to 10) and
`card_connect.py` (11 to 17); all of those still hold:

 18. A BATCH IS NOT A SIGNATURE. A single send may sign or widen the scope in the same click (the
     consent sentence says so); a batch never does. `room.batch_token` accepts only drafts whose
     plan is `WYSLIJ`: a live signed scope that already lists the draft's account. A draft outside
     the scope is refused with `POZA_ZAKRESEM` and the card's own Limits panel is the way to
     widen it. The reason is arithmetic of trust: the person agreed to a list of replies, not to
     a list of replies AND a new scope, and signing in the middle of a loop would change the
     delegation every later draft was checked against.
 19. THE TOKEN BINDS THE EXACT SET. It is minted for one ordered list of `(revision, digest,
     account, moment)` and the delegation the plan was drawn against (`basis`); `room.send_batch`
     takes the token and NOTHING ELSE (`additionalProperties: false`, no list of drafts, no times),
     so what is sent is what was shown. Unticking a draft in the card is a NEW question
     (`room.batch_token` again with the smaller set; the earlier token dies, one outstanding at a
     time), never a subset of an old answer. Like the other tokens it lives minutes (`TTL`, five:
     there is a list to read), works once and is burned by the first presentation, right or wrong,
     travels only in `_meta` and never in the trace (`SECRET_ARGS`).
 20. A CHANGED WORLD REFUSES THE WHOLE BATCH, BEFORE ANYTHING IS WRITTEN. At the click the server
     reads every row again: a draft edited since the token (another digest), approved or rejected
     elsewhere, past its window, on an account that moved, a delegation that is no longer the
     one the plan named, or a schedule that would slip by more than ten minutes or miss a
     deadline (`replay_schedule`): any of them refuses the batch as `PACZKA_ZMIENIONA`, naming the
     drafts, and writes nothing. The card asks again and shows the NEW plan; it ends at a
     confirmation that waits for a click, as every refusal that heals does. A moment may only
     move LATER than the one shown, never earlier.
 21. THE LIMITS ARE THE SCOPE'S AND NOTHING WIDER. The schedule keeps every rolling hour under
     `publications_per_hour` and every rolling day under `publications_per_day` (own posts also
     under `own_posts_per_day`), counting what already went out and what is queued, and each
     approval re-checks its own moment under the project's lock (`batch_plan.slot_blocked`). A
     reply that does not fit is LEFT OUT and stays waiting (with a code the card words), never
     squeezed in, never sent past the horizon, past its own approval window or past the scope's
     deadline. The pace of a batch is a delay of the publisher (`next_queued` honours
     `not_before`), not a second path around it: the publisher still checks the hour and the day
     against real attempts, the identity of the session and the read-back, for every request.
 22. NEVER AUTOMATIC, ONE CLICK PER BATCH. Nothing here runs by itself: no timer, no model call,
     no retry. A prompt injection that names `room.send_batch` has no token; a model that learned
     both names and called them in order is the residual of line 5 of `card_send.py`, and the
     batch adds nothing to what that one can do except size: the same checks, per draft, and a
     bound (`MAX_BATCH`, 25).
 23. A SCHEDULED REPLY IS LOOKED AT AGAIN WHEN ITS MOMENT COMES (`pilot/send_checks.py`). The
     person agreed to the TEXT, exactly as approved (the digest is bound; nothing ever edits it);
     they did not agree to post it into a conversation that is gone. So at the moment of sending the
     publisher's own checks run again, for every request: the post or thread is gone, locked or
     archived, or our account already answered there (SKIPPED, with the reason); the account's
     session is down or cannot be checked (the request STAYS QUEUED, with the reason, and is
     tried again later). For a scheduled request it adds one: the target is now STALE (an X post
     older than eight hours, a Reddit thread older than 48, both configurable) and it is SKIPPED. A
     reply is therefore either sent exactly as approved, or not sent and says why: the card's
     "Scheduled" list reads that reason from `room.send_status` (`powod`, `powod_kod`).
 24. RESIDUAL, stated plainly: a batch holds the scope's pending requests in the queue for hours,
     so while its last moment has not come `requests_waiting_on` is true and signing a new scope
     answers `ZAKRES_KOLEJKA` (the existing rule: a signature must not stop a queued request).
     The person can pause the scope (the publisher then STOPS what is queued); they cannot cancel
     one request from the card.
 25. THE POST A REPLY ANSWERS IS SOMEONE ELSE'S TEXT, shown above the reply (`opportunities/
     target_context.py`, `snapshot.py`). It is read from a public mirror, the founder's own browser
     or Reddit, kept in `project_opportunities` and drawn by the card as TEXT: clipped, stripped of
     control characters, never as markup. It travels in `room.show` beside the opportunity's own
     excerpt, so the model can read it as it reads that excerpt: data, never an instruction, and
     `room.show` has no write path. The author's avatar and the post's picture are URLs, so
     they are accepted only on the platforms' own image hosts (`opportunities/avatars.py`,
     `PICTURE_HOSTS`, pinned equal to the card's list); the card does not load them itself but
     asks `room.pictures` (`tools/card_pictures.py`) for small inline images, and its CSP
     `resourceDomains` names the same hosts and nothing else, as the fallback for a host that
     cannot call that tool; every other URL, a plain `http:` one and one with credentials
     included, is dropped and the initials are drawn. The read is best effort and bounded and
     NEVER refuses or changes a draft; it signs, approves and publishes nothing.
 26. A STALE DRAFT LEAVES THROUGH THE STATE MACHINE, NEVER BY AN EDIT. `room.show` stays a read: it
     hides a draft whose target is older than the limit (X 8 h, Reddit 48 h, `pilot/send_checks.py`,
     the publisher's own number) and lists the reason under "Skipped", writing nothing. The
     expiry is persisted by the publisher's tick (`opportunities/freshness.py`): the revision
     moves READY_FOR_APPROVAL to EXPIRED by the existing arrow, with its audit row, the text and the
     digest untouched. Nothing is approved or widened by it, and a draft whose age nobody knows, or
     that answers a Reddit COMMENT, is never called stale on a guess.
 27. THE PACE IS THE SCOPE'S, PER ACCOUNT WHEN IT SAYS SO (v11, 13.10). A batch is planned and
     gated on the rules of EACH draft's account (`budget.caps_for`): its own replies per hour and
     day, its own posts per day, and the overall ceiling when one was signed, over the same rows
     the single send is counted on (`budget.committed_ledger`). Reddit's slow hour no longer holds
     X, but nothing is looser than what was signed: every moment passes the one `fits_caps` at
     the planner and again at the gate under the project's lock, a limit of zero leaves ONLY that
     account's drafts out, and a scope signed with one bucket is planned exactly as before. The
     publisher counts the same per account over real attempts (`pace_blocked`), so a request that
     slipped through is still held for ITS account's pace and for no one else's.

WHICH DRAFTS A BATCH TAKES is decided by the card from the data it was given and ALWAYS decided
again by the server from rows (`room.batch_token`): the card's filter (waiting for approval, not
expired, account in the live scope, not being edited or sent) only keeps it from asking about what
cannot go. The drafts of the project, the digests and the scope are never read from the card.
"""

from __future__ import annotations

import hashlib
import random
import re
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from control_plane.opportunities.store import OpportunityStore
from control_plane.presence.approval import ACTION_OF_CAPABILITY, ACTION_OWN_POST
from control_plane.presence.batch_plan import (
    LEFT_LIMIT,
    LEFT_MOVED,
    LEFT_NO_LIMIT,
    LEFT_OWN,
    LEFT_SCOPE,
    LEFT_WINDOW,
    MAX_BATCH,
    SLIP_TOLERANCE,
    Candidate,
    Left,
    Schedule,
    plan_with_caps,
    replay_with_caps,
)
from control_plane.presence.budget import (
    HOUR,
    account_label,
    caps_for,
    committed_ledger,
    effective_limits,
    limits_for,
    reply_ledger,
)
from control_plane.presence.card_consent import CONSENT_SEND, CardConsentRefused
from control_plane.presence.human_approval import (
    HumanApprovalRefused,
    approve_and_queue,
    ensure_waiting,
    payload_of,
)
from control_plane.presence.store import PresenceStore

from .card_send import (
    CODE_CONFIRMATION,
    CODE_NOT_FOUND,
    CODE_SCOPE_MOVED,
    META_TOKEN,
    TokenRefused,
)

#: How long a batch confirmation lives. Longer than a single send's three minutes: there is a list
#: to read, and every tick or untick asks again and gets a fresh one.
BATCH_TTL = timedelta(minutes=5)

CODE_EMPTY = "PACZKA_PUSTA"
CODE_TOO_BIG = "PACZKA_ZA_DUZA"
CODE_CHANGED = "PACZKA_ZMIENIONA"
CODE_OUTSIDE_SCOPE = "POZA_ZAKRESEM"
CODE_ACTION = "DZIALANIE"

_SHA256 = re.compile(r"[0-9a-f]{64}")

#: The sentence for each reason a reply is not in the schedule, for a person (Polish; the card
#: words them itself for any other language, by the code).
LEFT_SENTENCES: dict[str, str] = {
    LEFT_LIMIT: (
        "Nie mieści się w limitach zakresu w najbliższych godzinach. Zostaje na liście "
        "i można ją wysłać w następnej paczce."
    ),
    LEFT_OWN: "Limit własnych postów na dobę jest już zajęty. Szkic zostaje na liście.",
    LEFT_WINDOW: "Okno zgody tego szkicu zamknęłoby się, zanim przyjdzie jego kolej.",
    LEFT_SCOPE: "Podpisany zakres wygasłby, zanim przyjdzie kolej tego szkicu.",
    LEFT_MOVED: (
        "Termin tego szkicu przesunął się o ponad "
        f"{int(SLIP_TOLERANCE.total_seconds() // 60)} minut od planu, który widziałeś."
    ),
    LEFT_NO_LIMIT: "Zakres nie dopuszcza ani jednej publikacji na godzinę albo na dobę.",
}
OUTSIDE_SCOPE_SENTENCE = (
    "Konto tego szkicu nie jest w podpisanym zakresie. Wyślij ten szkic osobno albo ustaw Limity: "
    "jedno kliknięcie w paczce niczego nie podpisuje."
)


def set_digest(items: Sequence[tuple[UUID, str]]) -> str:
    """The digest of a SET of drafts: SHA-256 over the sorted `revision:digest` lines. What the
    token is about, in one string a test or an audit row can quote."""
    lines = sorted(f"{revision}:{payload_hash}" for revision, payload_hash in items)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BatchItem:
    """One draft of a confirmed batch. The caller of `room.send_batch` chooses nothing in it."""

    revision_id: UUID
    payload_hash: str
    account_id: UUID
    #: The moment the person was shown for it.
    at: datetime


@dataclass(frozen=True)
class BatchGrant:
    items: tuple[BatchItem, ...]
    #: `ConsentPlan.basis()` of the live scope the plan was drawn against (one for every draft).
    basis: str
    expires_at: datetime

    @property
    def digest(self) -> str:
        return set_digest([(item.revision_id, item.payload_hash) for item in self.items])


class BatchTokens:
    """One-time confirmations for a batch, in memory, for the life of the tool server.

    In memory and hashed for the reasons `card_send.SendTokens` gives. ONE outstanding at a time:
    a person has one panel open, and every tick or untick is a new question whose answer replaces
    the last, so an old confirmation for a set that is no longer on the screen is dead the moment
    the new one is minted.
    """

    def __init__(
        self,
        *,
        ttl: timedelta = BATCH_TTL,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._grants: dict[str, BatchGrant] = {}

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def now(self) -> datetime:
        return self._clock()

    def mint(self, *, items: Sequence[BatchItem], basis: str) -> tuple[str, datetime]:
        self._grants.clear()
        token = secrets.token_urlsafe(32)
        expires_at = self._clock() + self._ttl
        self._grants[self._key(token)] = BatchGrant(
            items=tuple(items), basis=basis, expires_at=expires_at
        )
        return token, expires_at

    def redeem(self, token: str) -> BatchGrant:
        """The grant behind `token`, consumed. Any presentation of a known token burns it."""
        grant = self._grants.pop(self._key(token), None)
        if grant is None:
            raise TokenRefused(
                CODE_CONFIRMATION,
                "Brak ważnego potwierdzenia kliknięciem: nieznane albo już zużyte. Wysyłkę paczki "
                "zatwierdza człowiek w karcie, agent tego nie robi.",
            )
        if grant.expires_at <= self._clock():
            raise TokenRefused(CODE_CONFIRMATION, "Potwierdzenie wygasło. Spróbuj ponownie.")
        return grant


CARD_BATCH_TOOL_SPECS = (
    {
        "name": "room.batch_token",
        "summary": "TYLKO DLA KARTY: plan wysyłki paczki szkiców i potwierdzenie, po kliknięciu.",
        "detail": (
            "Narzędzie dla karty Pokoju w agencie, niewidoczne dla modelu (visibility: app). "
            "Sprawdza KAŻDY szkic tak, jak room.send_token (czeka na zgodę, okno nie minęło, "
            "odcisk jest tym, który człowiek widział, konto jest w podpisanym zakresie), układa "
            "je w kolejności (świeże posty na X najpierw) i w terminach, które mieszczą się w "
            "limitach zakresu na godzinę i na dobę, z losową zwłoką kilku minut, i odpowiada "
            "planem w `paczka` oraz listą szkiców, które nie weszły, w `odrzucone`. "
            "Potwierdzenie jedzie tylko w `_meta` wyniku, ważne pięć minut, jednorazowe, związane "
            "z dokładnie tym zestawem szkiców i ich odciskami. Niczego nie wysyła i niczego nie "
            "podpisuje; nie ma `writes`, żeby powtórne wywołanie wydało nowe potwierdzenie."
        ),
        "state": "DOSTEPNE",
        "input_schema": {
            "type": "object",
            "properties": {
                "revision_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": MAX_BATCH,
                },
                "odciski": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": MAX_BATCH,
                    "description": "Odciski szkiców z karty, w kolejności revision_ids.",
                },
            },
            "required": ["revision_ids", "odciski"],
            "additionalProperties": False,
        },
        "tags": ("karta",),
    },
    {
        "name": "room.send_batch",
        "summary": "TYLKO DLA KARTY: wyślij paczkę szkiców do publikatora po kliknięciu człowieka.",
        "detail": (
            "Narzędzie dla karty Pokoju w agencie, niewidoczne dla modelu (visibility: app). "
            "Wymaga potwierdzenia z room.batch_token, jednorazowego i związanego z dokładnym "
            "zestawem szkiców, ich odcisków i terminów; niczego innego nie przyjmuje. Najpierw "
            "czyta każdy szkic jeszcze raz: jeśli którykolwiek się zmienił (inny odcisk, "
            "zatwierdzony gdzie indziej, okno minęło, zakres inny niż w planie) albo termin "
            "przesunąłby się o więcej niż dziesięć minut, odmawia CAŁEJ paczki i niczego nie "
            "zapisuje. Potem zatwierdza i kolejkuje każdy szkic tą samą drogą co przycisk w "
            "dashboardzie, z terminem `not_before`, tak żeby paczka wychodziła w odstępach, "
            "w limitach zakresu. Nic nie podpisuje. W swoim terminie każdy wniosek jest "
            "sprawdzany przez publikator jeszcze raz (wątek zniknął, jest zablokowany, już "
            "odpowiedzieliśmy, wątek jest za stary: wniosek jest pomijany z powodem; sesja konta "
            "nie działa: wniosek czeka na kolejną próbę), a tekst nigdy się nie zmienia. "
            "WYNIK TO OCZEKUJE: wnioski stoją w kolejce publikatora, a „opublikowano” wolno "
            "powiedzieć dopiero po odczycie zwrotnym (room.send_status)."
        ),
        "state": "DOSTEPNE",
        "writes": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "Potwierdzenie z room.batch_token."}
            },
            "required": ["token"],
            "additionalProperties": False,
        },
        "tags": ("karta", "działanie"),
    },
)


@dataclass(frozen=True)
class _Checked:
    """A draft that passed every check of `room.send_token` for the batch."""

    revision_id: UUID
    row: Any
    payload_hash: str
    account_id: UUID
    basis: str
    candidate: Candidate
    platform: str
    account: str
    kind: str


@dataclass(frozen=True)
class _Problem:
    revision_id: UUID
    code: str
    detail: str

    def to_json(self) -> dict[str, str]:
        return {"revision_id": str(self.revision_id), "kod": self.code, "powod": self.detail}


class CardBatchTools:
    """Mixin of `ToolRegistry`: the two app-only tools behind the card's "Send all"."""

    @property
    def _batch_tokens(self) -> BatchTokens:
        # A property over `__dict__`, like `_scope_tokens`: `registry.ToolRegistry` is not edited
        # for a token store, and a test can set `registry._batch_tokens = BatchTokens(...)`.
        store = self.__dict__.get("_batch_tokens_value")
        if store is None:
            store = self.__dict__["_batch_tokens_value"] = BatchTokens()
        return store  # type: ignore[no-any-return]

    @_batch_tokens.setter
    def _batch_tokens(self, value: BatchTokens) -> None:
        self.__dict__["_batch_tokens_value"] = value

    @property
    def _batch_rng(self) -> random.Random:
        """The dice of the schedule. A test sets `registry._batch_rng = random.Random(1)`."""
        rng = self.__dict__.get("_batch_rng_value")
        if rng is None:
            rng = self.__dict__["_batch_rng_value"] = random.Random()
        return rng  # type: ignore[no-any-return]

    @_batch_rng.setter
    def _batch_rng(self, value: random.Random) -> None:
        self.__dict__["_batch_rng_value"] = value

    # -- plumbing -----------------------------------------------------------------------------

    def _batch_refusal(self, reason: str, code: str, **extra: Any) -> dict[str, Any]:
        from .registry import unavailable

        return unavailable(reason, kod=code, **extra)

    async def _batch_thread_time(self, row: Any, payload: dict[str, Any]) -> datetime | None:
        """When the thread this draft answers was created on the platform, from rows the server
        holds (the opportunity the agent recorded, else the observed thread), or `None`."""
        target = str(payload.get("target_external_id") or "").strip()
        if not target:
            return None
        capability = self.capability  # type: ignore[attr-defined]
        opportunity = await OpportunityStore(self.pool).for_target(  # type: ignore[attr-defined]
            organization_id=capability.organization_id,
            project_id=capability.project_id,
            external_id=target,
        )
        if opportunity is not None and opportunity.created_at_source is not None:
            return opportunity.created_at_source  # type: ignore[no-any-return]
        async with self.pool.acquire() as conn:  # type: ignore[attr-defined]
            found = await conn.fetchval(
                "SELECT t.created_at_source FROM observed_threads t "
                "WHERE t.organization_id = $1 AND t.workspace_id = $2 "
                "AND (t.external_id = $3 OR ('t3_' || t.external_id) = $3) LIMIT 1",
                capability.organization_id,
                capability.workspace_id,
                target,
            )
        return found  # type: ignore[no-any-return]

    async def _batch_check(
        self, revision_id: UUID, shown: str, *, with_thread: bool
    ) -> _Checked | _Problem:
        """ONE draft, checked exactly as `room.send_token` checks it, and then some: the plan has to
        be a plain send in a live scope that lists the account (line 18)."""
        from .registry import ToolError

        row = await self._card_draft(revision_id)  # type: ignore[attr-defined]
        if row is None:
            return _Problem(
                revision_id, CODE_NOT_FOUND, "Nie ma takiej propozycji w tym projekcie."
            )
        try:
            ensure_waiting(row, shown)
            plan = await self._card_plan(row)  # type: ignore[attr-defined]
        except HumanApprovalRefused as exc:
            return _Problem(revision_id, exc.code, exc.detail)
        except CardConsentRefused as exc:
            return _Problem(revision_id, exc.code, exc.detail)
        except ToolError as exc:
            return _Problem(revision_id, CODE_ACTION, str(exc))
        if plan.kind != CONSENT_SEND:
            return _Problem(revision_id, CODE_OUTSIDE_SCOPE, OUTSIDE_SCOPE_SENTENCE)
        payload = payload_of(row)
        action = ACTION_OF_CAPABILITY.get(str(payload.get("action_type")))
        platform = str(row["platform"])
        return _Checked(
            revision_id=revision_id,
            row=row,
            payload_hash=str(row["payload_hash"]),
            account_id=row["social_account_id"],
            basis=plan.basis(),
            candidate=Candidate(
                revision_id=revision_id,
                platform=platform,
                own_post=action == ACTION_OWN_POST,
                thread_at=await self._batch_thread_time(row, payload) if with_thread else None,
                expires_at=row["expires_at"],
                account_id=row["social_account_id"],
            ),
            platform=platform,
            account=str(row["account_handle"]),
            kind="OWN_POST" if action == ACTION_OWN_POST else "REPLY",
        )

    @staticmethod
    def _left_problems(left: Sequence[Left]) -> list[_Problem]:
        return [
            _Problem(item.revision_id, item.code, LEFT_SENTENCES.get(item.code, item.code))
            for item in left
        ]

    # -- room.batch_token ---------------------------------------------------------------------

    async def _room_batch_token(self, args: dict[str, Any]) -> dict[str, Any]:
        from .registry import ToolError, done

        ids, shown = args.get("revision_ids"), args.get("odciski")
        if not isinstance(ids, list) or not isinstance(shown, list) or len(ids) != len(shown):
            raise ToolError("revision_ids i odciski to dwie listy o tej samej długości")
        if not ids:
            return self._batch_refusal("Nie ma żadnego szkicu do wysłania.", CODE_EMPTY)
        if len(ids) > MAX_BATCH:
            return self._batch_refusal(
                f"Paczka ma najwyżej {MAX_BATCH} szkiców, a w tej jest {len(ids)}.",
                CODE_TOO_BIG,
                limit=MAX_BATCH,
            )
        pairs: list[tuple[UUID, str]] = []
        for raw_id, raw_digest in zip(ids, shown, strict=True):
            revision_id = self._uuid(raw_id, "revision_ids")  # type: ignore[attr-defined]
            digest = str(raw_digest or "").strip().lower()
            if not _SHA256.fullmatch(digest):
                raise ToolError("odcisk szkicu to 64 znaki szesnastkowe, takie jak na karcie")
            pairs.append((revision_id, digest))
        if len({revision_id for revision_id, _ in pairs}) != len(pairs):
            raise ToolError("szkice w paczce muszą być różne")

        capability = self.capability  # type: ignore[attr-defined]
        checked: list[_Checked] = []
        problems: list[_Problem] = []
        for revision_id, digest in pairs:
            outcome = await self._batch_check(revision_id, digest, with_thread=True)
            if isinstance(outcome, _Problem):
                problems.append(outcome)
            else:
                checked.append(outcome)
        if not checked:
            codes = {problem.code for problem in problems}
            only = next(iter(codes)) if len(codes) == 1 else CODE_EMPTY
            return self._batch_refusal(
                problems[0].detail if len(codes) == 1 else "Żaden z tych szkiców nie może wyjść.",
                only,
                odrzucone=[problem.to_json() for problem in problems],
                obecnosc_adres=self._presence_address(),  # type: ignore[attr-defined]
            )
        if len({one.basis for one in checked}) != 1:
            # Two different scopes in one batch cannot happen with one delegation; belt and braces.
            return self._batch_refusal(
                "Zakres zmienił się w trakcie sprawdzania. Spróbuj ponownie.", CODE_SCOPE_MOVED
            )

        delegation = await PresenceStore(self.pool).delegation_for(  # type: ignore[attr-defined]
            organization_id=capability.organization_id, project_id=capability.project_id
        )
        if delegation is None or not delegation.live:  # cannot be: every plan was a plain send
            return self._batch_refusal(
                "Zakres zmienił się w trakcie sprawdzania. Spróbuj ponownie.", CODE_SCOPE_MOVED
            )
        limits = effective_limits(delegation)
        tokens = self._batch_tokens
        now = tokens.now()
        async with self.pool.acquire() as conn:  # type: ignore[attr-defined]
            ledger = await committed_ledger(
                conn,
                organization_id=capability.organization_id,
                project_id=capability.project_id,
                accounts=delegation.scope.accounts,
                now=now,
            )
        schedule = plan_with_caps(
            [_with_rules(one, limits) for one in checked],
            now=now,
            ledger=ledger,
            rng=self._batch_rng,
            valid_until=delegation.expires_at,
            per_hour=limits.publications_per_hour,
            per_day=limits.publications_per_day,
            own_per_day=limits.own_posts_per_day,
        )
        problems += self._left_problems(schedule.left)
        if not schedule.placed:
            return self._batch_refusal(
                "Żaden z tych szkiców nie mieści się teraz w limitach zakresu.",
                schedule.left[0].code if schedule.left else CODE_EMPTY,
                odrzucone=[problem.to_json() for problem in problems],
                limity=_limits_json(limits, checked),
            )
        by_id = {one.revision_id: one for one in checked}
        items = [
            BatchItem(
                revision_id=placed.revision_id,
                payload_hash=by_id[placed.revision_id].payload_hash,
                account_id=by_id[placed.revision_id].account_id,
                at=placed.at,
            )
            for placed in schedule.placed
        ]
        token, expires_at = tokens.mint(items=items, basis=checked[0].basis)
        return {
            **done(
                paczka={
                    **schedule.to_json(now=now),
                    "limity": _limits_json(limits, checked),
                    "zestaw": set_digest([(i.revision_id, i.payload_hash) for i in items]),
                    "pozycje": [
                        {
                            "lp": number,
                            "revision_id": str(placed.revision_id),
                            "platforma": by_id[placed.revision_id].platform,
                            "konto": by_id[placed.revision_id].account,
                            "rodzaj": by_id[placed.revision_id].kind,
                            "kiedy": placed.at.isoformat(),
                        }
                        for number, placed in enumerate(schedule.placed, start=1)
                    ],
                },
                odrzucone=[problem.to_json() for problem in problems],
                wygasa_potwierdzenie=expires_at.isoformat(),
            ),
            # ONLY the card reads this, and nothing that is written to a trace or shown as text.
            "_mcp_meta": {META_TOKEN: token},
        }

    # -- room.send_batch ----------------------------------------------------------------------

    async def _room_send_batch(self, args: dict[str, Any]) -> dict[str, Any]:
        from .registry import pending

        capability = self.capability  # type: ignore[attr-defined]
        token = str(args.get("token") or "")
        if not token:
            return self._batch_refusal(
                "Brak potwierdzenia kliknięciem. Wysyłkę paczki zatwierdza człowiek w karcie, "
                "agent tego nie robi.",
                CODE_CONFIRMATION,
            )
        # THE TOKEN IS CONSUMED FIRST, before anything is looked at or written: whatever else
        # happens, this confirmation is spent, and a second send needs a second click.
        try:
            grant = self._batch_tokens.redeem(token)
        except TokenRefused as exc:
            return self._batch_refusal(exc.detail, exc.code)

        # 1. EVERY DRAFT AGAIN, BEFORE ANYTHING IS WRITTEN (line 20).
        rows: dict[UUID, _Checked] = {}
        problems: list[_Problem] = []
        for item in grant.items:
            outcome = await self._batch_check(
                item.revision_id, item.payload_hash, with_thread=False
            )
            if isinstance(outcome, _Problem):
                problems.append(outcome)
            elif outcome.account_id != item.account_id:
                problems.append(
                    _Problem(
                        item.revision_id,
                        "ODCISK",
                        "Konto tego szkicu zmieniło się od chwili, gdy go pokazano.",
                    )
                )
            elif outcome.basis != grant.basis:
                problems.append(
                    _Problem(
                        item.revision_id,
                        CODE_SCOPE_MOVED,
                        "Zakres zmienił się od chwili, gdy pokazano potwierdzenie.",
                    )
                )
            else:
                rows[item.revision_id] = outcome
        if problems:
            return self._changed(problems)

        # 2. THE SCHEDULE, AGAIN, WITH NO DICE: the moments the person saw, or later, or a refusal.
        delegation = await PresenceStore(self.pool).delegation_for(  # type: ignore[attr-defined]
            organization_id=capability.organization_id, project_id=capability.project_id
        )
        if delegation is None or not delegation.live:
            return self._batch_refusal(
                "Zakres zmienił się od chwili, gdy pokazano potwierdzenie. Spróbuj ponownie.",
                CODE_SCOPE_MOVED,
            )
        limits = effective_limits(delegation)
        now = self._batch_tokens.now()
        async with self.pool.acquire() as conn:  # type: ignore[attr-defined]
            ledger = await committed_ledger(
                conn,
                organization_id=capability.organization_id,
                project_id=capability.project_id,
                accounts=delegation.scope.accounts,
                now=now,
            )
        schedule = replay_with_caps(
            [(_with_rules(rows[item.revision_id], limits), item.at) for item in grant.items],
            now=now,
            ledger=ledger,
            valid_until=delegation.expires_at,
            per_hour=limits.publications_per_hour,
            per_day=limits.publications_per_day,
            own_per_day=limits.own_posts_per_day,
        )
        if schedule.left:
            return self._changed(self._left_problems(schedule.left))

        # 3. THE WRITES, in the order of the plan. Each approval re-checks its own moment under
        # the project's lock, so a race (another click, a publication) can still refuse ONE draft;
        # what was queued before it stays queued and is reported as such.
        reason = (
            f"zgoda człowieka kliknięciem w karcie agenta ({capability.host}), "
            f"paczka {len(grant.items)} szkiców, zestaw {grant.digest[:12]}"
        )
        queued: list[dict[str, Any]] = []
        refused: list[_Problem] = []
        for placed in schedule.placed:
            checked = rows[placed.revision_id]
            try:
                issued = await approve_and_queue(
                    self.pool,  # type: ignore[attr-defined]
                    organization_id=capability.organization_id,
                    workspace_id=capability.workspace_id,
                    project_id=capability.project_id,
                    row=checked.row,
                    signer=None,
                    reason=reason,
                    expected_payload_hash=checked.payload_hash,
                    not_before=placed.at,
                )
            except HumanApprovalRefused as exc:
                refused.append(_Problem(placed.revision_id, exc.code, exc.detail))
                continue
            queued.append(
                {
                    "revision_id": str(placed.revision_id),
                    "wniosek": str(issued.request_id),
                    "kiedy": placed.at.isoformat(),
                    "platforma": checked.platform,
                    "konto": checked.account,
                }
            )
        extra = {
            "zaplanowane": queued,
            "odmowy": [problem.to_json() for problem in refused],
            "paczka": {
                **schedule.to_json(now=now),
                "limity": _limits_json(limits, list(rows.values())),
            },
        }
        if not queued:
            return self._batch_refusal(
                refused[0].detail if refused else "Nic nie weszło do kolejki.",
                refused[0].code if refused else CODE_EMPTY,
                **extra,
            )
        return pending(
            "wnioski stoją w kolejce publikatora w terminach z planu; „opublikowano” dopiero po "
            "odczycie zwrotnym",
            **extra,
        )

    def _changed(self, problems: Sequence[_Problem]) -> dict[str, Any]:
        """The whole batch refused because the world is not the one the person was shown."""
        return self._batch_refusal(
            "Coś w paczce zmieniło się od chwili, gdy ją pokazano, więc nic nie wysłałem. "
            "Sprawdź listę jeszcze raz.",
            CODE_CHANGED,
            odrzucone=[problem.to_json() for problem in problems],
        )


def _with_rules(one: _Checked, limits: Any) -> Candidate:
    """The candidate with the rules of ITS account (`budget.caps_for`): its own replies and own
    posts and, when the scope signed one, the overall ceiling. With one bucket signed the rules
    are the project's totals, so a scope from before v11 plans exactly as it always did."""
    candidate = one.candidate
    pace = None
    if limits.per_account and candidate.own_post and candidate.account_id is not None:
        # An own post has no hourly rule of its own (its cap is per day), but a batch of them is
        # still not a burst: it is spaced like the replies of the same account, on the same key.
        mine = limits_for(limits, candidate.account_id, one.platform)
        pace = (
            reply_ledger(candidate.account_id),
            HOUR / max(1, mine.replies_per_hour),
        )
    return replace(
        candidate,
        pace=pace,
        caps=caps_for(
            limits,
            account_id=candidate.account_id,
            platform=one.platform,
            own_post=candidate.own_post,
            label=account_label(one.platform, one.account),
        ),
    )


def _limits_json(limits: Any, checked: Sequence[_Checked] = ()) -> dict[str, Any]:
    """The limits as the card tells them. The three project numbers always (the one bucket of a
    scope from before v11); with limits per account also one entry for each ACCOUNT IN THIS BATCH,
    in the card's words, and the overall ceiling when one was signed."""
    out: dict[str, Any] = {
        "godzina": limits.publications_per_hour,
        "doba": limits.publications_per_day,
        "wlasne_doba": limits.own_posts_per_day,
    }
    if limits.per_account:
        seen: dict[Any, dict[str, Any]] = {}
        for one in checked:
            if one.account_id in seen:
                continue
            mine = limits_for(limits, one.account_id, one.platform)
            seen[one.account_id] = {
                "konto": one.account,
                "platforma": one.platform,
                "godzina": mine.replies_per_hour,
                "doba": mine.replies_per_day,
                "wlasne_doba": mine.own_posts_per_day,
            }
        out["konta"] = sorted(seen.values(), key=lambda a: (a["platforma"], a["konto"]))
        if limits.overall_per_hour() is not None:
            out["razem_godzina"] = limits.overall_per_hour()
        if limits.overall_per_day() is not None:
            out["razem_doba"] = limits.overall_per_day()
    return out


__all__ = [
    "BATCH_TTL",
    "CARD_BATCH_TOOL_SPECS",
    "CODE_CHANGED",
    "CODE_EMPTY",
    "CODE_OUTSIDE_SCOPE",
    "CODE_TOO_BIG",
    "LEFT_SENTENCES",
    "BatchGrant",
    "BatchItem",
    "BatchTokens",
    "CardBatchTools",
    "Schedule",
    "set_digest",
]
