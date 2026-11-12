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
pilot pace: one publication an hour and six a day, ONE shared bucket (a budget with no per-account
limit is read exactly as it always was: `budget.spent` counts the publications of EVERY account of
the scope together, and the queue by project). Covering more accounts raises nobody's pace, and the
worst case (every text from one account) is the one the single-account scope already had. Nothing
here raises them; a person who wants more says so in the card's Limits panel (since v11 a number
PER ACCOUNT, `plan_limits`) or in Obecność, where the number is typed and signed.

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

A PAUSE IS A STOP, AND ONLY ONE CLICK LIFTS IT (v9, 11.10). A SEND click never does: a paused
signature is refused (`ZAKRES_WSTRZYMANY`), and so is signing a fresh scope over it, because that
would turn the stop button into a suggestion. What lifts it is the card's own "Wznow" control, a
dedicated two-step click behind a one-time token (`tools/card_actions.py`: `room.scope_token`, then
`room.resume`), which sets `paused_at` back to NULL on the SAME signature and writes the audit row.
The model has no tool that resumes, signs or pauses; the card's three are app-only. A pause from
the card is the same shape (`plan_pause`), and it also interrupts the background sessions.

THE CARD CAN NOW SET THE LIMITS ITSELF (v9, 11.10, the founder's day: he had to find the Obecnosc
page, revoke, find a hidden manual form, tick five boxes and type three numbers, and the first
attempt saved an EMPTY scope). `plan_limits` builds that signature from what the person ticked
in the card's panel: always REVIEW (every text waits for a click), the accounts they chose out of
the project's assigned ones (the server checks each one against the database and the platform
matrix; the card's ids are inert until then), the actions they chose, three limits inside fixed
bounds and a term of 7 or 14 days. An empty list of accounts or of actions is refused, never
saved: a scope that permits nothing is a signature that looks like a permission. It replaces the
old signature through the same `PresenceStore.sign`, behind the same token, and it refuses while a
request still waits in the publisher's queue, exactly as `CONSENT_WIDEN` does.

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

from .budget import (
    DEFAULT_PUBLICATIONS_PER_DAY,
    DEFAULT_PUBLICATIONS_PER_HOUR,
    effective_limits,
    limits_for,
)
from .scope import (
    ACTION_OWN_POST,
    ACTION_REPLY,
    ACTION_RESEARCH,
    DEFAULT_TERM,
    MODE_REVIEW,
    AccountCaps,
    Budget,
    Scope,
    action_supported,
)
from .store import DelegationRow, PresenceStore

CONSENT_SEND = "WYSLIJ"
CONSENT_SIGN = "PODPISZ"
CONSENT_WIDEN = "ROZSZERZ"
#: The card's own controls over the scope (v9): pause it, resume a paused one, replace it with the
#: limits the person set in the panel. None of them is reachable from a send click.
CONSENT_PAUSE = "WSTRZYMAJ"
CONSENT_RESUME = "WZNOW"
CONSENT_SET = "USTAW"
#: The kinds that write a NEW signature (`PresenceStore.sign`). Pause and resume only move
#: `paused_at`.
SIGNING_KINDS = frozenset({CONSENT_SIGN, CONSENT_WIDEN, CONSENT_SET})

REFUSED_PAUSED = "ZAKRES_WSTRZYMANY"
REFUSED_MODE = "ZAKRES_INNY_TRYB"
REFUSED_QUEUE = "ZAKRES_KOLEJKA"
REFUSED_EMPTY = "ZAKRES_PUSTY"
REFUSED_NO_SCOPE = "BRAK_ZAKRESU"
REFUSED_NOT_ACTIVE = "NIE_AKTYWNY"
REFUSED_NOT_PAUSED = "NIE_WSTRZYMANY"
REFUSED_EXPIRED = "ZAKRES_WYGASL"
REFUSED_ACCOUNT = "KONTO_NIEZNANE"
REFUSED_LIMITS = "LIMIT_POZA_GRANICA"

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
        return self.kind in SIGNING_KINDS

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
        out: dict[str, Any] = {
            "rodzaj": self.kind,
            "tryb": self.mode,
            "wygasa": self.expires_at.isoformat() if self.expires_at else None,
            "limity": {
                "godzina": self.budget.publications_per_hour,
                "doba": self.budget.publications_per_day,
            },
            "konta": [account.to_json() for account in self.accounts],
        }
        rows = account_limit_rows(self.budget, self.accounts)
        if rows:
            out["limity_kont"] = rows
        return out


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


def account_limit_rows(budget: Budget, accounts: Iterable[ConsentAccount]) -> list[dict[str, Any]]:
    """What binds each account, in the card's words, for a budget with limits PER ACCOUNT (v11).

    Empty for a budget with one shared bucket: the card then tells the project's numbers, as it
    always did. `zrodlo` says which level each number came from (`account`, `platform` or
    `project`), so the panel can show what the person did not set.
    """
    limits = effective_limits(budget)
    if not limits.per_account:
        return []
    return [
        {
            "id": str(account.account_id),
            "platforma": account.platform,
            "konto": account.handle,
            **limits_for(limits, account.account_id, account.platform).to_json(),
        }
        for account in accounts
    ]


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
            "Zakres tego projektu jest wstrzymany. Wznów go przyciskiem Wznów w karcie (albo w "
            "Obecności): wstrzymanie to Twój stop i samo wysłanie go nie zdejmuje.",
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
            "Ustaw go w karcie przyciskiem Limity: podpisze zakres, w którym każdy tekst czeka na "
            "Twoje kliknięcie, razem z tym kontem.",
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


#: What the card's panel starts from, and the most it will sign. The product's own defaults
#: (`budget.py`: 1 an hour, 6 a day, 1 own post) are the pilot pace for a NEW account; a person who
#: has used the product for a while wants more, and these bounds are where "more" stops being a
#: decision a click in a chat should make (the dashboard form has no bound, and stays the place for
#: a larger number). Written out, so a later change of the defaults does not move what is offered.
CARD_LIMIT_DEFAULTS = {"godzina": 2, "doba": 10, "wlasne_doba": 1, "dni": 7}
CARD_LIMIT_BOUNDS = {"godzina": 10, "doba": 50, "wlasne_doba": 10}
CARD_TERMS_DAYS = (7, 14)
#: The actions the panel offers, in the order it draws them.
CARD_ACTIONS = (ACTION_REPLY, ACTION_OWN_POST, ACTION_RESEARCH)

_PUBLISHING_ACTIONS = (ACTION_REPLY, ACTION_OWN_POST)


def scope_state(delegation: DelegationRow | None) -> str:
    """What the card says about the scope in one word: AKTYWNY, WSTRZYMANY, WYGASL or BRAK.

    BRAK is "nothing the card can lean on": no signature, a revoked one, or one whose signer is
    gone (the next send re-signs it, `plan_consent`). An expired signature is WYGASL even when it
    was paused: nothing can be resumed on it.
    """
    if delegation is None or not delegation.signer_active:
        return "BRAK"
    if delegation.expired:
        return "WYGASL"
    if delegation.paused_at is not None:
        return "WSTRZYMANY"
    return "AKTYWNY"


def _names(accounts: Iterable[ConsentAccount]) -> list[dict[str, str]]:
    return [account.to_json() for account in accounts]


def plan_pause(
    delegation: DelegationRow | None, *, named: Sequence[ConsentAccount] = ()
) -> ConsentPlan:
    """The pause the card's control asks for. PURE. Needs a live signature: a paused, expired or
    missing one has nothing to stop (`BRAK_ZAKRESU`, `NIE_AKTYWNY`). `named` is as in
    `plan_resume`."""
    if delegation is None or not delegation.signer_active:
        raise CardConsentRefused(
            REFUSED_NO_SCOPE, "Ten projekt nie ma podpisanego zakresu, więc nie ma czego wstrzymać."
        )
    if not delegation.live:
        raise CardConsentRefused(
            REFUSED_NOT_ACTIVE,
            "Zakres tego projektu już jest wstrzymany albo wygasł, więc nie ma czego wstrzymać.",
        )
    return ConsentPlan(
        kind=CONSENT_PAUSE,
        delegation_id=delegation.id,
        mode=delegation.mode,
        scope=delegation.scope,
        budget=delegation.budget,
        expires_at=delegation.expires_at,
        replaces=None,
        accounts=tuple(named),
    )


def plan_resume(
    delegation: DelegationRow | None, *, named: Sequence[ConsentAccount]
) -> ConsentPlan:
    """The resume the card's control asks for. PURE. Only a PAUSED, not expired signature can be
    resumed, and it comes back as it was: same mode, same accounts, same limits, same deadline.

    `named` are the accounts of the scope the SERVER could resolve, for the sentence the person
    reads; the plan carries them and the card only draws them.
    """
    if delegation is None or not delegation.signer_active:
        raise CardConsentRefused(
            REFUSED_NO_SCOPE, "Ten projekt nie ma podpisanego zakresu, więc nie ma czego wznawiać."
        )
    if delegation.expired:
        raise CardConsentRefused(
            REFUSED_EXPIRED,
            "Zakres tego projektu wygasł, a wygasłego nie da się wznowić. Ustaw nowy w karcie "
            "przyciskiem Limity.",
        )
    if delegation.paused_at is None:
        raise CardConsentRefused(
            REFUSED_NOT_PAUSED,
            "Zakres tego projektu nie jest wstrzymany, więc nie ma czego wznawiać.",
        )
    return ConsentPlan(
        kind=CONSENT_RESUME,
        delegation_id=delegation.id,
        mode=delegation.mode,
        scope=delegation.scope,
        budget=delegation.budget,
        expires_at=delegation.expires_at,
        replaces=None,
        accounts=tuple(named),
    )


def plan_limits(
    delegation: DelegationRow | None,
    *,
    assigned: Sequence[ConsentAccount],
    chosen: Iterable[UUID],
    actions: Iterable[str],
    limits: dict[str, int] | None,
    days: int | None,
    now: datetime | None = None,
    account_limits: dict[str, dict[str, int]] | None = None,
) -> ConsentPlan:
    """The signature the card's panel asks for, from what the person ticked. PURE.

    LIMITS PER ACCOUNT (v11): `account_limits` is `{account id: {godzina, doba, wlasne_doba}}`,
    the card's three numbers for each ticked account (replies per hour, replies per day, own posts
    per day; the first two count replies only, own posts are counted apart). Given, the scope is
    signed with a number for EVERY chosen account (one the card left out gets `limits`, else the
    card's defaults) and NO overall ceiling: each account's pace is its own. Not given, the three
    numbers of `limits` are ONE shared bucket, as before. An entry for an account that was not
    chosen is refused: a limit for nobody is a signature that looks like a permission.

    Everything the card sent is checked here against rows the SERVER read: each chosen account must
    be assigned to the project (`assigned`), not stopped, and able to do every publishing action
    that was chosen (the platform matrix); a list that ends up empty is refused (`ZAKRES_PUSTY`),
    as is a limit outside `CARD_LIMIT_BOUNDS` and a term that is not 7 or 14 days. The mode is
    always REVIEW: a click in a chat never hands the agent a scope that posts unseen. The old
    scope's communities are kept (the collector observes them); nothing new is added.

    A scope that is PAUSED and still running is not replaced here: signing a fresh one over it would
    lift the pause (`ZAKRES_WSTRZYMANY`, resume first).
    """
    moment = now or datetime.now(UTC)
    if (
        delegation is not None
        and delegation.signer_active
        and not delegation.expired
        and delegation.paused_at is not None
    ):
        raise CardConsentRefused(
            REFUSED_PAUSED,
            "Zakres tego projektu jest wstrzymany. Najpierw go wznów przyciskiem Wznów, "
            "potem zmieniaj limity: wstrzymanie to Twój stop i nowy podpis go nie zdejmuje.",
        )
    wanted_actions = sorted({str(a).strip().upper() for a in actions})
    unknown = [a for a in wanted_actions if a not in CARD_ACTIONS]
    if unknown:
        raise CardConsentRefused(REFUSED_EMPTY, f"Nieznane działania: {', '.join(unknown)}.")
    chosen_ids = {UUID(str(one)) for one in chosen}
    if not wanted_actions:
        raise CardConsentRefused(
            REFUSED_EMPTY,
            "Zaznacz co najmniej jedno działanie. Zakres bez działań nic nie pozwala robić.",
        )
    if not chosen_ids:
        raise CardConsentRefused(
            REFUSED_EMPTY,
            "Zaznacz co najmniej jedno konto. Zakres bez kont nic nie pozwala robić.",
        )
    by_id = {account.account_id: account for account in assigned}
    needed = [a for a in wanted_actions if a in _PUBLISHING_ACTIONS]
    picked: list[ConsentAccount] = []
    for account_id in sorted(chosen_ids, key=str):
        account = by_id.get(account_id)
        if account is None or account.state in STOPPED_STATES:
            raise CardConsentRefused(
                REFUSED_ACCOUNT,
                "To konto nie należy do projektu albo jest zatrzymane, więc nie wejdzie "
                "do zakresu.",
            )
        support = support_for(account.platform)
        if not all(action_supported(action, support) for action in needed):
            raise CardConsentRefused(
                REFUSED_ACCOUNT,
                f"Konto {account.handle} ({account.platform}) nie umie dziś wykonać wszystkich "
                "zaznaczonych działań. Odznacz je albo to konto.",
            )
        picked.append(account)
    picked.sort(key=lambda a: (a.platform, a.handle, str(a.account_id)))

    asked = dict(limits or {})
    unknown_keys = sorted(set(asked) - set(CARD_LIMIT_BOUNDS))
    if unknown_keys:
        raise CardConsentRefused(REFUSED_LIMITS, f"Nieznane limity: {', '.join(unknown_keys)}.")
    values: dict[str, int] = {}
    for key, bound in CARD_LIMIT_BOUNDS.items():
        raw = asked.get(key, CARD_LIMIT_DEFAULTS[key])
        if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= bound:
            raise CardConsentRefused(
                REFUSED_LIMITS,
                f"Limit „{key}” musi być liczbą od 1 do {bound}. Większy ustawisz w Obecności.",
            )
        values[key] = raw
    if values["godzina"] > values["doba"] or values["wlasne_doba"] > values["doba"]:
        raise CardConsentRefused(
            REFUSED_LIMITS,
            "Limit na godzinę i limit własnych postów nie mogą być większe niż limit na dobę.",
        )
    term = CARD_LIMIT_DEFAULTS["dni"] if days is None else days
    if isinstance(term, bool) or term not in CARD_TERMS_DAYS:
        raise CardConsentRefused(REFUSED_LIMITS, "Termin to 7 albo 14 dni.")
    if account_limits:
        budget = _per_account_budget(account_limits, picked=picked, fallback=values)
    else:
        budget = Budget(
            publications_per_day=values["doba"],
            publications_per_hour=values["godzina"],
            own_posts_per_day=values["wlasne_doba"],
        )

    return ConsentPlan(
        kind=CONSENT_SET,
        delegation_id=delegation.id if delegation is not None else None,
        mode=MODE_REVIEW,
        scope=Scope(
            accounts=_sorted({a.account_id for a in picked}),
            communities=tuple(delegation.scope.communities) if delegation is not None else (),
            actions=_sorted(set(wanted_actions)),
        ),
        budget=budget,
        expires_at=(moment + timedelta(days=term)).replace(microsecond=0),
        replaces=delegation.id if delegation is not None else None,
        accounts=tuple(picked),
    )


def _per_account_budget(
    asked: dict[str, dict[str, int]], *, picked: Sequence[ConsentAccount], fallback: dict[str, int]
) -> Budget:
    """The budget of a scope with a number for every chosen account. PURE; every number is checked
    against `CARD_LIMIT_BOUNDS` exactly as the shared three are."""
    chosen = {account.account_id for account in picked}
    entries: dict[UUID, AccountCaps] = {}
    for raw_id, raw_caps in asked.items():
        try:
            account_id = UUID(str(raw_id))
        except ValueError as exc:
            raise CardConsentRefused(
                REFUSED_LIMITS, "Limity kont: klucz nie jest identyfikatorem konta."
            ) from exc
        if account_id not in chosen:
            raise CardConsentRefused(
                REFUSED_LIMITS,
                "Limit dla konta, którego nie zaznaczono. Zaznacz konto albo usuń jego limit.",
            )
        if not isinstance(raw_caps, dict):
            raise CardConsentRefused(REFUSED_LIMITS, "Limity konta to obiekt z liczbami.")
        unknown = sorted(set(raw_caps) - set(CARD_LIMIT_BOUNDS))
        if unknown:
            raise CardConsentRefused(
                REFUSED_LIMITS, f"Nieznane limity konta: {', '.join(unknown)}."
            )
        entries[account_id] = _caps_of(raw_caps, fallback)
    for account_id in chosen - set(entries):
        entries[account_id] = _caps_of({}, fallback)
    return Budget(account_limits=tuple(sorted(entries.items(), key=lambda pair: str(pair[0]))))


def _caps_of(raw: dict[str, Any], fallback: dict[str, int]) -> AccountCaps:
    values: dict[str, int] = {}
    for key, bound in CARD_LIMIT_BOUNDS.items():
        value = raw.get(key, fallback[key])
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= bound:
            raise CardConsentRefused(
                REFUSED_LIMITS,
                f"Limit „{key}” konta musi być liczbą od 1 do {bound}. "
                "Większy ustawisz w Obecności.",
            )
        values[key] = value
    if values["godzina"] > values["doba"] or values["wlasne_doba"] > values["doba"]:
        raise CardConsentRefused(
            REFUSED_LIMITS,
            "Limit na godzinę i limit własnych postów konta nie mogą być większe niż jego limit "
            "na dobę.",
        )
    return AccountCaps(
        replies_per_hour=values["godzina"],
        replies_per_day=values["doba"],
        own_posts_per_day=values["wlasne_doba"],
    )


async def named_scope_accounts(
    pool: asyncpg.Pool, *, organization_id: UUID, ids: Iterable[UUID]
) -> tuple[ConsentAccount, ...]:
    """The accounts of a signed scope, by name, as far as the database still knows them."""
    wanted = list(ids)
    if not wanted:
        return ()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, platform, external_account_id, state FROM social_accounts "
            "WHERE organization_id = $1 AND id = ANY($2::uuid[]) "
            "ORDER BY platform, external_account_id",
            organization_id,
            wanted,
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


async def scope_summary(
    pool: asyncpg.Pool, *, organization_id: UUID, workspace_id: UUID, project_id: UUID
) -> dict[str, Any]:
    """The scope as the card shows it and its panel starts from (`zakres` of `room.show`).

    Read from the database on every call; nothing here is a decision. `do_wyboru` is the accounts
    the panel may offer (assigned, not stopped) with what the platform matrix lets each do; the
    card sends back ids from it, and `plan_limits` checks every one again.
    """
    delegation = await PresenceStore(pool).delegation_for(
        organization_id=organization_id, project_id=project_id
    )
    assigned = await assigned_accounts(
        pool, organization_id=organization_id, workspace_id=workspace_id, project_id=project_id
    )
    state = scope_state(delegation)
    in_scope = set(delegation.scope.accounts) if delegation is not None else set()
    if delegation is not None and state != "BRAK":
        named = await named_scope_accounts(
            pool, organization_id=organization_id, ids=delegation.scope.accounts
        )
        limits = effective_limits(delegation)
    else:
        named = ()
        limits = effective_limits(Budget())
    choices = []
    for account in assigned:
        if account.state in STOPPED_STATES:
            continue
        support = support_for(account.platform)
        entry = {
            "id": str(account.account_id),
            "platforma": account.platform,
            "konto": account.handle,
            "dzialania": [a for a in _PUBLISHING_ACTIONS if action_supported(a, support)],
            "w_zakresie": state != "BRAK" and account.account_id in in_scope,
        }
        if entry["w_zakresie"]:
            # v11: WHAT BINDS THIS ACCOUNT (its own number, its platform's or the project's), so
            # the panel opens on the real numbers and can set each account's own.
            entry["limity"] = limits_for(limits, account.account_id, account.platform).to_json()
        choices.append(entry)
    return {
        "stan": state,
        "tryb": delegation.mode if delegation is not None and state != "BRAK" else None,
        "wygasa": (
            delegation.expires_at.isoformat()
            if delegation is not None and state != "BRAK" and delegation.expires_at
            else None
        ),
        "konta": _names(named),
        "dzialania": list(delegation.scope.actions)
        if delegation is not None and state != "BRAK"
        else [],
        "limity": {
            "godzina": limits.publications_per_hour,
            "doba": limits.publications_per_day,
            "wlasne_doba": limits.own_posts_per_day,
        },
        # v11: true when the signed limits are per platform / per account (then `limity` above is
        # the project's own number, a fallback, and each account of `do_wyboru` has its own);
        # `razem` is the overall ceiling over all accounts, only when one was signed.
        "wedlug_kont": limits.per_account,
        "razem": {
            "godzina": limits.overall_per_hour() if limits.per_account else None,
            "doba": limits.overall_per_day() if limits.per_account else None,
        },
        "do_wyboru": choices,
        "domyslne": dict(CARD_LIMIT_DEFAULTS),
        "granice": dict(CARD_LIMIT_BOUNDS),
    }


async def pause_for_card(
    pool: asyncpg.Pool,
    plan: ConsentPlan,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    host: str,
) -> DelegationRow:
    """Pause the signature `plan` was drawn against. The caller has ALREADY redeemed the token."""
    if plan.kind != CONSENT_PAUSE or plan.delegation_id is None:
        raise ValueError("to nie jest plan wstrzymania")
    delegation = await PresenceStore(pool).set_paused(
        plan.delegation_id, organization_id=organization_id, paused=True, at=datetime.now(UTC)
    )
    await _audit_pause(
        pool,
        delegation,
        kind="PROJECT_DELEGATION_PAUSED",
        organization_id=organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
        host=host,
    )
    # A pause that left a turn running would be a button that does not stop anything.
    await interrupt_presence_sessions(pool, organization_id=organization_id, project_id=project_id)
    return delegation


async def resume_for_card(
    pool: asyncpg.Pool,
    plan: ConsentPlan,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    host: str,
) -> DelegationRow:
    """Lift the pause of the signature `plan` was drawn against. Token already redeemed."""
    if plan.kind != CONSENT_RESUME or plan.delegation_id is None:
        raise ValueError("to nie jest plan wznowienia")
    delegation = await PresenceStore(pool).set_paused(
        plan.delegation_id, organization_id=organization_id, paused=False, at=datetime.now(UTC)
    )
    await _audit_pause(
        pool,
        delegation,
        kind="PROJECT_DELEGATION_RESUMED",
        organization_id=organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
        host=host,
    )
    return delegation


async def _audit_pause(
    pool: asyncpg.Pool,
    delegation: DelegationRow,
    *,
    kind: str,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    host: str,
) -> None:
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind=kind,
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
