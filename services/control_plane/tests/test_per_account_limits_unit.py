"""Limits per platform and per account, not one bucket for the project (v11, 13.10). PURE.

THE FOUNDER (13.10), after a day of real use: X replies at six an hour waited behind Reddit's one
an hour, and Reddit's slow pace was spent by whoever got there first. A signed budget can now carry
a pace for each account and each platform. Held here, from the outside in:

  * THE SCHEMA, signed and BACKWARD COMPATIBLE: every budget written before v11 keeps the exact
    digest it was signed with (the new keys are omitted when empty), the new keys round-trip through
    `parse_budget`, are order and case independent, and a limit nobody enforces (an unknown name, a
    negative or a non-integer number) is a refusal;
  * THE READING, `limits_for`: account entry, then platform entry, then the project's own number;
    replies and own posts apart; with nothing per account signed the answer is the project's totals
    and says so (`shared`); the old totals become an optional OVERALL ceiling that binds only when
    signed; an account nobody gave a number gets the product's default pace, never "unlimited";
  * THE COUNTING AND THE GATE, `Spent`: the account's own replies, its own posts, and the ceiling;
    Reddit's full hour does not stop X; the message names the account;
  * THE SCHEDULE of "Send all": each account paced by its own hourly limit, no account slowed by
    another, the ceiling and the own-post cap respected, whatever is committed on an account pushes
    only that account, and (random worlds) no rule is ever broken;
  * THE PUBLISHER'S PACE over real attempts, per account;
  * THE CARD'S SIDE: `plan_limits` signs a number for every chosen account inside the same bounds as
    before, refuses a limit for nobody, and tells each account's pace.
"""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from control_plane.presence import batch_plan as bp
from control_plane.presence import budget as bg
from control_plane.presence import card_consent as cc
from control_plane.presence import scope as sc
from control_plane.presence.batch_plan import Candidate
from control_plane.presence.scope import AccountCaps, Budget, ScopeError, parse_budget

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
DAY = timedelta(hours=24)
MIN = timedelta(minutes=1)

X_ACC = uuid.UUID(int=0xA1)
R_ACC = uuid.UUID(int=0xA2)
THIRD = uuid.UUID(int=0xA3)


def _per_account() -> Budget:
    """The founder's example: X 6 an hour and 30 a day plus 2 own posts, Reddit lower."""
    return Budget(
        account_limits=(
            (X_ACC, AccountCaps(replies_per_hour=6, replies_per_day=30, own_posts_per_day=2)),
            (R_ACC, AccountCaps(replies_per_hour=2, replies_per_day=10, own_posts_per_day=1)),
        )
    )


# ---------------------------------------------------------------------------
# The schema: signed, and backward compatible
# ---------------------------------------------------------------------------


def _canonical_digest(mode: str, scope: dict, budget: dict, expires: str | None = None) -> str:
    signed = {"mode": mode, "scope": scope, "budget": budget}
    if expires:
        signed["expires_at"] = expires
    return hashlib.sha256(
        json.dumps(signed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_the_digest_of_every_budget_signed_before_v11_is_the_digest_it_was_signed_with() -> None:
    scope = sc.Scope(accounts=(X_ACC,), communities=("r/saas",), actions=("REPLY",))
    for budget, plain in (
        (Budget(), {}),
        (
            Budget(publications_per_hour=1, publications_per_day=6),
            {"publications_per_hour": 1, "publications_per_day": 6},
        ),
        (
            Budget(
                model_turns_per_day=8,
                publications_per_day=6,
                publications_per_hour=1,
                own_posts_per_day=1,
                scheduled_returns=3,
            ),
            {
                "model_turns_per_day": 8,
                "publications_per_day": 6,
                "publications_per_hour": 1,
                "own_posts_per_day": 1,
                "scheduled_returns": 3,
            },
        ),
    ):
        assert budget.to_json() == plain, "no new key appears in an old budget"
        expires = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
        assert sc.digest(mode="REVIEW", scope=scope, budget=budget, expires_at=expires) == (
            _canonical_digest("REVIEW", scope.to_json(), plain, "2026-10-20T12:00:00Z")
        )


def test_a_signed_pace_per_account_is_part_of_the_digest_and_changes_it() -> None:
    scope = sc.Scope(accounts=(X_ACC, R_ACC), communities=(), actions=("REPLY",))
    old = Budget(publications_per_hour=1, publications_per_day=6)
    new = Budget(
        publications_per_hour=1,
        publications_per_day=6,
        account_limits=((X_ACC, AccountCaps(replies_per_hour=6)),),
    )
    other = Budget(
        publications_per_hour=1,
        publications_per_day=6,
        account_limits=((X_ACC, AccountCaps(replies_per_hour=7)),),
    )
    digests = {sc.digest(mode="REVIEW", scope=scope, budget=b) for b in (old, new, other)}
    assert len(digests) == 3, "the same accounts with a different pace is a different permission"


def test_the_new_keys_round_trip_and_do_not_depend_on_order_or_case() -> None:
    raw = {
        "publications_per_day": 40,
        "platform_limits": {
            "reddit": {"replies_per_hour": 2},
            "x": {"replies_per_hour": 6, "replies_per_day": 30, "own_posts_per_day": 2},
        },
        "account_limits": {
            str(R_ACC): {"replies_per_day": 8},
            str(X_ACC).upper(): {"replies_per_hour": 4},
        },
    }
    budget = parse_budget(raw)
    assert [name for name, _ in budget.platform_limits] == ["REDDIT", "X"], "normalised, sorted"
    assert budget.per_account is True
    again = parse_budget(budget.to_json())
    assert again == budget and again.to_json() == budget.to_json()
    flipped = dict(reversed(list(raw.items())))
    flipped["platform_limits"] = dict(reversed(list(raw["platform_limits"].items())))
    assert parse_budget(flipped) == budget
    scope = sc.Scope(accounts=(X_ACC, R_ACC), communities=(), actions=("REPLY",))
    assert sc.digest(mode="REVIEW", scope=scope, budget=budget) == sc.digest(
        mode="REVIEW", scope=scope, budget=again
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"platform_limits": {"X": {"replies_per_week": 3}}},
        {"platform_limits": {"X": {"replies_per_hour": -1}}},
        {"platform_limits": {"X": {"replies_per_hour": "6"}}},
        {"platform_limits": {"X": {"replies_per_hour": True}}},
        {"platform_limits": {"X": 6}},
        {"platform_limits": [["X", {}]]},
        {"platform_limits": {"": {"replies_per_hour": 1}}},
        {"platform_limits": {"x": {"replies_per_hour": 1}, "X": {"replies_per_day": 2}}},
        {"account_limits": {"not-a-uuid": {"replies_per_hour": 1}}},
        {"account_limits": {str(X_ACC): {"nope": 1}}},
        {"account_limits": "x"},
    ],
)
def test_a_limit_nobody_enforces_is_a_refusal(payload: dict) -> None:
    with pytest.raises(ScopeError):
        parse_budget(payload)


def test_an_entry_that_names_no_limit_is_dropped_not_signed() -> None:
    budget = parse_budget({"account_limits": {str(X_ACC): {}}, "platform_limits": {"X": {}}})
    assert budget == Budget() and budget.to_json() == {}


# ---------------------------------------------------------------------------
# The reading: account, then platform, then the project
# ---------------------------------------------------------------------------


def test_with_no_pace_per_account_the_project_totals_are_one_shared_bucket() -> None:
    limits = bg.effective_limits(Budget(publications_per_hour=4, publications_per_day=20))
    assert limits.per_account is False
    mine = bg.limits_for(limits, X_ACC, "X")
    assert (mine.replies_per_hour, mine.replies_per_day, mine.own_posts_per_day) == (4, 20, 1)
    assert mine.shared is True
    assert limits.overall_per_hour() == 4 and limits.overall_per_day() == 20
    assert set(limits.to_json()) == {
        "publikacje_godzina",
        "publikacje_doba",
        "wlasne_posty_doba",
        "tury_modelu_doba",
        "domyslne",
    }, "the JSON older readers know is unchanged"


def test_the_account_beats_the_platform_beats_the_project() -> None:
    budget = Budget(
        publications_per_hour=3,
        publications_per_day=12,
        own_posts_per_day=2,
        platform_limits=(
            ("X", AccountCaps(replies_per_hour=6, replies_per_day=30)),
            ("REDDIT", AccountCaps(replies_per_hour=2)),
        ),
        account_limits=((X_ACC, AccountCaps(replies_per_hour=9)),),
    )
    limits = bg.effective_limits(budget)
    x = bg.limits_for(limits, X_ACC, "X")
    assert (x.replies_per_hour, x.replies_per_day, x.own_posts_per_day) == (9, 30, 2)
    assert dict(x.sources) == {"godzina": "account", "doba": "platform", "wlasne_doba": "project"}
    other_x = bg.limits_for(limits, THIRD, "x")
    assert (other_x.replies_per_hour, other_x.replies_per_day) == (6, 30), (
        "the platform's, any case"
    )
    reddit = bg.limits_for(limits, R_ACC, "REDDIT")
    assert (reddit.replies_per_hour, reddit.replies_per_day) == (2, 12), "day falls to the project"
    assert bg.limits_for(limits, THIRD, "LINKEDIN").replies_per_hour == 3, "an unknown platform"
    assert x.shared is False


def test_an_account_nobody_gave_a_number_gets_the_default_pace_never_unlimited() -> None:
    limits = bg.effective_limits(_per_account())
    third = bg.limits_for(limits, THIRD, "X")
    assert (third.replies_per_hour, third.replies_per_day, third.own_posts_per_day) == (
        bg.DEFAULT_PUBLICATIONS_PER_HOUR,
        bg.DEFAULT_PUBLICATIONS_PER_DAY,
        bg.DEFAULT_OWN_POSTS_PER_DAY,
    )
    assert dict(third.sources) == {
        "godzina": "project",
        "doba": "project",
        "wlasne_doba": "project",
    }


def test_the_old_totals_are_an_overall_ceiling_only_when_they_were_signed() -> None:
    unsigned = bg.effective_limits(_per_account())
    assert unsigned.overall_per_hour() is None and unsigned.overall_per_day() is None, (
        "an unsigned total is not a hidden cap on a scope that spelled out its numbers"
    )
    signed = bg.effective_limits(
        Budget(publications_per_day=25, account_limits=_per_account().account_limits)
    )
    assert signed.overall_per_day() == 25 and signed.overall_per_hour() is None


def test_limits_per_account_are_told_in_json_with_the_cards_words() -> None:
    out = bg.effective_limits(_per_account()).to_json()
    assert out["wedlug_kont"] is True
    assert out["konta"][str(X_ACC)] == {"godzina": 6, "doba": 30, "wlasne_doba": 2}
    assert out["platformy"] == {}


# ---------------------------------------------------------------------------
# The caps and the ledgers
# ---------------------------------------------------------------------------


def test_the_rules_of_a_publication_are_its_accounts_own_plus_the_ceiling() -> None:
    legacy = bg.caps_for(
        Budget(publications_per_hour=4, publications_per_day=20),
        account_id=X_ACC,
        platform="X",
        own_post=False,
    )
    assert [(c.ledger, c.per_hour, c.per_day) for c in legacy] == [(bg.LEDGER_ALL, 4, 20)]
    legacy_own = bg.caps_for(Budget(), account_id=X_ACC, platform="X", own_post=True)
    assert [c.ledger for c in legacy_own] == [bg.LEDGER_ALL, bg.LEDGER_OWN]

    caps = bg.caps_for(
        _per_account(), account_id=X_ACC, platform="X", own_post=False, label="X @jack"
    )
    assert [(c.ledger, c.per_hour, c.per_day) for c in caps] == [(bg.reply_ledger(X_ACC), 6, 30)]
    assert caps[0].what == "X @jack: odpowiedzi"
    own = bg.caps_for(_per_account(), account_id=R_ACC, platform="REDDIT", own_post=True)
    assert [(c.ledger, c.per_hour, c.per_day) for c in own] == [(bg.own_ledger(R_ACC), None, 1)]
    ceiling = Budget(publications_per_day=25, account_limits=_per_account().account_limits)
    both = bg.caps_for(ceiling, account_id=X_ACC, platform="X", own_post=False)
    assert [c.ledger for c in both] == [bg.LEDGER_ALL, bg.reply_ledger(X_ACC)]


def test_a_publication_is_written_into_the_ledgers_it_counts_in() -> None:
    assert bg.member_ledgers(X_ACC, own_post=False) == (bg.LEDGER_ALL, bg.reply_ledger(X_ACC))
    assert bg.member_ledgers(X_ACC, own_post=True) == (
        bg.LEDGER_ALL,
        bg.LEDGER_OWN,
        bg.own_ledger(X_ACC),
    )


# ---------------------------------------------------------------------------
# The gate: Spent
# ---------------------------------------------------------------------------


def _spent(budget: Budget, **per_account) -> bg.Spent:
    """A `Spent` for X (replies_this_hour, replies_today, own_today) and Reddit, as given."""
    limits = bg.effective_limits(budget)
    x = per_account.get("x", (0, 0, 0))
    r = per_account.get("r", (0, 0, 0))
    accounts = tuple(
        bg.AccountSpent(
            account_id=acc,
            platform=platform,
            handle=handle,
            limits=bg.limits_for(limits, acc, platform),
            replies_this_hour=hour,
            replies_today=day,
            own_posts_today=own,
            hour_frees_at=NOW + 20 * MIN,
            day_frees_at=NOW + 5 * HOUR,
            own_frees_at=NOW + 7 * HOUR,
        )
        for acc, platform, handle, (hour, day, own) in (
            (X_ACC, "X", "demo_founder", x),
            (R_ACC, "REDDIT", "Demo_Maintainer", r),
        )
    )
    return bg.Spent(
        model_turns_today=0,
        publications_today=per_account.get("all_day", 0),
        publications_this_hour=per_account.get("all_hour", 0),
        own_posts_today=0,
        limits=limits,
        accounts=accounts if limits.per_account else (),
    )


def test_a_full_reddit_hour_does_not_stop_x() -> None:
    spent = _spent(_per_account(), x=(1, 5, 0), r=(2, 3, 0))
    assert spent.publication_blocked(X_ACC) is None
    reddit = spent.publication_blocked(R_ACC)
    assert reddit is not None and "Reddit u/Demo_Maintainer" in reddit
    assert (
        "odpowiedzi 2 z 2 w ostatniej godzinie" in reddit
        and "następne miejsce o 12:20 UTC" in reddit
    )


def test_the_message_names_the_account_and_the_window_that_is_full() -> None:
    day = _spent(_per_account(), x=(0, 30, 0)).publication_blocked(X_ACC)
    assert day is not None and day.startswith("X @demo_founder: odpowiedzi 30 z 30 w ostatniej dobie")
    hour = _spent(_per_account(), x=(6, 6, 0)).publication_blocked(X_ACC)
    assert hour is not None and "odpowiedzi 6 z 6 w ostatniej godzinie" in hour


def test_replies_and_own_posts_are_asked_apart() -> None:
    spent = _spent(_per_account(), x=(6, 30, 0))
    assert spent.own_post_blocked(X_ACC) is None, "a day of replies does not eat the own post"
    posts = _spent(_per_account(), x=(0, 0, 2))
    assert posts.publication_blocked(X_ACC) is None, "nor the other way round"
    blocked = posts.own_post_blocked(X_ACC)
    assert blocked is not None and "własne posty 2 z 2" in blocked


def test_an_account_outside_the_scope_has_no_pace_and_that_is_a_refusal_not_unlimited() -> None:
    spent = _spent(_per_account(), x=(0, 0, 0))
    stranger = uuid.UUID(int=0xBEEF)
    for ask in (spent.publication_blocked, spent.own_post_blocked):
        message = ask(stranger)
        assert message is not None and "nie ma dla niego limitu" in message
    assert spent.publication_blocked(None) is None, "the overall question needs no account"


def test_the_overall_ceiling_binds_every_account_together_only_when_signed() -> None:
    ceiling = Budget(publications_per_day=8, account_limits=_per_account().account_limits)
    spent = _spent(ceiling, x=(0, 4, 0), r=(0, 4, 0), all_day=8)
    for account in (X_ACC, R_ACC):
        message = spent.publication_blocked(account)
        assert message is not None and "publikacje: 8 z 8 w ostatniej dobie" in message
    unsigned = _spent(_per_account(), x=(0, 4, 0), all_day=999)
    assert unsigned.publication_blocked(X_ACC) is None


def test_one_shared_bucket_is_asked_exactly_as_it_always_was() -> None:
    legacy = Budget(publications_per_hour=1, publications_per_day=6)
    spent = bg.Spent(
        model_turns_today=0,
        publications_today=2,
        publications_this_hour=1,
        own_posts_today=1,
        limits=bg.effective_limits(legacy),
        hour_frees_at=NOW + 10 * MIN,
    )
    message = spent.publication_blocked(X_ACC)
    assert message == "publikacje: 1 z 1 w ostatniej godzinie, następne miejsce o 12:10 UTC"
    assert spent.own_post_blocked(X_ACC) == "własne posty: 1 z 1 w ostatniej dobie"
    assert spent.publication_blocked() == message, "the account changes nothing with one bucket"


def test_the_agents_mandate_is_told_account_by_account() -> None:
    spent = _spent(_per_account(), x=(1, 5, 0), r=(2, 3, 1))
    text = spent.accounts_sentence()
    assert "X @demo_founder: odpowiedzi 1 z 6 w godzinie i 5 z 30 w dobie, własne posty 0 z 2" in text
    assert "Reddit u/Demo_Maintainer: odpowiedzi 2 z 2 w godzinie i 3 z 10 w dobie" in text
    blocked = spent.accounts_blocked()
    assert "Reddit u/Demo_Maintainer" in blocked and "X @demo_founder" not in blocked
    assert "accounts" not in spent.to_json() and len(spent.to_json()["konta"]) == 2


def test_the_publishers_pace_counts_real_attempts_of_the_account_alone() -> None:
    caps = bg.caps_for(
        _per_account(), account_id=X_ACC, platform="X", own_post=False, label="X @jack"
    )
    reddit_busy = {bg.LEDGER_ALL: [NOW - MIN] * 5, bg.reply_ledger(R_ACC): [NOW - MIN] * 5}
    assert bg.pace_message(caps, reddit_busy, NOW) is None, "Reddit's attempts do not hold X"
    x_busy = {bg.reply_ledger(X_ACC): [NOW - 10 * MIN * i for i in range(6)]}
    message = bg.pace_message(caps, x_busy, NOW)
    assert message == "tempo: X @jack: odpowiedzi: 6/6 w ostatniej godzinie"
    day = {bg.reply_ledger(X_ACC): [NOW - 2 * HOUR - i * MIN for i in range(30)]}
    assert bg.pace_message(caps, day, NOW) == "tempo: X @jack: odpowiedzi: 30/30 w ostatniej dobie"


# ---------------------------------------------------------------------------
# The schedule of "Send all"
# ---------------------------------------------------------------------------


def _cand(
    n: int,
    account: uuid.UUID,
    platform: str,
    budget: Budget,
    *,
    own: bool = False,
    age: timedelta | None = timedelta(hours=2),
) -> Candidate:
    return Candidate(
        revision_id=uuid.UUID(int=n),
        platform=platform,
        own_post=own,
        thread_at=NOW - age if age is not None else None,
        expires_at=NOW + timedelta(hours=48),
        account_id=account,
        caps=bg.caps_for(budget, account_id=account, platform=platform, own_post=own),
    )


class _Floor(random.Random):
    def uniform(self, a: float, b: float) -> float:
        return a


def _plan(cands, budget: Budget, ledger=None, rng=None) -> bp.Schedule:
    limits = bg.effective_limits(budget)
    return bp.plan_with_caps(
        cands,
        now=NOW,
        ledger=ledger or {},
        rng=rng or random.Random(3),
        per_hour=limits.publications_per_hour,
        per_day=limits.publications_per_day,
        own_per_day=limits.own_posts_per_day,
    )


def _of(schedule: bp.Schedule, account: uuid.UUID) -> list[datetime]:
    return sorted(p.at for p in schedule.placed if p.account_id == account)


def test_each_account_is_paced_by_its_own_hourly_limit() -> None:
    budget = _per_account()
    cands = [_cand(i, X_ACC, "X", budget, age=timedelta(minutes=30 + i)) for i in range(1, 7)]
    cands += [
        _cand(10 + i, R_ACC, "REDDIT", budget, age=timedelta(hours=3 + i)) for i in range(1, 4)
    ]
    schedule = _plan(cands, budget, rng=_Floor())
    x, r = _of(schedule, X_ACC), _of(schedule, R_ACC)
    assert len(x) == 6 and len(r) == 2 + 1, "all placed: the day limits are 30 and 10"
    for earlier, later in pairwise(x):
        assert later - earlier >= 10 * MIN, "X: an hour over six"
    for earlier, later in pairwise(r):
        assert later - earlier >= 30 * MIN, "Reddit: an hour over two"
    assert x[0] - NOW < 1 * MIN and r[0] - NOW < 1 * MIN, "each account starts on the click"
    assert x[-1] < NOW + 80 * MIN, "X is not slowed by Reddit's slow pace"
    assert dict(schedule.gaps) == {X_ACC: 10 * MIN, R_ACC: 30 * MIN}


def test_the_card_is_told_the_gap_of_each_account_when_they_differ() -> None:
    budget = _per_account()
    cands = [_cand(i, X_ACC, "X", budget) for i in range(1, 4)]
    cands += [_cand(10 + i, R_ACC, "REDDIT", budget) for i in range(1, 3)]
    out = _plan(cands, budget, rng=_Floor()).to_json(now=NOW)
    assert out["odstep_min"] == 10, "the sentence tells the account with the most replies"
    assert {row["konto_id"]: row["odstep_min"] for row in out["konta"]} == {
        str(X_ACC): 10,
        str(R_ACC): 30,
    }
    same = Budget(
        account_limits=(
            (X_ACC, AccountCaps(replies_per_hour=4, replies_per_day=20, own_posts_per_day=1)),
            (R_ACC, AccountCaps(replies_per_hour=4, replies_per_day=20, own_posts_per_day=1)),
        )
    )
    both = [_cand(1, X_ACC, "X", same), _cand(2, R_ACC, "REDDIT", same)]
    assert "konta" not in _plan(both, same).to_json(now=NOW), "one sentence tells it"


def test_what_is_committed_on_one_account_pushes_only_that_account() -> None:
    budget = _per_account()
    ledger = {bg.reply_ledger(X_ACC): [NOW - 5 * MIN * i for i in range(6)]}  # X's hour is full
    cands = [_cand(1, X_ACC, "X", budget), _cand(2, R_ACC, "REDDIT", budget)]
    schedule = _plan(cands, budget, ledger=ledger, rng=_Floor())
    (x,), (r,) = _of(schedule, X_ACC), _of(schedule, R_ACC)
    assert r - NOW < MIN, "Reddit goes now"
    assert x - NOW >= 24 * MIN, (
        "X waits for the oldest of its six to leave the hour (at -25 min + 1 h)"
    )


def test_the_overall_ceiling_and_the_own_post_cap_hold_on_top_of_the_account_rules() -> None:
    ceiling = Budget(publications_per_hour=3, account_limits=_per_account().account_limits)
    cands = [_cand(i, X_ACC, "X", ceiling) for i in range(1, 7)]
    schedule = _plan(cands, ceiling, rng=_Floor())
    times = sorted(p.at for p in schedule.placed)
    for end in times:
        assert sum(1 for t in times if end - HOUR <= t <= end) <= 3, "the ceiling: three an hour"
    assert schedule.gap == 20 * MIN, "paced by the tightest hourly rule: an hour over three"

    own = [_cand(i, X_ACC, "X", _per_account(), own=True) for i in range(1, 5)]
    posts = _plan(own, _per_account(), rng=_Floor())
    placed = sorted(p.at for p in posts.placed)
    assert len(placed) == 2 and all(p.at <= NOW + 12 * HOUR for p in posts.placed), (
        "two own posts a day on X, the rest wait: own posts are counted apart from replies"
    )
    assert {item.code for item in posts.left} == {bp.LEFT_OWN}


def test_own_posts_and_replies_of_one_account_are_spaced_not_a_burst() -> None:
    """An own post has no hourly rule of its own (its cap is per day), so the batch gives it the pace
    of its account's replies (`Candidate.pace`): own posts and replies of one account interleave on
    the same spacing, as they did when one hourly bucket spaced everything."""
    budget = Budget(
        account_limits=(
            (X_ACC, AccountCaps(replies_per_hour=6, replies_per_day=30, own_posts_per_day=3)),
        )
    )
    pace = (bg.reply_ledger(X_ACC), HOUR / 6)
    cands = [
        Candidate(
            revision_id=uuid.UUID(int=n),
            platform="X",
            own_post=own,
            thread_at=None if own else NOW - timedelta(hours=2),
            expires_at=NOW + timedelta(hours=48),
            account_id=X_ACC,
            caps=bg.caps_for(budget, account_id=X_ACC, platform="X", own_post=own),
            pace=pace if own else None,
        )
        for n, own in ((1, True), (2, True), (3, False), (4, True))
    ]
    schedule = _plan(cands, budget, rng=_Floor())
    times = sorted(p.at for p in schedule.placed)
    assert len(times) == 4
    for earlier, later in pairwise(times):
        assert later - earlier >= 10 * MIN + MIN, "an hour over six, plus the minimum jitter"
    assert schedule.gap == 10 * MIN, "the account's gap is the one the sentence tells"
    # without the pace they would all have started at the click
    bare = [
        Candidate(
            c.revision_id, c.platform, c.own_post, c.thread_at, c.expires_at, c.account_id, c.caps
        )
        for c in cands
    ]
    burst = sorted(
        p.at for p in _plan(bare, budget, rng=_Floor()).placed if p.revision_id != uuid.UUID(int=3)
    )
    assert burst[1] - burst[0] < MIN, "the old behaviour of a candidate with no pace: a burst"


def test_the_batch_tool_gives_each_candidate_the_rules_and_the_pace_of_its_account() -> None:
    from control_plane.tools.card_batch import _Checked, _limits_json, _with_rules

    limits = bg.effective_limits(_per_account())

    def checked(account: uuid.UUID, platform: str, handle: str, own: bool) -> _Checked:
        cand = Candidate(uuid.UUID(int=7), platform, own, None, None, account_id=account)
        return _Checked(
            cand.revision_id,
            None,
            "h" * 64,
            account,
            "b",
            cand,
            platform,
            handle,
            "OWN_POST" if own else "REPLY",
        )

    reply = _with_rules(checked(X_ACC, "X", "demo_founder", False), limits)
    assert [(c.ledger, c.per_hour, c.per_day) for c in reply.caps] == [
        (bg.reply_ledger(X_ACC), 6, 30)
    ]
    assert reply.caps[0].what == "X @demo_founder: odpowiedzi" and reply.pace is None
    own = _with_rules(checked(R_ACC, "REDDIT", "Demo_Maintainer", True), limits)
    assert [(c.ledger, c.per_hour, c.per_day) for c in own.caps] == [
        (bg.own_ledger(R_ACC), None, 1)
    ]
    assert own.pace == (bg.reply_ledger(R_ACC), 30 * MIN), (
        "spaced like Reddit's replies: two an hour"
    )
    legacy = bg.effective_limits(Budget(publications_per_hour=4, publications_per_day=20))
    one_bucket = _with_rules(checked(X_ACC, "X", "demo_founder", True), legacy)
    assert one_bucket.pace is None and one_bucket.caps[0].ledger == bg.LEDGER_ALL

    told = _limits_json(
        limits,
        [
            checked(X_ACC, "X", "demo_founder", False),
            checked(R_ACC, "REDDIT", "Demo_Maintainer", False),
        ],
    )
    assert [(a["platforma"], a["godzina"], a["doba"], a["wlasne_doba"]) for a in told["konta"]] == [
        ("REDDIT", 2, 10, 1),
        ("X", 6, 30, 2),
    ]
    assert "razem_godzina" not in told, "no overall ceiling was signed"
    assert "konta" not in _limits_json(legacy, []), "one bucket: the project's three numbers only"


def test_replies_do_not_use_up_the_own_post_and_own_posts_do_not_use_up_replies() -> None:
    budget = _per_account()
    # 30 replies today, the oldest 13 h 29 min ago: the day frees for one more in about ten and a half hours
    ledger = {bg.reply_ledger(X_ACC): [NOW - 13 * HOUR - i * MIN for i in range(30)]}
    both = [_cand(1, X_ACC, "X", budget, own=True), _cand(2, X_ACC, "X", budget)]
    schedule = _plan(both, budget, ledger=ledger, rng=_Floor())
    by_id = {p.revision_id: p.at for p in schedule.placed}
    assert by_id[uuid.UUID(int=1)] - NOW < MIN, "the own post goes now, the replies' day is full"
    assert by_id[uuid.UUID(int=2)] - NOW > HOUR, "the reply waits for the day to free"


def test_an_account_whose_limit_is_zero_is_left_out_and_the_others_are_placed() -> None:
    budget = Budget(
        account_limits=(
            (X_ACC, AccountCaps(replies_per_hour=6, replies_per_day=30, own_posts_per_day=1)),
            (R_ACC, AccountCaps(replies_per_hour=0, replies_per_day=10, own_posts_per_day=1)),
        )
    )
    cands = [_cand(1, X_ACC, "X", budget), _cand(2, R_ACC, "REDDIT", budget)]
    schedule = _plan(cands, budget)
    assert [p.revision_id for p in schedule.placed] == [uuid.UUID(int=1)]
    assert [(item.revision_id, item.code) for item in schedule.left] == [
        (uuid.UUID(int=2), bp.LEFT_NO_LIMIT)
    ]


def test_one_bucket_candidates_are_planned_exactly_as_before() -> None:
    legacy = Budget(publications_per_hour=4, publications_per_day=20)
    cands = [_cand(i, X_ACC if i % 2 else R_ACC, "X", legacy) for i in range(1, 6)]
    bare = [
        Candidate(c.revision_id, c.platform, c.own_post, c.thread_at, c.expires_at) for c in cands
    ]
    new = _plan(cands, legacy, rng=random.Random(11))
    old = bp.plan_schedule(
        bare,
        now=NOW,
        per_hour=4,
        per_day=20,
        own_per_day=1,
        events=[],
        own_events=[],
        rng=random.Random(11),
    )
    assert [(p.revision_id, p.at) for p in new.placed] == [
        (p.revision_id, p.at) for p in old.placed
    ]
    assert new.gap == old.gap == 15 * MIN, "one bucket: every reply is spaced from the one before"


def _brute(times, per_hour, per_day, ends) -> None:
    """Every closed rolling window that ENDS at one of `ends` (the moments just placed) holds at most
    `per_hour` and `per_day`, counting what was there before. History that already broke a limit by
    itself is not the planner's to fix: only windows a placement can be in are asked."""
    pool = sorted(times)
    for end in ends:
        assert sum(1 for x in pool if end - HOUR <= x <= end) <= per_hour, (end, per_hour)
        assert sum(1 for x in pool if end - DAY <= x <= end) <= per_day, (end, per_day)


@pytest.mark.parametrize("seed", range(60))
def test_random_worlds_never_break_any_rule_of_any_account(seed: int) -> None:
    rng = random.Random(seed)
    accounts = {
        X_ACC: ("X", rng.randint(1, 6), rng.randint(6, 30), rng.randint(1, 3)),
        R_ACC: ("REDDIT", rng.randint(1, 3), rng.randint(3, 10), rng.randint(1, 2)),
    }
    budget = Budget(
        publications_per_hour=rng.choice([None, 3, 5]),
        publications_per_day=rng.choice([None, 12, 25]),
        account_limits=tuple(
            (acc, AccountCaps(replies_per_hour=h, replies_per_day=d, own_posts_per_day=o))
            for acc, (_p, h, d, o) in accounts.items()
        ),
    )
    ledger: dict = {bg.LEDGER_ALL: [], bg.LEDGER_OWN: []}
    for acc in accounts:
        mine = [NOW - timedelta(minutes=rng.randint(1, 1400)) for _ in range(rng.randint(0, 5))]
        ledger[bg.reply_ledger(acc)] = mine
        ledger[bg.LEDGER_ALL] += mine
    cands = []
    for n in range(1, rng.randint(2, 14)):
        acc = rng.choice(list(accounts))
        cands.append(
            _cand(
                n,
                acc,
                accounts[acc][0],
                budget,
                own=rng.random() < 0.2,
                age=timedelta(minutes=rng.randint(5, 700)),
            )
        )
    schedule = _plan(cands, budget, ledger=ledger, rng=random.Random(seed))
    limits = bg.effective_limits(budget)
    by_id = {c.revision_id: c for c in cands}
    for acc, (_p, h, d, o) in accounts.items():
        mine = [p.at for p in schedule.placed if p.account_id == acc]
        replies = [
            p.at
            for p in schedule.placed
            if p.account_id == acc and not by_id[p.revision_id].own_post
        ]
        _brute(ledger[bg.reply_ledger(acc)] + replies, h, d, replies)
        own_times = [
            p.at for p in schedule.placed if p.account_id == acc and by_id[p.revision_id].own_post
        ]
        for end in own_times:
            assert sum(1 for x in own_times if end - DAY <= x <= end) <= o
        assert len(mine) == len(replies) + len(own_times)
    if limits.overall_per_hour() or limits.overall_per_day():
        placed_times = [p.at for p in schedule.placed]
        _brute(
            ledger[bg.LEDGER_ALL] + placed_times,
            limits.overall_per_hour() or 10**6,
            limits.overall_per_day() or 10**6,
            placed_times,
        )
    # the planner and the gate are one function: every slot passes `fits_caps` in the order placed
    work = {key: list(value) for key, value in ledger.items()}
    for placed in schedule.placed:
        cand = by_id[placed.revision_id]
        assert bp.fits_caps(work, placed.at, cand.caps)
        for member in bg.member_ledgers(cand.account_id, own_post=cand.own_post):
            work.setdefault(member, []).append(placed.at)


def test_the_replay_at_the_click_keeps_the_moments_and_never_goes_earlier() -> None:
    budget = _per_account()
    cands = [_cand(i, X_ACC, "X", budget) for i in range(1, 4)]
    cands += [_cand(10 + i, R_ACC, "REDDIT", budget) for i in range(1, 3)]
    schedule = _plan(cands, budget, rng=random.Random(5))
    by_id = {c.revision_id: c for c in cands}
    planned = [(by_id[p.revision_id], p.at) for p in schedule.placed]
    click = NOW + 5 * MIN  # the person clicks five minutes after the question: inside the tolerance
    again = bp.replay_with_caps(planned, now=click, ledger={})
    assert not again.left
    shown = {p.revision_id: p.at for p in schedule.placed}
    assert all(p.at >= shown[p.revision_id] for p in again.placed), "never earlier"
    # the world moved: X has already published six this hour, so its replies slip and are refused
    moved = {bg.reply_ledger(X_ACC): [NOW + 2 * MIN - i * MIN for i in range(6)]}
    refused = bp.replay_with_caps(planned, now=click, ledger=moved)
    assert {item.code for item in refused.left} == {bp.LEFT_MOVED}
    assert {p.account_id for p in refused.placed} == {R_ACC}, "Reddit's moments are untouched"


# ---------------------------------------------------------------------------
# The card's side: `plan_limits` signs a number for every chosen account
# ---------------------------------------------------------------------------


def _assigned() -> list[cc.ConsentAccount]:
    return [
        cc.ConsentAccount(X_ACC, "X", "demo_founder", "ACTIVE"),
        cc.ConsentAccount(R_ACC, "REDDIT", "Demo_Maintainer", "ACTIVE"),
    ]


def _plan_limits(**over):
    args = {
        "assigned": _assigned(),
        "chosen": [X_ACC, R_ACC],
        "actions": ["REPLY", "OWN_POST"],
        "limits": None,
        "days": 7,
        "now": NOW,
    }
    args.update(over)
    return cc.plan_limits(None, **args)


def test_the_card_signs_a_number_for_every_chosen_account_and_no_overall_ceiling() -> None:
    plan = _plan_limits(
        account_limits={
            str(X_ACC): {"godzina": 6, "doba": 30, "wlasne_doba": 2},
            str(R_ACC): {"godzina": 2, "doba": 10, "wlasne_doba": 1},
        }
    )
    assert plan.budget.account_limits == (
        (X_ACC, AccountCaps(6, 30, 2)),
        (R_ACC, AccountCaps(2, 10, 1)),
    )
    assert plan.budget.publications_per_hour is None, "each account's pace is its own"
    rows = plan.to_json()["limity_kont"]
    assert [(r["konto"], r["godzina"], r["doba"], r["wlasne_doba"]) for r in rows] == [
        (
            "Demo_Maintainer",
            2,
            10,
            1,
        ),  # by platform, then login: the sentence reads the same each time
        ("demo_founder", 6, 30, 2),
    ]
    assert {r["konto"]: r["zrodlo"]["godzina"] for r in rows} == {
        "demo_founder": "account",
        "Demo_Maintainer": "account",
    }


def test_a_chosen_account_the_card_left_out_gets_the_cards_defaults_not_a_hidden_unlimited() -> (
    None
):
    plan = _plan_limits(account_limits={str(X_ACC): {"godzina": 6, "doba": 30, "wlasne_doba": 2}})
    caps = dict(plan.budget.account_limits)
    assert caps[R_ACC] == AccountCaps(2, 10, 1), "CARD_LIMIT_DEFAULTS"


def test_a_limit_for_nobody_a_number_out_of_bounds_and_nonsense_are_refused() -> None:
    cases = [
        {str(uuid.UUID(int=0xFF)): {"godzina": 1}},  # not chosen
        {"nope": {"godzina": 1}},
        {str(X_ACC): {"godzina": 11}},  # above the bound of 10
        {str(X_ACC): {"godzina": 0}},
        {str(X_ACC): {"doba": 51}},
        {str(X_ACC): {"godzina": 5, "doba": 4}},  # an hour above its day
        {str(X_ACC): {"wlasne_doba": 9, "doba": 3}},
        {str(X_ACC): {"tydzien": 3}},
        {str(X_ACC): "6"},
        {str(X_ACC): {"godzina": True}},
    ]
    for asked in cases:
        with pytest.raises(cc.CardConsentRefused) as refused:
            _plan_limits(account_limits=asked)
        assert refused.value.code == cc.REFUSED_LIMITS, asked


def test_without_per_account_numbers_the_three_are_one_shared_bucket_as_before() -> None:
    plan = _plan_limits(limits={"godzina": 3, "doba": 15, "wlasne_doba": 1})
    assert plan.budget == Budget(
        publications_per_day=15, publications_per_hour=3, own_posts_per_day=1
    )
    assert "limity_kont" not in plan.to_json()


def test_the_confirmation_is_bound_to_the_numbers_the_person_was_shown() -> None:
    first = _plan_limits(account_limits={str(X_ACC): {"godzina": 6, "doba": 30, "wlasne_doba": 2}})
    second = _plan_limits(account_limits={str(X_ACC): {"godzina": 7, "doba": 30, "wlasne_doba": 2}})
    assert first != second, (
        "the grant compares the whole plan: a changed number is a changed sentence"
    )
    assert first == _plan_limits(
        account_limits={str(X_ACC): {"godzina": 6, "doba": 30, "wlasne_doba": 2}}
    )
