"""The card's acts on a draft and on the scope, against PostgreSQL (v9): ONLY A CLICK CAN DO THEM.

Nothing here publishes. A revision is written, a scope is paused, resumed or signed; no adapter, no
capsule and no network is reached, and the throwaway database is the one of `run_db_tests.sh`.

THE QUESTIONS, EACH A WAY THE CARD COULD DO WHAT NOBODY MEANT IT TO DO:

  * does a call that was not a click (no token, a guessed one, a spent one, an expired one, one minted
    for another act) change nothing, and does a token minted for a pause stay worthless for a signature?
  * does a revision never publish or approve (no request, no consent), keep the old text as history,
    leave the window where it was, and kill the confirmation of the text it replaced?
  * does a pause really pause (the signature is not live, running sessions are told to stop, the next
    send is refused) and does only the dedicated click lift it, on the SAME signature?
  * does a signature made in the card say what it was shown (REVIEW, the ticked accounts, the three
    limits, a term of 7 or 14 days), refuse an empty scope, an account the project does not have, and a
    busy queue, and refuse when the scope moved after the sentence was drawn?
  * does `room.show` give the card the skipped conversations with the agent's reason, and the scope?
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from community_os_contracts.enums import SemanticCapability
from community_os_contracts.execution import ActionProposalState, ApprovalHashInput
from control_plane.approval.store import ApprovalStore
from control_plane.opportunities import OpportunityStore
from control_plane.pilot.publication import join_own_post
from control_plane.presence.scope import Budget, Scope, digest
from control_plane.presence.store import PresenceStore
from control_plane.sessions.store import EVENT_INTERRUPT, SESSION_PRESENCE, SessionStore
from control_plane.tools import NoSuchTool, ToolRegistry, issue
from control_plane.tools.card_actions import ScopeTokens
from control_plane.tools.card_send import META_TOKEN
from tests.test_card_send_db import (
    _click,
    _counts,
    _drafted,
    _drafted_x,
    _j,
    _let_the_queue_go,
    _scope_accounts,
    _send,
    _sign,
    _state,
    _token,
)
from tests.test_opportunities_db import (
    World,
    _account,
    _mark,
    _mark_published,
    _reddit_args,
    _world,
)

pytestmark = pytest.mark.db

ACTION_TOOLS = (
    "room.revise_draft",
    "room.scope_token",
    "room.pause",
    "room.resume",
    "room.sign_scope",
)
LONG_DASH = chr(0x2014)  # the long dash the founder forbids, built so no file here contains it


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _registry_like(world: World, **changes) -> ToolRegistry:
    """A registry for the same project as `world`, with another kind of host."""
    capability = world.registry.capability
    return ToolRegistry(
        pool=world.registry.pool,
        capability=issue(
            session_id=capability.session_id,
            organization_id=capability.organization_id,
            workspace_id=capability.workspace_id,
            project_id=capability.project_id,
            dsn="postgresql://atrapa/nieuzywane",
            **{"host": "codex", **changes},
        ),
    )


async def _ask(world: World, akcja: str, **args) -> tuple[dict, str | None]:
    """The card's first step on the scope. Returns the answer and the confirmation (only in `_meta`)."""
    answer = await world.registry.call("room.scope_token", {"akcja": akcja, **args})
    return answer, (answer.get("_mcp_meta") or {}).get(META_TOKEN)


async def _delegations(pool, world: World) -> list:
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM project_delegations WHERE project_id = $1 ORDER BY signed_at",
            world.project_id,
        )


async def _live(pool, world: World):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM project_delegations WHERE project_id = $1 AND revoked_at IS NULL",
            world.project_id,
        )


async def _audit(pool, kind: str, world: World) -> list:
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT kind, actor_ref, subject_ref FROM audit_events WHERE kind = $1 "
            "AND organization_id = $2 ORDER BY id",
            kind,
            world.registry.capability.organization_id,
        )


async def _expire(pool, delegation_id) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE project_delegations SET signed_at = now() - interval '2 hours', "
            "expires_at = now() - interval '1 hour' WHERE id = $1",
            delegation_id,
        )


async def _revisions(pool, world: World) -> list:
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, state, revision, payload_hash, expires_at, payload FROM "
            "action_proposal_revisions WHERE payload ->> 'plan_version' = $1 ORDER BY created_at",
            str(world.project_id),
        )


async def _nothing_published(pool, world: World) -> None:
    counts = await _counts(pool, world)
    assert counts["requests"] == 0 and counts["decisions"] == 0, counts


# ---------------------------------------------------------------------------
# Who can call them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", [{"host": "claude-code"}, {"host": None}, {"hosted": True}])
async def test_no_session_without_the_card_can_call_any_of_the_five(pool, tenant, variant) -> None:
    world = await _world(pool, tenant)
    registry = _registry_like(world, **variant)
    for name in ACTION_TOOLS:
        with pytest.raises(NoSuchTool):
            await registry.call(name, {"revision_id": str(uuid.uuid4()), "text": "x", "token": "x"})
    assert await _counts(pool, world) == {"delegations": 0, "requests": 0, "decisions": 0}


# ---------------------------------------------------------------------------
# room.revise_draft: a correction is a new revision, never a publication
# ---------------------------------------------------------------------------


async def test_a_revision_is_the_next_revision_of_the_same_draft_and_publishes_nothing(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    draft = await _drafted(world)
    shown = await world.registry.call("room.show", {})
    before = shown["szkice"][0]

    revised = await world.registry.call(
        "room.revise_draft",
        {"revision_id": draft["revision"], "text": "  Poprawiona przeze mnie.  "},
    )

    assert revised["stan"] == "WYKONANE", revised
    new = revised["szkic"]
    assert revised["poprzednia"] == draft["revision"]
    assert new["revision_id"] != draft["revision"] and new["odcisk"] != draft["odcisk"]
    assert new["tekst"] == "Poprawiona przeze mnie." and new["rodzaj"] == "REPLY"
    assert new["stan"] == "czeka_na_zgode" and new["konto"] == world.login
    assert new["wygasa"] == before["wygasa"], "okno zgody się nie przedłuża"
    assert new["okazja_id"] == before["okazja_id"]
    assert new["w_odpowiedzi_na"] == before["w_odpowiedzi_na"] and new["tytul"] == before["tytul"]
    # THE SHAPE OF ONE ELEMENT OF `szkice`: the card swaps it in without asking the room again.
    assert set(new) == set(before)

    revisions = await _revisions(pool, world)
    assert [r["state"] for r in revisions] == ["REJECTED", "READY_FOR_APPROVAL"]
    assert [r["revision"] for r in revisions] == [1, 2]
    assert revisions[0]["expires_at"] == revisions[1]["expires_at"]
    assert json.dumps(_j(revisions[0]["payload"])["text_and_media"]) != json.dumps(
        _j(revisions[1]["payload"])["text_and_media"]
    ), "stara wersja zostaje w historii nietknięta"
    assert _j(revisions[0]["payload"])["text_and_media"] == "Moja odpowiedź."
    await _nothing_published(pool, world)

    # The room now has the new revision only, and the opportunity points at it.
    again = await world.registry.call("room.show", {})
    assert [d["revision_id"] for d in again["szkice"]] == [new["revision_id"]]
    assert again["okazje"][0]["szkic"] == new["revision_id"]


async def test_a_confirmation_minted_for_the_old_text_is_worth_nothing_after_a_revision(
    pool, tenant
) -> None:
    """The person asked for "Wyślij" on the old text, then edited: the confirmation must not send."""
    world = await _world(pool, tenant)
    draft = await _drafted(world)
    minted, token = await _token(world, draft)
    assert minted["stan"] == "WYKONANE" and token
    revised = (
        await world.registry.call(
            "room.revise_draft", {"revision_id": draft["revision"], "text": "Inny tekst."}
        )
    )["szkic"]
    before = await _counts(pool, world)

    # The old token, shown for the old revision (now REJECTED) and for the new one: nothing goes out.
    old = await world.registry.call(
        "room.send_draft", {"revision_id": draft["revision"], "token": token}
    )
    assert old["stan"] == "NIEDOSTEPNE" and old["kod"] == "STAN", old
    new = await world.registry.call(
        "room.send_draft", {"revision_id": revised["revision_id"], "token": token}
    )
    assert new["stan"] == "NIEDOSTEPNE" and new["kod"] == "POTWIERDZENIE", new
    assert await _counts(pool, world) == before

    # The new text is sent through the normal two clicks, with the digest the card was given.
    sent = await _click(world, {"revision": revised["revision_id"], "odcisk": revised["odcisk"]})
    assert sent["stan"] == "OCZEKUJE", sent
    async with pool.acquire() as conn:
        queued = await conn.fetchrow(
            "SELECT draft_sha256 FROM publication_requests WHERE project_id = $1", world.project_id
        )
    from control_plane.pilot.publication import draft_sha256

    assert queued["draft_sha256"] == draft_sha256("Inny tekst.")


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", "PUSTY"),
        ("   \n  ", "PUSTY"),
        ("x" * 4001, "ZA_DLUGI"),
        (f"Pierwsze zdanie {LONG_DASH} drugie.", "DLUGA_PAUZA"),
        ("Moja odpowiedź.", "BEZ_ZMIAN"),
        ("  Moja odpowiedź.  ", "BEZ_ZMIAN"),
    ],
)
async def test_a_correction_the_server_refuses_writes_nothing_and_has_a_code(
    pool, tenant, text, code
) -> None:
    world = await _world(pool, tenant)
    draft = await _drafted(world)
    before = await _revisions(pool, world)

    refused = await world.registry.call(
        "room.revise_draft", {"revision_id": draft["revision"], "text": text}
    )

    assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == code, refused
    assert refused["powod"]
    assert [(r["id"], r["state"]) for r in await _revisions(pool, world)] == [
        (r["id"], r["state"]) for r in before
    ]
    assert await _state(pool, draft["revision"]) == "READY_FOR_APPROVAL"
    await _nothing_published(pool, world)


async def test_the_long_dash_is_refused_and_never_rewritten_into_the_text(pool, tenant) -> None:
    world = await _world(pool, tenant)
    draft = await _drafted(world)
    refused = await world.registry.call(
        "room.revise_draft",
        {"revision_id": draft["revision"], "text": f"Tak {chr(0x2013)} nie."},
    )
    assert refused["kod"] == "DLUGA_PAUZA"
    assert chr(0x2013) not in refused["powod"] and LONG_DASH not in refused["powod"]
    assert len(await _revisions(pool, world)) == 1


async def test_a_reply_on_x_is_held_to_the_weighted_length_and_the_whole_text_is_the_reply(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    _, login = await _account(pool, tenant, world.project_id, "X")
    draft = await _drafted_x(world, login)

    over = await world.registry.call(
        "room.revise_draft", {"revision_id": draft["revision"], "text": "a" * 281}
    )
    assert over["stan"] == "NIEDOSTEPNE" and over["kod"] == "DLUGOSC_X", over
    assert over["waga"] == 281 and over["limit"] == 280
    assert len(await _revisions(pool, world)) == 1, "nic nie zapisano"

    exact = await world.registry.call(
        "room.revise_draft", {"revision_id": draft["revision"], "text": "a" * 280}
    )
    assert exact["stan"] == "WYKONANE" and exact["szkic"]["tekst"] == "a" * 280
    assert exact["szkic"]["platforma"] == "X"


async def test_the_founders_text_ending_in_a_bare_domain_is_refused_as_the_actuator_would(
    pool, tenant
) -> None:
    """11.10: 275 characters ending in `getwenext.com` weigh 285 for X (the domain is a link of 23).
    The card's editor must refuse it with the number the actuator computes, not after the approval."""
    world = await _world(pool, tenant)
    _, login = await _account(pool, tenant, world.project_id, "X")
    draft = await _drafted_x(world, login)
    text = "a" * (275 - len(" getwenext.com")) + " getwenext.com"
    assert len(text) == 275

    refused = await world.registry.call(
        "room.revise_draft", {"revision_id": draft["revision"], "text": text}
    )

    assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == "DLUGOSC_X", refused
    assert refused["waga"] == 285 and refused["limit"] == 280
    assert len(await _revisions(pool, world)) == 1, "nic nie zapisano"
    # Without the domain the same length goes through: the weight is the domain's, not the text's.
    fine = await world.registry.call(
        "room.revise_draft", {"revision_id": draft["revision"], "text": "a" * 275}
    )
    assert fine["stan"] == "WYKONANE", fine


async def test_a_stale_revision_an_unknown_one_and_a_bad_id_are_refused(pool, tenant) -> None:
    world = await _world(pool, tenant)
    other = await _world(pool, tenant)
    draft = await _drafted(world)
    foreign = await _drafted(other, post="zzz999")
    first = await world.registry.call(
        "room.revise_draft", {"revision_id": draft["revision"], "text": "Raz."}
    )
    assert first["stan"] == "WYKONANE"

    stale = await world.registry.call(
        "room.revise_draft", {"revision_id": draft["revision"], "text": "Dwa."}
    )
    assert stale["stan"] == "NIEDOSTEPNE" and stale["kod"] == "STAN", "druga poprawka tej samej"
    assert len(await _revisions(pool, world)) == 2

    for args, code in (
        ({"revision_id": foreign["revision"], "text": "Cudza."}, "NIE_ZNALEZIONO"),
        ({"revision_id": str(uuid.uuid4()), "text": "Nie ma."}, "NIE_ZNALEZIONO"),
        ({"revision_id": "nie-id", "text": "x"}, "ARGUMENT"),
        ({"revision_id": draft["revision"], "text": 5}, "ARGUMENT"),
    ):
        refused = await world.registry.call("room.revise_draft", args)
        assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == code, (args, refused)
    assert await _state(pool, foreign["revision"]) == "READY_FOR_APPROVAL", "cudza nietknięta"


async def test_a_revision_that_is_already_approved_is_not_edited_from_the_card(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    draft = await _drafted(world)
    await _sign(pool, tenant, world)
    assert (await _click(world, draft))["stan"] == "OCZEKUJE"
    refused = await world.registry.call(
        "room.revise_draft", {"revision_id": draft["revision"], "text": "Za późno."}
    )
    assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == "STAN"
    assert await _state(pool, draft["revision"]) == "APPROVED"


async def test_the_body_of_an_own_reddit_post_is_edited_and_its_title_stays(pool, tenant) -> None:
    world = await _world(pool, tenant)
    store = ApprovalStore(pool)
    proposal = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=world.account_id,
    )
    revision = await store.record_revision(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        proposal_id=proposal,
        revision_no=1,
        hash_input=ApprovalHashInput(
            organization=tenant["organization"],
            workspace=tenant["workspace"],
            assignment_version=uuid.uuid4(),
            account=world.account_id,
            platform="REDDIT",
            action_type=SemanticCapability.SUBMIT_POST,
            target_external_id="r/SaaS",
            target_url="https://www.reddit.com/r/SaaS/",
            source_fingerprint=f"session:{uuid.uuid4()}",
            text_and_media=join_own_post("Tytuł od agenta", "Treść od agenta."),
            intelligence_profile_version=uuid.uuid4(),
            plan_version=world.project_id,
            memory_snapshot=uuid.uuid4(),
            adapter_version="wenext-session-draft/1",
        ),
        state=ActionProposalState.READY_FOR_APPROVAL,
        expires_at=datetime.now(UTC) + timedelta(hours=6),
    )
    shown = (await world.registry.call("room.show", {}))["szkice"][0]
    assert shown["rodzaj"] == "OWN_POST" and shown["tytul"] == "Tytuł od agenta"
    assert shown["tekst"] == "Treść od agenta."

    revised = await world.registry.call(
        "room.revise_draft", {"revision_id": str(revision.id), "text": "Moja treść posta."}
    )

    assert revised["stan"] == "WYKONANE", revised
    new = revised["szkic"]
    assert new["rodzaj"] == "OWN_POST" and new["tytul"] == "Tytuł od agenta"
    assert new["tekst"] == "Moja treść posta."
    stored = _j((await _revisions(pool, world))[-1]["payload"])["text_and_media"]
    assert stored == join_own_post("Tytuł od agenta", "Moja treść posta.")


# ---------------------------------------------------------------------------
# room.pause: a pause that actually pauses, behind a click
# ---------------------------------------------------------------------------


async def test_a_pause_without_a_token_or_with_a_bad_one_changes_nothing(pool, tenant) -> None:
    world = await _world(pool, tenant)
    signed = await _sign(pool, tenant, world)
    for args in ({}, {"token": ""}, {"token": "zgadnięty-przez-model"}):
        refused = await world.registry.call("room.pause", args)
        assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == "POTWIERDZENIE", refused
    row = await _live(pool, world)
    assert row["id"] == signed.id and row["paused_at"] is None
    assert await _audit(pool, "PROJECT_DELEGATION_PAUSED", world) == []

    # A token for one act is no token for another: a pause token cannot resume or sign.
    answer, token = await _ask(world, "wstrzymaj")
    assert answer["stan"] == "WYKONANE" and token, answer
    for tool in ("room.resume", "room.sign_scope"):
        refused = await world.registry.call(tool, {"token": token})
        assert refused["kod"] == "POTWIERDZENIE", (tool, refused)
    # ... and showing it to the wrong tool spent it.
    spent = await world.registry.call("room.pause", {"token": token})
    assert spent["kod"] == "POTWIERDZENIE"
    assert (await _live(pool, world))["paused_at"] is None


async def test_a_pause_pauses_the_signature_stops_sessions_and_refuses_the_next_send(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    signed = await _sign(pool, tenant, world)
    draft = await _drafted(world)
    running = await SessionStore(pool).create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=world.project_id,
        goal="praca w tle",
        budget={},
        kind=SESSION_PRESENCE,
    )

    answer, token = await _ask(world, "wstrzymaj")
    assert answer["zakres"]["rodzaj"] == "WSTRZYMAJ" and answer["zakres"]["tryb"] == "REVIEW"
    assert answer["zakres"]["konta"] == [{"platforma": "REDDIT", "konto": world.login}]
    assert answer["zakres"]["limity"] == {"godzina": 6, "doba": 6, "wlasne_doba": 1}
    assert token and token not in json.dumps({k: v for k, v in answer.items() if k != "_mcp_meta"})
    assert (await _live(pool, world))["paused_at"] is None, "pytanie nic nie wstrzymało"

    done = await world.registry.call("room.pause", {"token": token})

    assert done["stan"] == "WYKONANE" and done["zmiana"] == "WSTRZYMANO", done
    assert done["zakres"]["stan"] == "WSTRZYMANY"
    row = await _live(pool, world)
    assert row["id"] == signed.id and row["paused_at"] is not None, "ten sam podpis, wstrzymany"
    current = await PresenceStore(pool).delegation_for(
        organization_id=tenant["organization"], project_id=world.project_id
    )
    assert current is not None and current.live is False and current.state == "wstrzymana"
    audits = await _audit(pool, "PROJECT_DELEGATION_PAUSED", world)
    assert [(a["actor_ref"], a["subject_ref"]) for a in audits] == [
        ("plugin:codex", f"delegation:{signed.id}")
    ]
    async with pool.acquire() as conn:
        interrupts = await conn.fetchval(
            "SELECT count(*) FROM session_events WHERE session_id = $1 AND kind = $2",
            running.id,
            EVENT_INTERRUPT,
        )
    assert interrupts == 1, "sesja w tle dostała polecenie zatrzymania"

    # The next send is refused, and a spent token cannot pause again.
    before = await _counts(pool, world)
    minted, no_token = await _token(world, draft)
    assert minted["stan"] == "NIEDOSTEPNE" and minted["kod"] == "ZAKRES_WSTRZYMANY" and not no_token
    assert (await world.registry.call("room.pause", {"token": token}))["kod"] == "POTWIERDZENIE"
    assert await _counts(pool, world) == before
    # Pausing what is paused is refused at the question.
    again, nothing = await _ask(world, "wstrzymaj")
    assert again["kod"] == "NIE_AKTYWNY" and nothing is None
    shown = await world.registry.call("room.show", {})
    assert shown["zakres"]["stan"] == "WSTRZYMANY"


async def test_a_pause_asked_for_a_project_with_no_scope_is_refused_at_the_question(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    answer, token = await _ask(world, "wstrzymaj")
    assert answer["stan"] == "NIEDOSTEPNE" and answer["kod"] == "BRAK_ZAKRESU" and token is None


# ---------------------------------------------------------------------------
# room.resume: only the dedicated click lifts a pause, on the same signature
# ---------------------------------------------------------------------------


async def _paused(pool, tenant, world: World, **sign) -> object:
    signed = await _sign(pool, tenant, world, **sign)
    await PresenceStore(pool).set_paused(
        signed.id, organization_id=tenant["organization"], paused=True, at=datetime.now(UTC)
    )
    return signed


async def test_resume_needs_the_token_and_brings_back_the_same_signature(pool, tenant) -> None:
    world = await _world(pool, tenant)
    signed = await _paused(pool, tenant, world)
    for args in ({}, {"token": "zgadnięty"}):
        refused = await world.registry.call("room.resume", args)
        assert refused["kod"] == "POTWIERDZENIE", refused
    assert (await _live(pool, world))["paused_at"] is not None

    answer, token = await _ask(world, "wznow")
    assert answer["stan"] == "WYKONANE" and token, answer
    assert answer["zakres"]["rodzaj"] == "WZNOW" and answer["zakres"]["tryb"] == "REVIEW"
    assert answer["zakres"]["poprzedni_tryb"] == "REVIEW"
    assert (await _live(pool, world))["paused_at"] is not None, "pytanie nie wznowiło"

    done = await world.registry.call("room.resume", {"token": token})

    assert done["stan"] == "WYKONANE" and done["zmiana"] == "WZNOWIONO"
    assert done["zakres"]["stan"] == "AKTYWNY"
    assert len(await _delegations(pool, world)) == 1, "nikt niczego nie podpisał od nowa"
    row = await _live(pool, world)
    assert row["id"] == signed.id and row["paused_at"] is None
    assert row["scope_sha256"] == signed.scope_sha256, "ten sam odcisk: ten sam podpis"
    assert [a["actor_ref"] for a in await _audit(pool, "PROJECT_DELEGATION_RESUMED", world)] == [
        "plugin:codex"
    ]
    assert (await world.registry.call("room.resume", {"token": token}))["kod"] == "POTWIERDZENIE"
    # A draft can be sent again.
    draft = await _drafted(world)
    assert (await _click(world, draft))["stan"] == "OCZEKUJE"


async def test_an_autonomous_scope_comes_back_autonomous_and_the_sentence_says_so(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    await _paused(pool, tenant, world, mode="AUTONOMOUS")
    answer, token = await _ask(world, "wznow")
    assert token and answer["zakres"]["tryb"] == "AUTONOMOUS"
    assert (await world.registry.call("room.resume", {"token": token}))["tryb"] == "AUTONOMOUS"
    assert (await _live(pool, world))["mode"] == "AUTONOMOUS", "tryb się nie zmienił"


async def test_an_expired_scope_cannot_be_resumed_and_neither_can_one_that_is_not_paused(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    signed = await _sign(pool, tenant, world)
    answer, token = await _ask(world, "wznow")
    assert answer["kod"] == "NIE_WSTRZYMANY" and token is None

    await PresenceStore(pool).set_paused(
        signed.id, organization_id=tenant["organization"], paused=True, at=datetime.now(UTC)
    )
    await _expire(pool, signed.id)
    answer, token = await _ask(world, "wznow")
    assert answer["stan"] == "NIEDOSTEPNE" and answer["kod"] == "ZAKRES_WYGASL" and token is None
    assert (await _live(pool, world))["paused_at"] is not None
    assert (await world.registry.call("room.show", {}))["zakres"]["stan"] == "WYGASL"


async def test_a_resume_token_is_void_when_the_scope_was_replaced_after_the_sentence(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    old = await _paused(pool, tenant, world)
    _, token = await _ask(world, "wznow")
    assert token
    async with pool.acquire() as conn:  # the person revoked it elsewhere and signed another
        await conn.execute(
            "UPDATE project_delegations SET revoked_at = now() WHERE id = $1", old.id
        )
    fresh = await _sign(pool, tenant, world)
    refused = await world.registry.call("room.resume", {"token": token})
    assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] in {
        "ZAKRES_ZMIENIONY",
        "NIE_WSTRZYMANY",
    }
    assert (await _live(pool, world))["id"] == fresh.id and (await _live(pool, world))[
        "paused_at"
    ] is None


async def test_a_send_click_never_lifts_a_pause_and_signing_over_it_is_refused(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    await _paused(pool, tenant, world)
    draft = await _drafted(world)
    minted, token = await _token(world, draft)
    assert minted["kod"] == "ZAKRES_WSTRZYMANY" and token is None
    answer, token = await _ask(
        world,
        "podpisz",
        konta=[str(world.account_id)],
        dzialania=["REPLY"],
        limity={"godzina": 2, "doba": 10, "wlasne_doba": 1},
        dni=7,
    )
    assert answer["kod"] == "ZAKRES_WSTRZYMANY" and token is None, "nowy podpis nie zdejmuje stopu"
    assert "Wznów" in answer["powod"]
    assert (await _live(pool, world))["paused_at"] is not None


# ---------------------------------------------------------------------------
# room.sign_scope: the Limits panel
# ---------------------------------------------------------------------------

PANEL = {"limity": {"godzina": 4, "doba": 20, "wlasne_doba": 2}, "dni": 14}


async def _panel_args(world: World, **changes) -> dict:
    return {
        "konta": [str(world.account_id)],
        "dzialania": ["REPLY", "OWN_POST", "RESEARCH"],
        **PANEL,
        **changes,
    }


async def test_the_panel_signs_exactly_what_was_shown_and_only_after_the_click(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    x_id, x_login = await _account(pool, tenant, world.project_id, "X")
    args = await _panel_args(world, konta=[str(world.account_id), str(x_id)])

    answer, token = await _ask(world, "podpisz", **args)

    assert answer["stan"] == "WYKONANE" and token, answer
    shown = answer["zakres"]
    assert (
        shown["rodzaj"] == "USTAW" and shown["tryb"] == "REVIEW" and shown["poprzedni_tryb"] is None
    )
    assert shown["limity"] == {"godzina": 4, "doba": 20, "wlasne_doba": 2}
    assert shown["dzialania"] == ["OWN_POST", "REPLY", "RESEARCH"]
    assert {a["konto"] for a in shown["konta"]} == {world.login, x_login}
    assert token not in json.dumps({k: v for k, v in answer.items() if k != "_mcp_meta"})
    assert await _delegations(pool, world) == [], "pytanie niczego nie podpisało"

    # Whatever the card sends back next, only the token counts: extra arguments are ignored.
    foreign, _ = await _account(pool, tenant, (await _world(pool, tenant)).project_id, "REDDIT")
    done = await world.registry.call(
        "room.sign_scope", {"token": token, "konta": [str(foreign)], "limity": {"doba": 50}}
    )

    assert (
        done["stan"] == "WYKONANE" and done["zmiana"] == "USTAWIONO" and done["podpisano"] is True
    )
    row = await _live(pool, world)
    assert row["mode"] == "REVIEW" and row["revoked_at"] is None
    assert row["signed_by"] == tenant["operator"]
    assert set(_j(row["scope"])["accounts"]) == {str(world.account_id), str(x_id)}
    assert _j(row["scope"])["actions"] == ["OWN_POST", "REPLY", "RESEARCH"]
    assert _j(row["scope"])["communities"] == []
    assert _j(row["budget"]) == {
        "publications_per_day": 20,
        "publications_per_hour": 4,
        "own_posts_per_day": 2,
    }
    assert abs(row["expires_at"] - (datetime.now(UTC) + timedelta(days=14))) < timedelta(minutes=1)
    assert row["scope_sha256"] == digest(
        mode="REVIEW",
        scope=Scope(
            accounts=tuple(sorted((world.account_id, x_id), key=str)),
            communities=(),
            actions=("OWN_POST", "REPLY", "RESEARCH"),
        ),
        budget=Budget(publications_per_day=20, publications_per_hour=4, own_posts_per_day=2),
        expires_at=row["expires_at"],
    ), "ten sam odcisk, co z formularza"
    audits = await _audit(pool, "PROJECT_DELEGATION_SIGNED", world)
    assert [a["actor_ref"] for a in audits] == ["plugin:codex"]
    assert done["zakres"]["stan"] == "AKTYWNY"
    assert done["zakres"]["limity"] == {"godzina": 4, "doba": 20, "wlasne_doba": 2}
    assert (await world.registry.call("room.sign_scope", {"token": token}))[
        "kod"
    ] == "POTWIERDZENIE"
    assert len(await _delegations(pool, world)) == 1


async def test_a_new_scope_replaces_the_old_one_keeps_its_communities_and_is_review(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    old = await _sign(pool, tenant, world, mode="AUTONOMOUS")
    account_b, login_b = await _account(pool, tenant, world.project_id, "REDDIT")
    args = await _panel_args(world, konta=[str(world.account_id), str(account_b)], dni=7)
    answer, token = await _ask(world, "podpisz", **args)
    assert answer["zakres"]["poprzedni_tryb"] == "AUTONOMOUS", "karta powie, że tryb się zmienia"

    done = await world.registry.call("room.sign_scope", {"token": token})

    assert done["stan"] == "WYKONANE", done
    rows = await _delegations(pool, world)
    assert [r["revoked_at"] is not None for r in rows] == [True, False]
    live = await _live(pool, world)
    assert live["id"] != old.id and live["mode"] == "REVIEW"
    assert _j(live["scope"])["communities"] == ["r/SaaS"], "społeczności starego podpisu zostają"
    assert set(_j(live["scope"])["accounts"]) == {str(world.account_id), str(account_b)}
    assert abs(live["expires_at"] - (datetime.now(UTC) + timedelta(days=7))) < timedelta(minutes=1)
    assert login_b in {a["konto"] for a in done["zakres"]["konta"]}


@pytest.mark.parametrize(
    "changes",
    [{"konta": []}, {"dzialania": []}, {"konta": [], "dzialania": []}],
)
async def test_an_empty_scope_is_refused_at_the_question_and_nothing_is_signed(
    pool, tenant, changes
) -> None:
    world = await _world(pool, tenant)
    answer, token = await _ask(world, "podpisz", **(await _panel_args(world, **changes)))
    assert answer["stan"] == "NIEDOSTEPNE" and answer["kod"] == "ZAKRES_PUSTY" and token is None
    assert answer["powod"]
    assert await _delegations(pool, world) == []


async def test_an_account_the_project_does_not_have_or_a_stopped_one_is_refused(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    other = await _world(pool, tenant)  # another project's account
    stopped_id, _ = await _account(pool, tenant, world.project_id, "X")
    async with pool.acquire() as conn:
        await conn.execute("UPDATE social_accounts SET state = 'PAUSED' WHERE id = $1", stopped_id)
    for foreign in (other.account_id, stopped_id, uuid.uuid4()):
        answer, token = await _ask(
            world,
            "podpisz",
            **(await _panel_args(world, konta=[str(world.account_id), str(foreign)])),
        )
        assert answer["kod"] == "KONTO_NIEZNANE" and token is None, (foreign, answer)
    bad_id, _ = await _ask(world, "podpisz", **(await _panel_args(world, konta=["nie-id"])))
    assert bad_id["kod"] == "ARGUMENT"
    assert await _delegations(pool, world) == []


@pytest.mark.parametrize(
    "limits",
    [
        {"godzina": 0, "doba": 10, "wlasne_doba": 1},
        {"godzina": 11, "doba": 20, "wlasne_doba": 1},
        {"godzina": 2, "doba": 51, "wlasne_doba": 1},
        {"godzina": 6, "doba": 5, "wlasne_doba": 1},
    ],
)
async def test_limits_outside_their_bounds_are_refused(pool, tenant, limits) -> None:
    world = await _world(pool, tenant)
    answer, token = await _ask(world, "podpisz", **(await _panel_args(world, limity=limits)))
    assert answer["kod"] == "LIMIT_POZA_GRANICA" and token is None
    bad_term, _ = await _ask(world, "podpisz", **(await _panel_args(world, dni=30)))
    assert bad_term["kod"] == "LIMIT_POZA_GRANICA"
    assert await _delegations(pool, world) == []


async def test_a_scope_is_not_replaced_while_a_request_waits_in_the_publisher_queue(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    await _sign(pool, tenant, world)
    draft = await _drafted(world)
    assert (await _click(world, draft))["stan"] == "OCZEKUJE"  # waits in the queue
    before = await _counts(pool, world)

    answer, token = await _ask(world, "podpisz", **(await _panel_args(world)))
    assert answer["kod"] == "ZAKRES_KOLEJKA" and token is None
    assert await _counts(pool, world) == before

    await _let_the_queue_go(pool, world)
    answer, token = await _ask(world, "podpisz", **(await _panel_args(world)))
    assert answer["stan"] == "WYKONANE" and token


async def test_a_queue_that_fills_between_the_question_and_the_yes_stops_the_signature(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    await _sign(pool, tenant, world)
    _, token = await _ask(world, "podpisz", **(await _panel_args(world)))
    assert token
    draft = await _drafted(world)
    assert (await _click(world, draft))["stan"] == "OCZEKUJE"
    refused = await world.registry.call("room.sign_scope", {"token": token})
    assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == "ZAKRES_KOLEJKA"
    assert len(await _delegations(pool, world)) == 1


async def test_a_scope_that_moved_after_the_sentence_is_a_different_sentence(pool, tenant) -> None:
    world = await _world(pool, tenant)
    old = await _sign(pool, tenant, world)
    _, token = await _ask(world, "podpisz", **(await _panel_args(world)))
    assert token
    # Another click (the dashboard) revoked it and signed a different one in the meantime.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE project_delegations SET revoked_at = now() WHERE id = $1", old.id
        )
    other = await _sign(pool, tenant, world, budget=Budget(publications_per_hour=1))
    refused = await world.registry.call("room.sign_scope", {"token": token})
    assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == "ZAKRES_ZMIENIONY"
    assert (await _live(pool, world))["id"] == other.id
    assert len(await _delegations(pool, world)) == 2


async def test_an_account_unassigned_between_the_question_and_the_yes_is_not_signed(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    account_b, _ = await _account(pool, tenant, world.project_id, "REDDIT")
    _, token = await _ask(
        world,
        "podpisz",
        **(await _panel_args(world, konta=[str(world.account_id), str(account_b)])),
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE project_account_assignments SET unassigned_at = now() "
            "WHERE project_id = $1 AND social_account_id = $2",
            world.project_id,
            account_b,
        )
    refused = await world.registry.call("room.sign_scope", {"token": token})
    assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] in {
        "KONTO_NIEZNANE",
        "ZAKRES_ZMIENIONY",
    }
    assert await _delegations(pool, world) == []


async def test_signing_without_a_valid_token_changes_nothing(pool, tenant) -> None:
    world = await _world(pool, tenant)
    for args in ({}, {"token": ""}, {"token": "zgadnięty-przez-model"}):
        refused = await world.registry.call("room.sign_scope", args)
        assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == "POTWIERDZENIE", refused

    clock = [datetime.now(UTC)]
    world.registry._scope_tokens = ScopeTokens(clock=lambda: clock[0])
    _, token = await _ask(world, "podpisz", **(await _panel_args(world)))
    clock[0] += timedelta(minutes=4)
    late = await world.registry.call("room.sign_scope", {"token": token})
    assert late["kod"] == "POTWIERDZENIE"

    _, fresh = await _ask(world, "podpisz", **(await _panel_args(world)))
    _, newer = await _ask(world, "podpisz", **(await _panel_args(world)))  # kills `fresh`
    assert (await world.registry.call("room.sign_scope", {"token": fresh}))[
        "kod"
    ] == "POTWIERDZENIE"
    assert await _delegations(pool, world) == []
    ok = await world.registry.call("room.sign_scope", {"token": newer})
    assert ok["stan"] == "WYKONANE" and len(await _delegations(pool, world)) == 1


async def test_a_spent_pace_is_raised_in_the_card_and_the_send_then_goes(pool, tenant) -> None:
    """The founder's day, replayed: the hour is spent, the send is refused (BUDZET), the limits are
    raised in the chat, and the same draft is sent. The queue is empty (the first reply was
    published), so signing is allowed."""
    world = await _world(pool, tenant)
    await _sign(pool, tenant, world, budget=Budget(publications_per_hour=1, publications_per_day=1))
    first = await _drafted(world, post="aaa111", text="Pierwsza.")
    sent = await _click(world, first)
    assert sent["stan"] == "OCZEKUJE", sent
    await _mark_published(pool, tenant, uuid.UUID(sent["wniosek"]))

    second = await _drafted(world, post="bbb222", text="Druga.")
    minted, token = await _token(world, second)
    assert minted["stan"] == "WYKONANE" and token
    spent = await _send(world, second, token)
    assert spent["stan"] == "NIEDOSTEPNE" and spent["kod"] == "BUDZET", spent
    assert await _state(pool, second["revision"]) == "READY_FOR_APPROVAL"

    answer, panel_token = await _ask(world, "podpisz", **(await _panel_args(world)))
    assert answer["stan"] == "WYKONANE" and panel_token, answer
    assert (await world.registry.call("room.sign_scope", {"token": panel_token}))[
        "stan"
    ] == "WYKONANE"
    assert (await world.registry.call("room.show", {}))["zakres"]["limity"] == {
        "godzina": 4,
        "doba": 20,
        "wlasne_doba": 2,
    }
    again = await _click(world, second)
    assert again["stan"] == "OCZEKUJE", again
    assert await _scope_accounts(pool, world) == {str(world.account_id)}


# ---------------------------------------------------------------------------
# The dashboard's sign route refuses an empty scope too
# ---------------------------------------------------------------------------


async def _post_scope(client, tenant, world: World, **body) -> int:
    base = {
        "mode": "REVIEW",
        "accounts": [str(world.account_id)],
        "communities": ["r/SaaS"],
        "actions": ["REPLY"],
    }
    response = await client.post(
        f"/api/workspaces/{tenant['workspace']}/projects/{world.project_id}/delegation",
        json={**base, **body},
    )
    return response.status_code, response.json() if response.content else {}


async def test_the_dashboard_form_refuses_an_empty_scope_and_still_signs_a_real_one(
    pool, tenant, logged_in_client
) -> None:
    world = await _world(pool, tenant)
    for body in (
        {"actions": []},
        {"accounts": [], "actions": ["REPLY"]},
        {"accounts": [], "actions": ["OWN_POST"]},
        {"accounts": [], "actions": []},
        {"mode": "AUTONOMOUS", "accounts": [], "actions": []},
    ):
        status, payload = await _post_scope(logged_in_client, tenant, world, **body)
        assert status == 422, (body, status, payload)
        assert "zakres" in json.dumps(payload, ensure_ascii=False)
    assert await _delegations(pool, world) == [], "pusty zakres nie został zapisany"

    # A RESEARCH-only scope names no account on purpose and stays valid; so does a normal one.
    status, _ = await _post_scope(
        logged_in_client,
        tenant,
        world,
        mode="RESEARCH",
        accounts=[],
        communities=[],
        actions=["RESEARCH"],
    )
    assert status == 201
    live = await _live(pool, world)
    status, _ = await _post_scope(
        logged_in_client,
        tenant,
        world,
        replaces=str(live["id"]),
        actions=["REPLY", "OWN_POST"],
        mode="REVIEW",
    )
    assert status in {201, 409}, "formularz działa jak dotąd (OWN_POST może wymagać społeczności)"


# ---------------------------------------------------------------------------
# room.show: the skipped conversations and the scope
# ---------------------------------------------------------------------------


async def test_the_agent_skips_with_a_reason_and_the_card_gets_it(pool, tenant) -> None:
    world = await _world(pool, tenant)
    marked = await _mark(
        world,
        **_reddit_args("skp001", pomin=True, powod="Wątek zamknięty, nikt tu już nie czyta."),
    )
    assert marked["okazja"]["stan"] == "POMINIETA"
    assert marked["okazja"]["dlaczego"] == "Wątek zamknięty, nikt tu już nie czyta."
    assert marked["uwaga"] == "Okazja pominięta: znika z Pokoju."

    shown = await world.registry.call("room.show", {})
    assert shown["okazje"] == [], "pominięta nie wraca na listę do odpowiedzi"
    assert shown["pominiete_lacznie"] == 1
    (skipped,) = shown["pominiete"]
    assert set(skipped) == {
        "id", "platforma", "spolecznosc", "tytul", "url", "pominieto", "przez", "powod", "powod_kod",
    }  # fmt: skip
    assert skipped["przez"] == "agent" and skipped["powod_kod"] is None, (
        "the agent gave its own words"
    )
    assert skipped["id"] == marked["okazja"]["id"] and skipped["platforma"] == "REDDIT"
    assert (
        skipped["spolecznosc"] == "r/SaaS" and skipped["tytul"] == "How do you handle X? (skp001)"
    )
    assert skipped["url"].startswith("https://www.reddit.com/r/SaaS/comments/skp001/")
    assert (
        skipped["przez"] == "agent"
        and skipped["powod"] == "Wątek zamknięty, nikt tu już nie czyta."
    )
    assert datetime.fromisoformat(skipped["pominieto"]) <= datetime.now(UTC)


async def test_a_skip_needs_a_reason_and_a_reason_needs_a_skip(pool, tenant) -> None:
    world = await _world(pool, tenant)
    plain = _reddit_args("skp002")
    plain.pop("dlaczego")
    nothing = await world.registry.call("opportunities.mark", {**plain, "pomin": True})
    assert nothing["stan"] == "BLAD" and "powod" in nothing["powod"]
    only_why = await world.registry.call(
        "opportunities.mark", {**plain, "pomin": True, "dlaczego": "Stary sposób, nadal działa."}
    )
    assert only_why["stan"] == "WYKONANE" and only_why["okazja"]["stan"] == "POMINIETA"
    # A reason without a skip is not a thing: the sentence of an open opportunity is `dlaczego`.
    stray = await world.registry.call(
        "opportunities.mark", {**_reddit_args("skp003"), "powod": "Bez pominięcia."}
    )
    assert stray["stan"] == "BLAD" and "pomin=true" in stray["powod"]
    # `dlaczego` is still required for an opportunity that is not skipped.
    missing = _reddit_args("skp004")
    missing.pop("dlaczego")
    assert (await world.registry.call("opportunities.mark", missing))["stan"] == "BLAD"
    # The reason is clean text: a long dash is rewritten, like every sentence the agent writes.
    dashed = await world.registry.call(
        "opportunities.mark",
        {**_reddit_args("skp005"), "pomin": True, "powod": f"Zamknięty {LONG_DASH} nie ma po co."},
    )
    assert LONG_DASH not in dashed["okazja"]["dlaczego"] and dashed["przepisane_dlugie_pauzy"] == 1


async def test_a_refresh_does_not_overwrite_the_reason_of_a_skip_and_a_second_skip_does(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    args = _reddit_args("skp006", pomin=True, powod="Pierwszy powód.")
    first = await _mark(world, **args)
    refreshed = await _mark(world, **_reddit_args("skp006", score=77, dlaczego="Jednak ciekawy."))
    assert refreshed["okazja"]["stan"] == "POMINIETA" and refreshed["okazja"]["wynik"] == 77
    assert refreshed["okazja"]["dlaczego"] == "Pierwszy powód.", "odświeżenie nie ruszyło powodu"
    again = await _mark(world, **{**args, "powod": "Drugi, dokładniejszy powód."})
    assert again["okazja"]["dlaczego"] == "Drugi, dokładniejszy powód."
    shown = (await world.registry.call("room.show", {}))["pominiete"]
    assert [one["powod"] for one in shown] == ["Drugi, dokładniejszy powód."]
    async with pool.acquire() as conn:
        author = await conn.fetchval(
            "SELECT dismissed_by FROM project_opportunities WHERE id = $1",
            uuid.UUID(first["okazja"]["id"]),
        )
    assert author == f"session:{world.session_id}", "autor pierwszej decyzji zostaje"


async def test_a_skip_made_on_the_page_has_no_reason_and_says_who_skipped(pool, tenant) -> None:
    world = await _world(pool, tenant)
    marked = await _mark(world, **_reddit_args("skp007"))
    await OpportunityStore(pool).dismiss(
        organization_id=tenant["organization"],
        project_id=world.project_id,
        opportunity_id=uuid.UUID(marked["okazja"]["id"]),
        by="operator:test",
    )
    (skipped,) = (await world.registry.call("room.show", {}))["pominiete"]
    assert skipped["przez"] == "czlowiek" and skipped["powod"] is None, (
        "karta nie zmyśla powodu, którego nikt nie podał"
    )
    refreshed = await _mark(world, **_reddit_args("skp007", dlaczego="Nowe zdanie agenta."))
    (still,) = (await world.registry.call("room.show", {}))["pominiete"]
    assert still["przez"] == "czlowiek" and still["powod"] is None
    assert refreshed["okazja"]["stan"] == "POMINIETA"


async def test_the_skipped_list_is_newest_first_capped_and_counted(pool, tenant) -> None:
    world = await _world(pool, tenant)
    for number in range(23):
        await _mark(
            world, **_reddit_args(f"sk{number:02d}", pomin=True, powod=f"Powód numer {number}.")
        )
    shown = await world.registry.call("room.show", {})
    assert shown["pominiete_lacznie"] == 23 and len(shown["pominiete"]) == 20
    assert [one["powod"] for one in shown["pominiete"][:3]] == [
        "Powód numer 22.",
        "Powód numer 21.",
        "Powód numer 20.",
    ]
    other = await _world(pool, tenant)
    assert (await other.registry.call("room.show", {}))["pominiete"] == [], "tylko własny projekt"


async def test_a_hosted_session_gets_the_skipped_and_no_scope(pool, tenant) -> None:
    world = await _world(pool, tenant)
    await _mark(world, **_reddit_args("hos001", pomin=True, powod="Nie ta osoba."))
    hosted = _registry_like(world, hosted=True)
    shown = await hosted.call("room.show", {})
    assert shown["stan"] == "WYKONANE" and shown["wysylka"] == "reczna"
    assert "zakres" not in shown, "hosted nie ma zakresu: nic nie wychodzi z Wenext"
    assert shown["pominiete"][0]["powod"] == "Nie ta osoba."
    local = await world.registry.call("room.show", {})
    assert "zakres" in local and local["wysylka"] == "karta"


async def test_the_card_gets_the_scope_in_every_state(pool, tenant) -> None:
    world = await _world(pool, tenant)
    x_id, x_login = await _account(pool, tenant, world.project_id, "X")
    stopped_id, _ = await _account(pool, tenant, world.project_id, "REDDIT")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE social_accounts SET state = 'QUARANTINED' WHERE id = $1", stopped_id
        )

    none = (await world.registry.call("room.show", {}))["zakres"]
    assert none["stan"] == "BRAK" and none["tryb"] is None and none["wygasa"] is None
    assert none["konta"] == [] and none["dzialania"] == []
    assert none["limity"] == {"godzina": 1, "doba": 6, "wlasne_doba": 1}, "domyślne produktu"
    assert none["domyslne"] == {"godzina": 2, "doba": 10, "wlasne_doba": 1, "dni": 7}
    assert none["granice"] == {"godzina": 10, "doba": 50, "wlasne_doba": 10}
    choices = {c["id"]: c for c in none["do_wyboru"]}
    assert set(choices) == {str(world.account_id), str(x_id)}, "zatrzymane konto nie jest do wyboru"
    assert choices[str(x_id)] == {
        "id": str(x_id),
        "platforma": "X",
        "konto": x_login,
        "dzialania": ["REPLY", "OWN_POST"],
        "w_zakresie": False,
    }

    signed = await _sign(pool, tenant, world)
    active = (await world.registry.call("room.show", {}))["zakres"]
    assert active["stan"] == "AKTYWNY" and active["tryb"] == "REVIEW"
    assert active["konta"] == [{"platforma": "REDDIT", "konto": world.login}]
    assert active["dzialania"] == ["REPLY"]
    assert active["limity"] == {"godzina": 6, "doba": 6, "wlasne_doba": 1}
    assert datetime.fromisoformat(active["wygasa"]) == signed.expires_at
    assert {c["id"]: c["w_zakresie"] for c in active["do_wyboru"]} == {
        str(world.account_id): True,
        str(x_id): False,
    }

    await PresenceStore(pool).set_paused(
        signed.id, organization_id=tenant["organization"], paused=True, at=datetime.now(UTC)
    )
    assert (await world.registry.call("room.show", {}))["zakres"]["stan"] == "WSTRZYMANY"
    await _expire(pool, signed.id)
    assert (await world.registry.call("room.show", {}))["zakres"]["stan"] == "WYGASL"
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE project_delegations SET revoked_at = now() WHERE id = $1", signed.id
        )
    assert (await world.registry.call("room.show", {}))["zakres"]["stan"] == "BRAK"
    # Reading changed nothing.
    assert len(await _delegations(pool, world)) == 1


async def test_room_show_stays_a_read_with_the_new_keys(pool, tenant) -> None:
    world = await _world(pool, tenant)
    await _drafted(world)
    before = await _counts(pool, world)
    first = await world.registry.call("room.show", {})
    assert await world.registry.call("room.show", {}) == first
    assert await _counts(pool, world) == before
    # The page's own payload is untouched: the new keys exist only on the tool's answer.
    from control_plane.opportunities.room import room_view

    page = await room_view(
        pool,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=world.project_id,
    )
    assert set(page) == {"okazje", "szkice", "wyniki", "pokoj"}
    assert META_TOKEN not in json.dumps(first)
