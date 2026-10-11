"""What a person signed when they delegated a project, and what it does not cover (P4).

THE SCOPE IS THE WHOLE POINT OF A DELEGATION. In AUTONOMOUS mode the system stops asking for
consent to each text, so the question "was this allowed" has to be answerable from the signature
alone, months later. That means three things about this module:

  1. **The scope is a closed shape, not a free-form object.** Accounts, communities and actions,
     each a list, each checked. A field nobody parses is a field nobody honours, and a scope with
     a typo in a key would silently widen to "everything" or narrow to "nothing" depending on how
     the reader was written.
  2. **An EMPTY list means nothing, never everything.** A delegation naming no account permits no
     publication. The opposite default is the one that publishes from an identity nobody chose,
     and it is the default a careless reader arrives at by accident.
  3. **The digest covers the scope AND the budget together.** They are signed as one thing: a
     scope with a bigger budget is a different permission, and a digest over the scope alone
     would let the limits change under a signature that still looks valid.

WHAT IS NOT HERE: the check whether a delegation is live (that is `store.py`, which knows about
`revoked_at` and `paused_at`) and the budget arithmetic (that is `budget.py`, which counts what
actually happened). This module answers one question: is THIS action, on THIS account, in THIS
community, inside what was signed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

#: What a delegation may permit. `REPLY` is a comment under somebody else's thread, `OWN_POST` a
#: thread of our own, `RESEARCH` reading and remembering without writing anywhere.
ACTION_REPLY = "REPLY"
ACTION_OWN_POST = "OWN_POST"
ACTION_RESEARCH = "RESEARCH"
ACTIONS = (ACTION_REPLY, ACTION_OWN_POST, ACTION_RESEARCH)

MODE_AUTONOMOUS = "AUTONOMOUS"
MODE_REVIEW = "REVIEW"
MODE_RESEARCH = "RESEARCH"
MODES = (MODE_AUTONOMOUS, MODE_REVIEW, MODE_RESEARCH)

#: Budget keys a delegation may carry. A key outside this list is a refusal rather than a limit
#: nobody enforces: "publications_per_week" silently ignored reads exactly like "no limit".
BUDGET_KEYS = (
    "model_turns_per_day",
    "publications_per_day",
    "publications_per_hour",
    # P4 etap C: własne posty mają OSOBNY limit i to nie jest przesada. Odpowiedź pod cudzym
    # wątkiem wchodzi w rozmowę, która już trwa; własny post zabiera uwagę całej społeczności i
    # jest tym, za co moderacja banuje najszybciej. Wspólny limit z odpowiedziami znaczyłby, że
    # agent może wydać całą dobę na sześć własnych postów, czego nikt nie miał na myśli.
    "own_posts_per_day",
    # Ile powrotów naraz może sobie zaplanować (P4 etap C).
    "scheduled_returns",
)


#: Deadline of a signature that named none, and the bounds of one that did. A week is the length
#: of the founder's pilots; ninety days is where "until further notice" begins, and that is a
#: decision the product does not make by default. Here and not in `api/delegations.py` because the
#: card in the person's agent signs a scope too (`presence/card_consent.py`) and must use the
#: SAME default as the form does, from the one definition.
DEFAULT_TERM = timedelta(days=7)
MIN_TERM = timedelta(minutes=5)
MAX_TERM = timedelta(days=90)

#: Sentinel for "the caller is not asking about the platform". See `covers`.
_UNCHECKED = object()


class ScopeError(Exception):
    """The scope or the budget cannot be read as signed. Nothing is delegated."""


@dataclass(frozen=True)
class Scope:
    """Accounts, communities and actions a delegation covers."""

    accounts: tuple[UUID, ...]
    communities: tuple[str, ...]
    actions: tuple[str, ...]

    def covers_account(self, account_id: UUID) -> bool:
        return account_id in self.accounts

    def covers_community(self, community_key: str) -> bool:
        return community_key in self.communities

    def covers_action(self, action: str) -> bool:
        return action in self.actions

    def to_json(self) -> dict[str, Any]:
        return {
            "accounts": [str(a) for a in self.accounts],
            "communities": list(self.communities),
            "actions": list(self.actions),
        }


@dataclass(frozen=True)
class Budget:
    """How much a delegation permits per window. Absent key means NO limit of that kind."""

    model_turns_per_day: int | None = None
    publications_per_day: int | None = None
    publications_per_hour: int | None = None
    own_posts_per_day: int | None = None
    scheduled_returns: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in (
                ("model_turns_per_day", self.model_turns_per_day),
                ("publications_per_day", self.publications_per_day),
                ("publications_per_hour", self.publications_per_hour),
                ("own_posts_per_day", self.own_posts_per_day),
                ("scheduled_returns", self.scheduled_returns),
            )
            if value is not None
        }


def parse_scope(payload: Any) -> Scope:
    """A scope from an already-parsed object. Every list is required, and may be empty.

    Required and possibly empty rather than optional: an absent `accounts` key and an empty one
    are the same permission (none), but only one of them is a scope somebody wrote on purpose.
    Requiring the key means a caller cannot forget a dimension and get a default.
    """
    if not isinstance(payload, dict):
        raise ScopeError("zakres delegacji nie jest obiektem")
    missing = [key for key in ("accounts", "communities", "actions") if key not in payload]
    if missing:
        raise ScopeError(
            f"zakres delegacji nie ma pól: {', '.join(missing)}. Brak pola i pusta lista znaczą "
            "to samo pozwolenie, ale tylko jedno z nich ktoś napisał świadomie"
        )
    for key in ("accounts", "communities", "actions"):
        if not isinstance(payload[key], list):
            raise ScopeError(f"zakres delegacji: `{key}` musi być listą")

    accounts: list[UUID] = []
    for raw in payload["accounts"]:
        try:
            accounts.append(UUID(str(raw)))
        except ValueError as exc:
            raise ScopeError(f"zakres delegacji: {raw!r} nie jest identyfikatorem konta") from exc

    communities = [str(value).strip() for value in payload["communities"]]
    if any(not value for value in communities):
        raise ScopeError("zakres delegacji: pusty klucz społeczności")

    actions = [str(value).strip().upper() for value in payload["actions"]]
    unknown = [action for action in actions if action not in ACTIONS]
    if unknown:
        raise ScopeError(
            f"zakres delegacji: nieznane działania {unknown}. Dozwolone: {', '.join(ACTIONS)}"
        )

    # Duplicates are not an error and not kept: a scope listing one account twice permits exactly
    # what it permits once, and the digest must not depend on how many times it was written.
    return Scope(
        accounts=tuple(sorted(set(accounts), key=str)),
        communities=tuple(sorted(set(communities))),
        actions=tuple(sorted(set(actions))),
    )


def parse_budget(payload: Any) -> Budget:
    """A budget from an already-parsed object. Unknown keys are a refusal."""
    if payload is None:
        return Budget()
    if not isinstance(payload, dict):
        raise ScopeError("budżet delegacji nie jest obiektem")
    unknown = sorted(set(payload) - set(BUDGET_KEYS))
    if unknown:
        raise ScopeError(
            f"budżet delegacji: nieznane klucze {unknown}. Limit, którego nikt nie egzekwuje, "
            f"czyta się dokładnie jak brak limitu. Dozwolone: {', '.join(BUDGET_KEYS)}"
        )
    values: dict[str, int] = {}
    for key in BUDGET_KEYS:
        if key not in payload:
            continue
        raw = payload[key]
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ScopeError(f"budżet delegacji: `{key}` musi być liczbą całkowitą")
        if raw < 0:
            raise ScopeError(f"budżet delegacji: `{key}` nie może być ujemne")
        values[key] = raw
    return Budget(**values)


def digest(*, mode: str, scope: Scope, budget: Budget, expires_at: datetime | None = None) -> str:
    """The sha256 a decision cites as the thing that was signed.

    Over the mode, the scope AND the budget, canonically ordered. The three are one permission:
    the same accounts with a higher limit is a different thing to have agreed to, and a digest
    over the scope alone would let the limits move under a signature that still looks valid.

    SINCE 0033 THE DEADLINE IS PART OF IT. The same scope for a week and for a year are two
    different permissions. The key is added ONLY when there is a deadline, so every signature made
    before 0033 keeps the digest it was signed with: rewriting what somebody signed is not a
    migration's call to make.
    """
    if mode not in MODES:
        raise ScopeError(f"tryb {mode!r} poza listą {MODES}")
    signed: dict[str, Any] = {"mode": mode, "scope": scope.to_json(), "budget": budget.to_json()}
    if expires_at is not None:
        if expires_at.tzinfo is None:
            raise ScopeError("termin delegacji musi mieć strefę czasową")
        # Seconds, UTC: a deadline read back from PostgreSQL has microseconds and a caller's may
        # not, and the digest must not depend on which of the two did the arithmetic.
        signed["expires_at"] = (
            expires_at.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
    canonical = json.dumps(
        signed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: What each scope action needs the PLATFORM to be able to do, by the flag's name in
#: `workspace/platforms.py`. RESEARCH needs nothing from the platform beyond being readable at
#: all, and it is deliberately absent here: reading and remembering is something the agent does
#: with materials, not something an adapter has to support.
CAPABILITY_OF_ACTION = {
    ACTION_REPLY: "comment",
    ACTION_OWN_POST: "own_post",
}


def action_supported(action: str, support: Any) -> bool:
    """Can this platform do this action AT ALL, today, in this repository.

    ONE RULE, READ IN THREE PLACES: the delegation panel (to grey out what cannot work), the
    delegated decision (to refuse a proposal on an account that cannot publish) and the tool
    catalog (to mark a tool unavailable). Three copies of `if platform == "X"` would disagree
    within a week, and the disagreement would show up as a screen offering work no adapter does.

    `support` is a `workspace.platforms.PlatformSupport`, passed in rather than looked up here:
    that module belongs to P1 and this one must not start owning a second capability matrix.
    `None` means a platform this product does not know, and nothing is supported on it.
    """
    if support is None:
        return False
    flag = CAPABILITY_OF_ACTION.get(action)
    if flag is None:
        return action in ACTIONS  # RESEARCH: no adapter has to support it
    return bool(getattr(support, flag, False))


@dataclass(frozen=True)
class Verdict:
    """Whether an action is covered, and in one sentence why not."""

    covered: bool
    reason: str = ""


def covers(
    *,
    mode: str,
    scope: Scope,
    action: str,
    account_id: UUID,
    community_key: str | None,
    support: Any = _UNCHECKED,
) -> Verdict:
    """Is this action, on this account, in this community, inside what was signed, and possible.

    RESEARCH mode covers no writing action at all, whatever the scope lists. The mode is the
    coarser statement and it wins: a person who set a project to RESEARCH and left `REPLY` in
    the scope meant the mode, and the other reading publishes something nobody expected.

    `support` is the account's PLATFORM capabilities (P6). A scope may name an X account for
    REPLY, and a person may sign it in good faith; no adapter in this repository can carry it
    out, and a signature is not an implementation. The refusal says which platform and which
    action, because "nie wolno" and "nie ma jak" are different problems with different fixes.

    It defaults to UNCHECKED rather than to `None`, and the difference matters: `None` is a
    platform this product does not know and means "nothing is supported", while UNCHECKED means
    the caller is asking only about the SIGNATURE. A default of `None` would silently refuse
    every caller that has not been taught to pass it.
    """
    if support is not _UNCHECKED and not action_supported(action, support):
        platform = getattr(support, "label", None) or getattr(support, "platform", "ta platforma")
        return Verdict(
            False,
            f"{platform}: ten produkt nie umie dziś wykonać działania {action} na tej platformie",
        )
    if mode not in MODES:
        return Verdict(False, f"tryb {mode!r} jest nieznany")
    if action not in ACTIONS:
        return Verdict(False, f"działanie {action!r} jest nieznane")
    if mode == MODE_RESEARCH and action in (ACTION_REPLY, ACTION_OWN_POST):
        return Verdict(
            False,
            "tryb RESEARCH nie publikuje niczego, niezależnie od tego, co wymienia zakres",
        )
    if not scope.covers_action(action):
        listed = ", ".join(scope.actions) or "nic"
        return Verdict(False, f"działanie {action} nie jest w zakresie (podpisano: {listed})")
    if not scope.covers_account(account_id):
        return Verdict(False, f"konto {account_id} nie jest w zakresie delegacji")
    if community_key is not None and not scope.covers_community(community_key):
        return Verdict(False, f"społeczność {community_key} nie jest w zakresie delegacji")
    return Verdict(True)
