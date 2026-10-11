"""The one click that signs a minimal scope and sends, from the card in the person's own agent.

WHY THIS EXISTS (founder, 11.10). A first "Wyślij" in a project with no signed scope used to end
in "brak podpisanego zakresu" and a trip to the Obecność page, whose form has nine steps (mode,
deadline, accounts, actions, limits, "Podpisuję powierzenie"). A person who has just read a reply
in Codex and wants it to go will not do that. So the confirmation step of the FIRST send carries
the consent itself: "Wenext może pisać z @a i u/b. Każdą odpowiedź pokażę Ci przed wysłaniem", and
that one click signs a scope as small as the sentence says and then sends.

ONE CONSENT COVERS THE PROJECT'S ACCOUNTS (11.10, from the founder's first real use). v4 and v5
signed the account of the clicked draft only, so the founder, who has an X and a Reddit account in
the project, signed for X, saw the Reddit draft refused ("poprzednia odpowiedź jeszcze czeka"), and
was asked to consent a second time for the same project. The sentence now names every account and
the click signs every one of them: ALL accounts assigned to the project whose platform the matrix
(`workspace/platforms.py`) lets REPLY, and OWN_POST too when the draft is an own post. They are read
from `project_account_assignments` by the server (`assigned_accounts`), never from anything the
card sends: the card's tools take a revision id, a digest and a token, and the card only DRAWS the
names the server put in the plan (`ConsentPlan.to_json`, key `konta`). An account the person
stopped (`PAUSED`, `QUARANTINED`) is not added on their behalf: the dashboard's form lets a person
tick whatever they like, and this click chooses for them, so it leaves a stop alone. The account of
the clicked draft is always in the scope, so the click can always send what it was pressed for.

THE LIMITS DID NOT MOVE, AND THAT IS ON PURPOSE. A scope for two accounts keeps the product's
pilot pace: one publication an hour and six a day. `budget.spent` counts the publications of EVERY
account of the scope together (and the queue by project), so these numbers bind the whole project,
not each account: covering more accounts raises nobody's pace, and the worst case (every text from
one account) is the one the single-account scope already had. Nothing here raises them; a person who
wants more says so in Obecność, where the number is typed and signed.

IT IS THE EXISTING SIGNATURE, NOT A LIGHTER ONE. `PresenceStore.sign` is what the dashboard's
form calls: the same row in `project_delegations`, the same digest over mode, scope, budget and
deadline, the same audit entry, the same interruption of background sessions when a scope is
replaced. Only the INPUT is decided here instead of typed: mode REVIEW (every text of the project
waits for a click, so nothing is ever sent unseen), the accounts above, the actions of the draft
(REPLY, and OWN_POST for an own post), the publication limits the product defaults to (written out,
so a later change of the defaults cannot move what was signed) and the default term.

THREE CASES, AND ONLY THREE (`plan_consent`):

  * `WYSLIJ`: a live signature already covers the account. Nothing is signed; the click is the
    approval the dashboard would record.
  * `PODPISZ`: no live signature (never signed, revoked, expired, its signer gone). The click
    signs the minimal scope above, for every eligible account of the project.
  * `ROZSZERZ`: a live REVIEW signature that does not list this account (an account added to the
    project after it was signed, or a signature made by an older card for one account). The click
    signs a new one that adds the eligible accounts it lacks and closes the old one, keeping the
    mode, the limits, the actions and the deadline the person chose. In any other mode this is
    REFUSED and the person is sent to the Obecność page: adding an account to an AUTONOMOUS scope
    with one click in a chat would let that account publish with nobody looking, which the sentence
    on the card does not say.

A PAUSE IS A STOP, AND A CLICK DOES NOT LIFT IT. A paused signature is refused (`ZAKRES_WSTRZYMANY`)
and the person resumes it where they paused it. Replacing it with a fresh signature from a card
would turn the stop button into a suggestion.

WHO THE SIGNER IS. The process that serves the card is the plugin's tool server, which has no
operator session and must not get one. The signature is filed under the organization's single
operator (`PublicationStore.sole_operator_id`, the same rule the pilot's consents use, D-016), and
the audit entry says it came through the plugin (`actor_ref` `plugin:<host>`). What proves a PERSON
pressed the button is not here: it is the one-time token of `tools/card_send.py`, which this
module never sees, and signing is reachable ONLY behind it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
from community_os_contracts import SocialAccountState

from control_plane.audit import AuditWriter
from control_plane.pilot.publication import PublicationStore
from control_plane.sessions.store import EVENT_INTERRUPT, SESSION_PRESENCE, SessionStore
from control_plane.workspace.platforms import support_for

from .budget import DEFAULT_PUBLICATIONS_PER_DAY, DEFAULT_PUBLICATIONS_PER_HOUR
from .scope import (
    ACTION_REPLY,
    DEFAULT_TERM,
    MODE_REVIEW,
    Budget,
    Scope,
    action_supported,
)
from .store import DelegationRow, PresenceStore

CONSENT_SEND = "WYSLIJ"
CONSENT_SIGN = "PODPISZ"
CONSENT_WIDEN = "ROZSZERZ"

REFUSED_PAUSED = "ZAKRES_WSTRZYMANY"
REFUSED_MODE = "ZAKRES_INNY_TRYB"
REFUSED_QUEUE = "ZAKRES_KOLEJKA"

#: A widened signature keeps the old deadline, but not one that is about to run out: a scope that
#: ends in a minute is not a thing to hand somebody who has just agreed to a week.
_MIN_KEPT_TERM = timedelta(hours=1)


#: Accounts the person (or the system) stopped. A click that chooses the accounts FOR the person
#: does not add one of these to a scope; the account of the clicked draft is the one exception,
#: because the click is about that draft (and the publisher still checks the account's state before
#: it writes anything).
STOPPED_STATES = frozenset({SocialAccountState.PAUSED.value, SocialAccountState.QUARANTINED.value})


class CardConsentRefused(Exception):
    """The click cannot sign anything here. `code` says why; the person goes to Obecność."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ConsentAccount:
    """One account of the project, as the SERVER read it: the id the scope lists and the name the
    card shows. `state` is `social_accounts.state`, kept only to leave a stopped account alone."""

    account_id: UUID
    platform: str
    handle: str
    state: str = ""

    def to_json(self) -> dict[str, str]:
        return {"platforma": self.platform, "konto": self.handle}


@dataclass(frozen=True)
class ConsentPlan:
    """What ONE click will do about the scope, decided before the person is asked.

    `delegation_id` is the signature the plan was drawn against (`None` when there was none). The
    card's token carries it, so a send that finds a different one refuses: the person agreed to
    the sentence they were shown, not to whatever the scope became in the meantime.
    """

    kind: str
    delegation_id: UUID | None
    mode: str
    scope: Scope
    budget: Budget
    expires_at: datetime | None
    #: The signature a new one closes (`PresenceStore.sign(replaces=...)`), or `None`.
    replaces: UUID | None
    #: The accounts this click puts INTO a scope, the ones the sentence names: every eligible
    #: account of the project for `PODPISZ`, the eligible ones the old scope lacks for `ROZSZERZ`,
    #: none for `WYSLIJ` (nothing is signed). Ordered, so the sentence reads the same on every
    #: draft.
    accounts: tuple[ConsentAccount, ...] = ()

    @property
    def signs(self) -> bool:
        return self.kind != CONSENT_SEND

    def basis(self) -> str:
        """The string the token binds: the kind, the signature it was planned against and, for a
        plan that signs, WHICH ACCOUNTS it signs.

        The accounts are in it because the person agrees to a sentence that NAMES them: an account
        assigned to the project between "Wyślij" and "Tak, wyślij" would otherwise be signed into
        the scope without being on the screen. The mismatch surfaces as `ZAKRES_ZMIENIONY`, and the
        card then asks again and shows the new sentence.
        """
        base = f"{self.kind}:{self.delegation_id or '-'}"
        if not self.signs:
            return base
        listed = ",".join(sorted(str(account) for account in self.scope.accounts))
        return f"{base}:{hashlib.sha256(listed.encode('utf-8')).hexdigest()[:16]}"

    def to_json(self) -> dict[str, Any]:
        """What the card tells the person. Limits are the ones that will bind, as numbers."""
        return {
            "rodzaj": self.kind,
            "tryb": self.mode,
            "wygasa": self.expires_at.isoformat() if self.expires_at else None,
            "limity": {
                "godzina": self.budget.publications_per_hour,
                "doba": self.budget.publications_per_day,
            },
            "konta": [account.to_json() for account in self.accounts],
        }


def minimal_budget() -> Budget:
    """The publication limits the product applies to a signature that names none, written out.

    The same one an hour and six a day as when the scope held one account, NOT raised for the
    accounts it holds now: `budget.spent` adds up every account of the scope, so these bind the
    project as a whole (see the module docstring).
    """
    return Budget(
        publications_per_day=DEFAULT_PUBLICATIONS_PER_DAY,
        publications_per_hour=DEFAULT_PUBLICATIONS_PER_HOUR,
    )


def _term(now: datetime) -> datetime:
    return (now + DEFAULT_TERM).replace(microsecond=0)


def _sorted(values: set[Any]) -> tuple[Any, ...]:
    return tuple(sorted(values, key=str))


def eligible_accounts(
    assigned: Iterable[ConsentAccount], *, draft: ConsentAccount, actions: Iterable[str]
) -> tuple[ConsentAccount, ...]:
    """The accounts a click may put in a scope, PURE: the project's assigned accounts whose
    platform can do every one of `actions` (the matrix), minus the stopped ones, plus the draft's
    own account.

    Ordered by platform and login, and without duplicates, so the sentence is the same whichever
    draft was clicked and the basis of a token does not depend on the order the rows came in.
    """
    needed = tuple(actions)
    chosen: dict[UUID, ConsentAccount] = {draft.account_id: draft}
    for account in assigned:
        if account.state in STOPPED_STATES:
            continue
        support = support_for(account.platform)
        if all(action_supported(action, support) for action in needed):
            chosen.setdefault(account.account_id, account)
    return tuple(sorted(chosen.values(), key=lambda a: (a.platform, a.handle, str(a.account_id))))


async def assigned_accounts(
    pool: asyncpg.Pool, *, organization_id: UUID, workspace_id: UUID, project_id: UUID
) -> tuple[ConsentAccount, ...]:
    """Every account assigned to this project right now. THE ONLY SOURCE of the accounts a click
    signs: the database, filtered by `eligible_accounts`, never a value the card sent."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT a.id, a.platform, a.external_account_id, a.state "
            "FROM project_account_assignments x JOIN social_accounts a "
            "ON a.organization_id = x.organization_id AND a.id = x.social_account_id "
            "WHERE x.organization_id = $1 AND x.workspace_id = $2 AND x.project_id = $3 "
            "AND x.unassigned_at IS NULL ORDER BY a.platform, a.external_account_id",
            organization_id,
            workspace_id,
            project_id,
        )
    return tuple(
        ConsentAccount(
            account_id=row["id"],
            platform=str(row["platform"]),
            handle=str(row["external_account_id"]),
            state=str(row["state"]),
        )
        for row in rows
    )


def plan_consent(
    delegation: DelegationRow | None,
    *,
    account: ConsentAccount,
    community_key: str | None,
    action: str,
    assigned: Sequence[ConsentAccount] = (),
    now: datetime | None = None,
) -> ConsentPlan:
    """The one decision of this module, PURE: what a click will do for this draft.

    `account` is the draft's own account, `assigned` every account the project has (rows the SERVER
    read: `assigned_accounts`). A click that signs covers `eligible_accounts` of them, so one
    consent serves the whole project. `action` is `REPLY` or `OWN_POST`, the vocabulary of a scope
    (`presence.scope.ACTIONS`). Raises `CardConsentRefused` for a paused signature and for a
    widening that is not REVIEW.
    """
    moment = now or datetime.now(UTC)
    account_id = account.account_id
    actions = {ACTION_REPLY, action}
    communities = {community_key} if community_key else set()
    covered = eligible_accounts(assigned, draft=account, actions=actions)

    def minimal() -> ConsentPlan:
        return ConsentPlan(
            kind=CONSENT_SIGN,
            delegation_id=delegation.id if delegation is not None else None,
            mode=MODE_REVIEW,
            scope=Scope(
                accounts=_sorted({a.account_id for a in covered}),
                communities=_sorted(communities),
                actions=_sorted(actions),
            ),
            budget=minimal_budget(),
            expires_at=_term(moment),
            replaces=delegation.id if delegation is not None else None,
            accounts=covered,
        )

    if delegation is None or delegation.expired:
        return minimal()  # an expired signature governs nothing, paused or not
    if delegation.paused_at is not None:
        # BEFORE the dead-signer case below: a paused scope stays a stop even when its signer is
        # gone. Replacing it with a fresh signature would lift the pause with a click.
        raise CardConsentRefused(
            REFUSED_PAUSED,
            "Zakres tego projektu jest wstrzymany. Wznów go w Obecności: wstrzymanie to Twój "
            "stop i kliknięcie w karcie go nie zdejmuje.",
        )
    if not delegation.signer_active:
        return minimal()
    if account_id in delegation.scope.accounts:
        return ConsentPlan(
            kind=CONSENT_SEND,
            delegation_id=delegation.id,
            mode=delegation.mode,
            scope=delegation.scope,
            budget=delegation.budget,
            expires_at=delegation.expires_at,
            replaces=None,
        )
    if delegation.mode != MODE_REVIEW:
        raise CardConsentRefused(
            REFUSED_MODE,
            f"Zakres tego projektu działa w trybie {delegation.mode} i nie obejmuje tego konta. "
            "Dodaj je w Obecności: z karty dodaję konta tylko do zakresu, w którym każdy tekst "
            "czeka na Twoje kliknięcie.",
        )
    kept = delegation.expires_at
    expires_at = kept if kept is not None and kept > moment + _MIN_KEPT_TERM else _term(moment)
    # Every eligible account the old scope lacks, not only this draft's: after this click the scope
    # covers the project, and the sentence names what is added. (The draft's account is among them.)
    added = tuple(a for a in covered if a.account_id not in delegation.scope.accounts)
    return ConsentPlan(
        kind=CONSENT_WIDEN,
        delegation_id=delegation.id,
        mode=delegation.mode,
        scope=Scope(
            accounts=_sorted({*delegation.scope.accounts, *(a.account_id for a in added)}),
            communities=_sorted({*delegation.scope.communities, *communities}),
            actions=_sorted({*delegation.scope.actions, *actions}),
        ),
        budget=delegation.budget,
        expires_at=expires_at,
        replaces=delegation.id,
        accounts=added,
    )


async def requests_waiting_on(
    pool: asyncpg.Pool, *, organization_id: UUID, delegation_id: UUID
) -> int:
    """How many requests still wait for the publisher under this signature.

    THE PUBLISHER STOPS A REQUEST WHOSE SIGNATURE WAS REPLACED ("nowy podpis nie dziedziczy
    starych zgód", `pilot/publisher.py`). Widening a scope closes the old signature, so a request
    queued a minute ago under it would be stopped by the click that widened the scope. The card
    refuses to widen while there is one (`ZAKRES_KOLEJKA`) and says why; the person clicks again
    when the queue is empty.
    """
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM publication_requests WHERE organization_id = $1 "
            "AND delegation_id = $2 AND status IN ('QUEUED', 'PUBLISHING')",
            organization_id,
            delegation_id,
        )
    return int(count or 0)


async def interrupt_presence_sessions(
    pool: asyncpg.Pool, *, organization_id: UUID, project_id: UUID
) -> None:
    """Ask every background session of this project that is still moving to stop.

    A replaced scope is a different permission, so work running under the old one is asked to
    stop (the dashboard's form does the same, `api/delegations.py`). Written through the trace
    because the set is "whatever is running right now" and the turn watches its own trace. A
    session that finished between the read and the write is not an error.
    """
    sessions = SessionStore(pool)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM agent_sessions WHERE organization_id = $1 AND project_id = $2 "
            "AND kind = $3 AND finished_at IS NULL",
            organization_id,
            project_id,
            SESSION_PRESENCE,
        )
    for row in rows:
        try:
            await sessions.append_event(
                organization_id=organization_id,
                session_id=row["id"],
                kind=EVENT_INTERRUPT,
                payload={"detail": "powierzenie wstrzymane albo odwołane"},
            )
        except Exception:
            continue


async def sign_for_card(
    pool: asyncpg.Pool,
    plan: ConsentPlan,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    host: str,
) -> DelegationRow:
    """Sign what `plan` says, through the store the dashboard's form uses, and audit it.

    The caller has ALREADY redeemed the one-time token: this function does not check for a person,
    and must never be reachable without that check in front of it.
    """
    if not plan.signs:
        raise ValueError("plan_consent nie przewiduje podpisu: nie ma czego podpisywać")
    operator_id = await PublicationStore(pool).sole_operator_id(organization_id=organization_id)
    delegation = await PresenceStore(pool).sign(
        organization_id=organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
        mode=plan.mode,
        scope=plan.scope,
        budget=plan.budget,
        operator_id=operator_id,
        replaces=plan.replaces,
        expires_at=plan.expires_at,
    )
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind="PROJECT_DELEGATION_SIGNED",
            organization_id=organization_id,
            workspace_id=workspace_id,
            subject_ref=f"delegation:{delegation.id}",
            actor_ref=f"plugin:{host}",
            payload={
                "delegation_id": str(delegation.id),
                "project_id": str(project_id),
                "mode": delegation.mode,
                "payload_hash": delegation.scope_sha256,
            },
        )
    if plan.replaces is not None:
        await interrupt_presence_sessions(
            pool, organization_id=organization_id, project_id=project_id
        )
    return delegation
