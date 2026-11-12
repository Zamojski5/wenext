"""Limits per platform and per account, against PostgreSQL (v11, 13.10).

THE FOUNDER (13.10): X replies at six an hour waited behind Reddit's one an hour, and the slow
pace of one platform was spent by whoever got there first. What is held here is the same promise as
`test_per_account_limits_unit.py`, but through the real rows:

  * THE LEDGERS are read from the same tables the old counters were (attempts that reached the
    platform, the queue by the moment each request may go out), by account and by kind;
  * THE GATE, in the decision (`approval`) and in the person's click (`human_approval`): one
    account's full hour does not stop another, the refusal names the account, and a scope signed
    with ONE bucket is refused exactly as before (the whole backward-compatibility argument);
  * "SEND ALL" plans each account on its own hourly limit and the gate under the project's lock
    agrees with the plan;
  * THE PUBLISHER holds a request for its own account's real attempts only, and still reads one
    bucket for a scope that signed one;
  * THE CARD signs a number for every chosen account, behind the same click token.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from control_plane.presence import budget as bg
from control_plane.presence.scope import AccountCaps, Budget
from control_plane.presence.store import PresenceStore
from control_plane.tools.card_send import META_TOKEN
from tests.test_card_batch_db import _ask, _at, _confirm, _Floor
from tests.test_card_send_db import (
    _account,
    _click,
    _counts,
    _drafted,
    _drafted_x,
    _sign,
    _world,
)
from tests.test_opportunities_db import _mark_published

pytestmark = pytest.mark.db


def _caps(hour: int, day: int, own: int) -> AccountCaps:
    return AccountCaps(replies_per_hour=hour, replies_per_day=day, own_posts_per_day=own)


async def _two_accounts(pool, tenant, *, x=(3, 30, 2), reddit=(1, 6, 1), **budget):
    """A project with a Reddit and an X account and a REVIEW scope with a pace for each."""
    world = await _world(pool, tenant)
    x_id, x_login = await _account(pool, tenant, world.project_id, "X")
    world.registry._batch_rng = _Floor()
    await _sign(
        pool,
        tenant,
        world,
        accounts=(world.account_id, x_id),
        budget=Budget(
            account_limits=tuple(
                sorted(
                    ((world.account_id, _caps(*reddit)), (x_id, _caps(*x))),
                    key=lambda pair: str(pair[0]),
                )
            ),
            **budget,
        ),
    )
    return world, x_id, x_login


async def _spent(pool, world, tenant, accounts):
    delegation = await PresenceStore(pool).delegation_for(
        organization_id=tenant["organization"], project_id=world.project_id
    )
    async with pool.acquire() as conn:
        return await bg.spent(
            conn,
            organization_id=tenant["organization"],
            project_id=world.project_id,
            accounts=tuple(accounts),
            limits=bg.effective_limits(delegation),
            now=datetime.now(UTC),
        )


async def _request_of(pool, world, revision: str) -> uuid.UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT id FROM publication_requests WHERE project_id = $1 AND proposal_revision_id = $2",
            world.project_id,
            uuid.UUID(revision),
        )


# ---------------------------------------------------------------------------
# The signature
# ---------------------------------------------------------------------------


async def test_a_pace_per_account_is_signed_and_read_back_with_the_digest_it_was_signed_under(
    pool, tenant
) -> None:
    world, x_id, _login = await _two_accounts(pool, tenant)
    delegation = await PresenceStore(pool).delegation_for(
        organization_id=tenant["organization"], project_id=world.project_id
    )
    assert dict(delegation.budget.account_limits) == {
        world.account_id: _caps(1, 6, 1),
        x_id: _caps(3, 30, 2),
    }
    limits = bg.effective_limits(delegation)
    assert bg.limits_for(limits, x_id, "X").replies_per_hour == 3
    assert bg.limits_for(limits, world.account_id, "REDDIT").replies_per_hour == 1


async def test_a_scope_signed_before_v11_reads_back_with_its_old_budget_untouched(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    row = await _sign(
        pool, tenant, world, budget=Budget(publications_per_hour=1, publications_per_day=6)
    )
    delegation = await PresenceStore(pool).delegation_for(
        organization_id=tenant["organization"], project_id=world.project_id
    )
    assert delegation.budget.to_json() == {"publications_per_hour": 1, "publications_per_day": 6}
    assert delegation.scope_sha256 == row.scope_sha256
    assert bg.effective_limits(delegation).per_account is False


# ---------------------------------------------------------------------------
# The ledgers and the counters
# ---------------------------------------------------------------------------


async def test_the_ledgers_count_each_account_by_kind_from_the_same_rows(pool, tenant) -> None:
    world, x_id, x_login = await _two_accounts(pool, tenant, x=(6, 30, 2), reddit=(2, 10, 1))
    x1 = await _drafted_x(world, x_login, status="2103587050346250341")
    x2 = await _drafted_x(world, x_login, status="2103587050346250342")
    r1 = await _drafted(world, post="redd1x")
    for draft in (x1, x2, r1):
        assert (await _click(world, draft))["stan"] == "OCZEKUJE"
    await _mark_published(pool, tenant, await _request_of(pool, world, x1["revision"]))

    async with pool.acquire() as conn:
        ledger = await bg.committed_ledger(
            conn,
            organization_id=tenant["organization"],
            project_id=world.project_id,
            accounts=(world.account_id, x_id),
            now=datetime.now(UTC),
        )
    assert len(ledger[bg.reply_ledger(x_id)]) == 2, "one published (an attempt) and one queued"
    assert len(ledger[bg.reply_ledger(world.account_id)]) == 1
    assert len(ledger[bg.LEDGER_ALL]) == 3
    assert ledger[bg.LEDGER_OWN] == [], "no own post in this project"

    used = await _spent(pool, world, tenant, (world.account_id, x_id))
    mine = {one.handle: one for one in used.accounts}
    assert mine[x_login].replies_this_hour == 2 and mine[x_login].replies_today == 2
    assert mine[x_login].limits.replies_per_hour == 6
    assert used.publications_this_hour == 3, "the project total is still counted for the screens"
    assert [one.platform for one in used.accounts] == ["REDDIT", "X"]


async def test_real_attempts_alone_are_what_the_publishers_pace_counts(pool, tenant) -> None:
    world, x_id, x_login = await _two_accounts(pool, tenant, x=(1, 30, 2), reddit=(1, 6, 1))
    x1 = await _drafted_x(world, x_login, status="2103587050346250341")
    x2 = await _drafted_x(world, x_login, status="2103587050346250342")
    assert (await _click(world, x1))["stan"] == "OCZEKUJE"
    delegation = await PresenceStore(pool).delegation_for(
        organization_id=tenant["organization"], project_id=world.project_id
    )
    limits = bg.effective_limits(delegation)
    accounts = (world.account_id, x_id)
    revision = uuid.UUID(x2["revision"])

    async def held() -> str | None:
        async with pool.acquire() as conn:
            return await bg.pace_blocked(
                conn,
                organization_id=tenant["organization"],
                revision_id=revision,
                own_post=False,
                limits=limits,
                accounts=accounts,
                now=datetime.now(UTC),
            )

    assert await held() is None, "a QUEUED request is not an attempt: nothing reached the platform"
    await _mark_published(pool, tenant, await _request_of(pool, world, x1["revision"]))
    message = await held()
    assert message is not None and message.startswith("tempo: X @")
    assert "1/1 w ostatniej godzinie" in message


# ---------------------------------------------------------------------------
# The gate: one click, one account
# ---------------------------------------------------------------------------


async def test_a_second_x_reply_goes_while_reddits_hour_is_full_and_the_refusal_names_reddit(
    pool, tenant
) -> None:
    world, _x_id, x_login = await _two_accounts(pool, tenant, x=(3, 30, 2), reddit=(1, 6, 1))
    reddit1 = await _drafted(world, post="redd1x")
    reddit2 = await _drafted(world, post="redd2x")
    x1 = await _drafted_x(world, x_login, status="2103587050346250341")
    x2 = await _drafted_x(world, x_login, status="2103587050346250342")

    assert (await _click(world, reddit1))["stan"] == "OCZEKUJE"
    assert (await _click(world, x1))["stan"] == "OCZEKUJE", (
        "one shared bucket would have refused this"
    )
    assert (await _click(world, x2))["stan"] == "OCZEKUJE"

    minted = await world.registry.call(
        "room.send_token", {"revision_id": reddit2["revision"], "odcisk": reddit2["odcisk"]}
    )
    token = (minted.get("_mcp_meta") or {}).get(META_TOKEN)
    refused = await world.registry.call(
        "room.send_draft", {"revision_id": reddit2["revision"], "token": token}
    )
    assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == "BUDZET", refused
    assert (
        "Reddit u/" in refused["powod"]
        and "odpowiedzi 1 z 1 w ostatniej godzinie" in refused["powod"]
    )


async def test_one_shared_bucket_is_refused_exactly_as_it_always_was(pool, tenant) -> None:
    world = await _world(pool, tenant)
    x_id, x_login = await _account(pool, tenant, world.project_id, "X")
    await _sign(
        pool,
        tenant,
        world,
        accounts=(world.account_id, x_id),
        budget=Budget(publications_per_hour=1, publications_per_day=6),
    )
    assert (await _click(world, await _drafted(world, post="redd1x")))["stan"] == "OCZEKUJE"
    x = await _drafted_x(world, x_login)
    minted = await world.registry.call(
        "room.send_token", {"revision_id": x["revision"], "odcisk": x["odcisk"]}
    )
    refused = await world.registry.call(
        "room.send_draft",
        {"revision_id": x["revision"], "token": (minted["_mcp_meta"] or {})[META_TOKEN]},
    )
    assert refused["kod"] == "BUDZET"
    assert "publikacje: 1 z 1 w ostatniej godzinie" in refused["powod"], "the project's bucket"


async def test_the_overall_ceiling_binds_the_accounts_together_when_one_was_signed(
    pool, tenant
) -> None:
    world, _x_id, x_login = await _two_accounts(
        pool, tenant, x=(5, 30, 2), reddit=(5, 30, 1), publications_per_hour=2
    )
    assert (await _click(world, await _drafted(world, post="redd1x")))["stan"] == "OCZEKUJE"
    assert (await _click(world, await _drafted_x(world, x_login)))["stan"] == "OCZEKUJE"
    third = await _drafted(world, post="redd2x")
    minted = await world.registry.call(
        "room.send_token", {"revision_id": third["revision"], "odcisk": third["odcisk"]}
    )
    refused = await world.registry.call(
        "room.send_draft",
        {"revision_id": third["revision"], "token": minted["_mcp_meta"][META_TOKEN]},
    )
    assert (
        refused["kod"] == "BUDZET" and "publikacje: 2 z 2 w ostatniej godzinie" in refused["powod"]
    )


# ---------------------------------------------------------------------------
# "Send all": each account on its own pace
# ---------------------------------------------------------------------------


async def test_a_batch_places_each_account_on_its_own_hourly_limit(pool, tenant) -> None:
    world, _x_id, x_login = await _two_accounts(pool, tenant, x=(3, 30, 2), reddit=(1, 6, 1))
    xs = [await _drafted_x(world, x_login, status=f"21035870503462503{40 + i}") for i in range(3)]
    reddits = [await _drafted(world, post=f"redd{i}x") for i in range(2)]
    drafts = [*xs, *reddits]

    answer, token = await _ask(world, drafts)

    assert answer["stan"] == "WYKONANE" and token, answer
    plan = answer["paczka"]
    assert plan["ile"] == 5 and answer["odrzucone"] == []
    moments = {"X": [], "REDDIT": []}
    for item in plan["pozycje"]:
        moments[item["platforma"]].append(_at(item["kiedy"]))
    for earlier, later in zip(sorted(moments["X"]), sorted(moments["X"])[1:], strict=False):
        assert timedelta(minutes=20) <= later - earlier <= timedelta(minutes=25), "X: three an hour"
    reddit = sorted(moments["REDDIT"])
    assert reddit[1] - reddit[0] >= timedelta(minutes=61), "Reddit: one an hour"
    assert max(moments["X"]) < reddit[1], "X is not made to wait for Reddit's slow pace"
    assert len(plan["limity"]["konta"]) == 2, "the limits of the accounts in THIS batch"
    by_platform = {row["platforma"]: row for row in plan["limity"]["konta"]}
    assert (by_platform["X"]["godzina"], by_platform["X"]["doba"]) == (3, 30)
    assert (by_platform["REDDIT"]["godzina"], by_platform["REDDIT"]["doba"]) == (1, 6)
    assert {row["odstep_min"] for row in plan["konta"]} == {20, 60}

    sent = await _confirm(world, token)
    assert sent["stan"] == "OCZEKUJE" and len(sent["zaplanowane"]) == 5 and sent["odmowy"] == [], (
        sent
    )
    assert (await _counts(pool, world))["requests"] == 5


async def test_the_gate_under_the_lock_agrees_with_the_plan_per_account(pool, tenant) -> None:
    world, x_id, x_login = await _two_accounts(pool, tenant, x=(2, 30, 2), reddit=(1, 6, 1))
    xs = [await _drafted_x(world, x_login, status=f"21035870503462503{50 + i}") for i in range(3)]
    answer, token = await _ask(world, xs)
    assert answer["paczka"]["ile"] == 3, (
        "two an hour: the third waits for the next hour, still inside 12 h"
    )
    sent = await _confirm(world, token)
    assert sent["stan"] == "OCZEKUJE" and sent["odmowy"] == []
    # the same moment taken again for the same account has no place: the gate is the planner's `fits`
    from control_plane.presence import batch_plan

    delegation = await PresenceStore(pool).delegation_for(
        organization_id=tenant["organization"], project_id=world.project_id
    )
    first = min(_at(item["kiedy"]) for item in sent["zaplanowane"])
    async with pool.acquire() as conn:
        taken = await batch_plan.slot_blocked(
            conn,
            organization_id=tenant["organization"],
            project_id=world.project_id,
            accounts=delegation.scope.accounts,
            limits=bg.effective_limits(delegation),
            own_post=False,
            at=first + timedelta(minutes=1),
            now=datetime.now(UTC),
            account_id=x_id,
            platform="X",
        )
        free_elsewhere = await batch_plan.slot_blocked(
            conn,
            organization_id=tenant["organization"],
            project_id=world.project_id,
            accounts=delegation.scope.accounts,
            limits=bg.effective_limits(delegation),
            own_post=False,
            at=first + timedelta(minutes=1),
            now=datetime.now(UTC),
            account_id=world.account_id,
            platform="REDDIT",
        )
    assert taken is not None and "X @" in taken and "odpowiedzi" in taken
    assert free_elsewhere is None, "Reddit has its own hour"


# ---------------------------------------------------------------------------
# The card signs it
# ---------------------------------------------------------------------------


async def test_the_limits_panel_signs_a_number_per_account_behind_the_click_token(
    pool, tenant
) -> None:
    world, x_id, _login = await _two_accounts(pool, tenant)
    asked = {
        "akcja": "podpisz",
        "konta": [str(world.account_id), str(x_id)],
        "dzialania": ["REPLY", "OWN_POST"],
        "limity_konta": {
            str(x_id): {"godzina": 6, "doba": 30, "wlasne_doba": 2},
            str(world.account_id): {"godzina": 2, "doba": 10, "wlasne_doba": 1},
        },
        "dni": 7,
    }
    answer = await world.registry.call("room.scope_token", asked)
    assert answer["stan"] == "WYKONANE", answer
    rows = {row["platforma"]: row for row in answer["zakres"]["limity_kont"]}
    assert (rows["X"]["godzina"], rows["X"]["doba"], rows["X"]["wlasne_doba"]) == (6, 30, 2)
    assert (rows["REDDIT"]["godzina"], rows["REDDIT"]["doba"]) == (2, 10)
    assert answer["zakres"]["razem"] == {"godzina": None, "doba": None}
    token = (answer.get("_mcp_meta") or {}).get(META_TOKEN)
    assert token and token not in str({k: v for k, v in answer.items() if k != "_mcp_meta"})

    before = (await _counts(pool, world))["delegations"]
    signed = await world.registry.call("room.sign_scope", {"token": token})
    assert signed["stan"] == "WYKONANE", signed
    assert (await _counts(pool, world))["delegations"] == before + 1
    delegation = await PresenceStore(pool).delegation_for(
        organization_id=tenant["organization"], project_id=world.project_id
    )
    assert dict(delegation.budget.account_limits)[x_id] == _caps(6, 30, 2)
    assert delegation.mode == "REVIEW", "the card never signs more than a click per text"
    shown = await world.registry.call("room.show", {})
    by_platform = {c["platforma"]: c for c in shown["zakres"]["do_wyboru"]}
    assert by_platform["X"]["limity"]["godzina"] == 6 and shown["zakres"]["wedlug_kont"] is True
    assert by_platform["X"]["limity"]["zrodlo"]["godzina"] == "account"


async def test_a_limit_for_an_account_nobody_chose_is_refused_and_signs_nothing(
    pool, tenant
) -> None:
    world, x_id, _login = await _two_accounts(pool, tenant)
    stranger = str(uuid.uuid4())
    answer = await world.registry.call(
        "room.scope_token",
        {
            "akcja": "podpisz",
            "konta": [str(x_id)],
            "dzialania": ["REPLY"],
            "limity_konta": {stranger: {"godzina": 1, "doba": 2, "wlasne_doba": 1}},
        },
    )
    assert answer["stan"] == "NIEDOSTEPNE" and answer["kod"] == "LIMIT_POZA_GRANICA", answer
    assert not (answer.get("_mcp_meta") or {}).get(META_TOKEN)


async def test_the_scope_summary_for_a_legacy_scope_still_reads_one_bucket(pool, tenant) -> None:
    world = await _world(pool, tenant)
    await _sign(pool, tenant, world, budget=Budget(publications_per_hour=2, publications_per_day=9))
    shown = await world.registry.call("room.show", {})
    zakres = shown["zakres"]
    assert zakres["wedlug_kont"] is False
    assert zakres["limity"] == {"godzina": 2, "doba": 9, "wlasne_doba": 1}
    in_scope = [c for c in zakres["do_wyboru"] if c["w_zakresie"]]
    assert in_scope and all(c["limity"]["wspolny_kosz"] is True for c in in_scope)


# ---------------------------------------------------------------------------
# The decision of an autonomous scope, and a pace signed per PLATFORM
# ---------------------------------------------------------------------------


async def test_an_autonomous_decision_counts_replies_and_own_posts_apart_per_platform(
    pool, tenant
) -> None:
    """A pace signed for the platform (`platform_limits`) binds every account of it; the decision of
    an AUTONOMOUS scope asks the account's replies for a reply and its own posts for an own post, and
    the refusal names the account."""
    from control_plane.presence.approval import ISSUED, REFUSED
    from tests.test_presence_own_post import (
        _approver,
        _propose_own_post,
        _propose_reply,
        _requests,
        _seed,
        now,
    )

    seeded = await _seed(
        pool,
        tenant,
        budget={"platform_limits": {"REDDIT": {"replies_per_hour": 1, "replies_per_day": 5}}},
    )

    async def decide() -> list:
        return await _approver(pool, tenant).consider_pending(
            workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
        )

    await _propose_reply(pool, tenant, seeded, text="pierwsza odpowiedź")
    assert [one.outcome for one in await decide()] == [ISSUED]
    await _propose_reply(pool, tenant, seeded, text="druga odpowiedź, ta sama godzina")
    second = await decide()
    assert [one.outcome for one in second] == [REFUSED]
    assert (
        "Reddit u/" in second[0].reason
        and "odpowiedzi 1 z 1 w ostatniej godzinie" in second[0].reason
    )

    # a full hour of replies does not stand in the way of the day's one own post
    await _propose_own_post(pool, tenant, seeded)
    outcomes = sorted(one.outcome for one in await decide())
    assert outcomes == sorted([REFUSED, ISSUED]), (
        "the second reply is still refused, the own post goes"
    )
    kinds = sorted(row["kind"] for row in await _requests(pool, seeded))
    assert kinds == ["OWN_POST", "REPLY"]


async def test_the_old_one_bucket_decision_is_unchanged(pool, tenant) -> None:
    from control_plane.presence.approval import ISSUED, REFUSED
    from tests.test_presence_own_post import (
        _approver,
        _propose_reply,
        _seed,
        now,
    )

    seeded = await _seed(
        pool, tenant, budget={"publications_per_hour": 1, "publications_per_day": 6}
    )
    await _propose_reply(pool, tenant, seeded, text="pierwsza")
    first = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    await _propose_reply(pool, tenant, seeded, text="druga")
    second = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    assert [one.outcome for one in first] == [ISSUED]
    assert [one.outcome for one in second] == [REFUSED]
    assert "publikacje: 1 z 1 w ostatniej godzinie" in second[0].reason


# ---------------------------------------------------------------------------
# The publisher's own pace
# ---------------------------------------------------------------------------


async def test_the_publisher_holds_a_request_for_its_own_accounts_attempts_and_no_other(
    pool, tenant
) -> None:
    """Three requests from a batch: an X reply now, an X reply an hour later, a Reddit reply now. The
    first X reply goes out; the second X reply, whose moment is brought forward, is held by X's own
    pace (one an hour) and the Reddit reply is NOT held by X's attempt. A scope signed with one bucket
    keeps the organization-wide count it always had."""
    from tests.test_presence_own_post import _world as publisher_world

    world, _x_id, x_login = await _two_accounts(pool, tenant, x=(1, 30, 2), reddit=(1, 6, 1))
    x1 = await _drafted_x(world, x_login, status="2103587050346250341")
    x2 = await _drafted_x(world, x_login, status="2103587050346250342")
    r1 = await _drafted(world, post="redd1x")
    _answer, token = await _ask(world, [x1, x2, r1])
    assert (await _confirm(world, token))["stan"] == "OCZEKUJE"
    ids = {
        name: await _request_of(pool, world, d["revision"])
        for name, d in (("x1", x1), ("x2", x2), ("r1", r1))
    }
    await _mark_published(pool, tenant, ids["x1"])
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET not_before = NULL WHERE id = $1", ids["x2"]
        )

    handlers = publisher_world(pool, tenant).handlers
    now = datetime.now(UTC)

    async def pace_of(name: str):
        request = await handlers._publication.get_request(ids[name])
        rule = await handlers._governance(request, now)
        return rule, await handlers._pace_detail(request, rule, now)

    rule, held = await pace_of("x2")
    assert rule.limits is not None and rule.limits.per_account is True
    assert held is not None and held.startswith("tempo: X @") and "1/1 w ostatniej godzinie" in held
    assert "delegacja" in held, "the queue's detail line says whose pace it is"
    _rule, reddit = await pace_of("r1")
    assert reddit is None, "an X attempt does not hold a Reddit reply"


async def test_a_scope_with_one_bucket_still_reads_the_organizations_attempts(pool, tenant) -> None:
    from tests.test_presence_own_post import _world as publisher_world

    world = await _world(pool, tenant)
    await _sign(pool, tenant, world, budget=Budget(publications_per_hour=1, publications_per_day=6))
    draft = await _drafted(world, post="redd1x")
    assert (await _click(world, draft))["stan"] == "OCZEKUJE"
    handlers = publisher_world(pool, tenant).handlers
    request = await handlers._publication.get_request(
        await _request_of(pool, world, draft["revision"])
    )
    rule = await handlers._governance(request, datetime.now(UTC))
    assert rule.limits is None, (
        "no per-account limit signed: the one bucket (`per_hour`, `per_day`)"
    )
    assert (rule.per_hour, rule.per_day) == (1, 6)
