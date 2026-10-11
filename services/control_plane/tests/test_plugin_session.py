"""The conversation a plugin host (Codex, Claude Code) works in, against a real database.

WHAT THESE TESTS HOLD, each one a way the plugin could go wrong quietly:

  * ONE LIVE SESSION PER PROJECT AND HOST, not one per start. A plugin server starts every time
    the person opens their agent; a session per start would bury the project's conversation list
    in empty rows. Rotation (a day, six hundred events, a terminal state) is as deliberate as the
    reuse, and two windows opened at the same instant must still agree on one row.
  * THE SEED IS NOT A `USER_MESSAGE`. That event is what `memory.write` accepts as "the person
    said so"; a host agent citing seq 1 would put its own words in the founder's mouth.
  * NOBODY RUNS A SECOND MODEL ON IT. The routes that would start a turn answer 409 with a
    sentence that says where to write, and the turn handler, if a job ever reached it, gives the
    session back and runs nothing.
  * THE LISTS OF PREVIOUS CONVERSATIONS do not teach a Wenext-run model that "Wtyczka: praca w
    Twoim agencie" was a conversation somebody had.
  * A PERSON'S APPROVAL PUBLISHES NOW, with the nudge the delegated path already had.
"""

from __future__ import annotations

import asyncio
import json
import re
from uuid import UUID, uuid4

import pytest
from community_os_contracts.enums import SemanticCapability
from community_os_contracts.execution import ActionProposalState, ApprovalHashInput
from control_plane.approval.store import ApprovalStore
from control_plane.sessions import (
    EVENT_STATUS,
    EVENT_USER_MESSAGE,
    STATUS_DONE,
    STATUS_QUEUED,
    STATUS_WAITING_USER,
    SessionStore,
    host_of,
)
from control_plane.sessions.context import starting_context
from control_plane.sessions.handlers import OUTCOME_STOPPED, SessionTurnHandlers
from control_plane.tools import ToolRegistry, issue
from control_plane.tools.plugin_server import (
    HOST_LABELS,
    PLUGIN_GOAL,
    ProjectChoice,
    list_projects,
    open_session,
    record_audit,
)

pytestmark = [pytest.mark.db, pytest.mark.p2]

DSN_PLACEHOLDER = "postgresql://nieuzywane-w-tescie"
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
REFUSAL = (
    "Ta rozmowa jest prowadzona w Twoim agencie (Codex albo Claude Code). Napisz tam; "
    "tutaj zatwierdzasz szkice."
)


async def _project(pool, tenant, *, name: str | None = None) -> ProjectChoice:
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel projektu') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            name or f"projekt-{uuid4().hex[:8]}",
        )
    return ProjectChoice(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        name=name or "projekt",
        workspace_name="ws",
    )


async def _events(pool, session_id: UUID) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT seq, kind, payload FROM session_events WHERE session_id = $1 ORDER BY seq",
            session_id,
        )
    return [
        {
            "seq": row["seq"],
            "kind": row["kind"],
            "payload": json.loads(row["payload"])
            if isinstance(row["payload"], str)
            else row["payload"],
        }
        for row in rows
    ]


def _store_args(choice: ProjectChoice, host: str = "codex") -> dict:
    return {
        "organization_id": choice.organization_id,
        "workspace_id": choice.workspace_id,
        "project_id": choice.project_id,
        "host": host,
        "goal": PLUGIN_GOAL,
    }


# ---------------------------------------------------------------------------
# Create, reuse, rotate
# ---------------------------------------------------------------------------


async def test_the_first_start_creates_a_waiting_conversation_with_no_user_message(
    pool, tenant
) -> None:
    choice = await _project(pool, tenant)
    store = SessionStore(pool)

    session, created = await store.open_host_session(**_store_args(choice))

    assert created is True
    assert session.kind == "CONVERSATION"
    assert session.status == STATUS_WAITING_USER, "nikt nie uruchomi tury, więc nie QUEUED"
    assert session.codex_connection_id is None
    assert session.budget == {"host": "codex"} and host_of(session) == "codex"
    assert session.goal == PLUGIN_GOAL
    assert await _events(pool, session.id) == [], (
        "zalążek sesji wtyczki nie jest wypowiedzią człowieka: agent mógłby się na nią powołać"
    )


async def test_a_normal_session_has_no_host_and_keeps_its_goal_event(pool, tenant) -> None:
    """The other side of `announce_goal`: the default still writes the person's goal."""
    choice = await _project(pool, tenant)
    session = await SessionStore(pool).create(
        organization_id=choice.organization_id,
        workspace_id=choice.workspace_id,
        project_id=choice.project_id,
        goal="sprawdź odzew",
        budget={},
    )
    assert host_of(session) is None and session.status == STATUS_QUEUED
    events = await _events(pool, session.id)
    assert [e["kind"] for e in events] == [EVENT_USER_MESSAGE]
    assert events[0]["payload"]["is_goal"] is True


async def test_the_same_host_reuses_its_live_session_and_another_host_gets_its_own(
    pool, tenant
) -> None:
    choice = await _project(pool, tenant)
    store = SessionStore(pool)

    first, created_first = await store.open_host_session(**_store_args(choice))
    again, created_again = await store.open_host_session(**_store_args(choice))
    other, created_other = await store.open_host_session(**_store_args(choice, "claude-code"))

    assert (created_first, created_again, created_other) == (True, False, True)
    assert again.id == first.id
    assert other.id != first.id and host_of(other) == "claude-code"


async def test_another_project_never_shares_the_session(pool, tenant) -> None:
    store = SessionStore(pool)
    one, two = await _project(pool, tenant), await _project(pool, tenant)
    first, _ = await store.open_host_session(**_store_args(one))
    second, created = await store.open_host_session(**_store_args(two))
    assert created is True and second.id != first.id


async def test_a_session_older_than_a_day_is_rotated(pool, tenant) -> None:
    choice = await _project(pool, tenant)
    store = SessionStore(pool)
    old, _ = await store.open_host_session(**_store_args(choice))
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_sessions SET created_at = now() - interval '25 hours' WHERE id = $1",
            old.id,
        )

    fresh, created = await store.open_host_session(**_store_args(choice))

    assert created is True and fresh.id != old.id


async def test_a_session_just_under_a_day_is_still_reused(pool, tenant) -> None:
    choice = await _project(pool, tenant)
    store = SessionStore(pool)
    old, _ = await store.open_host_session(**_store_args(choice))
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_sessions SET created_at = now() - interval '23 hours' WHERE id = $1",
            old.id,
        )
    same, created = await store.open_host_session(**_store_args(choice))
    assert created is False and same.id == old.id


@pytest.mark.parametrize(("events", "reused"), [(599, True), (600, False)])
async def test_a_long_trace_rotates_the_session_at_six_hundred_events(
    pool, tenant, events: int, reused: bool
) -> None:
    choice = await _project(pool, tenant)
    store = SessionStore(pool)
    session, _ = await store.open_host_session(**_store_args(choice))
    async with pool.acquire() as conn:
        # The trace is append-only to UPDATE and DELETE, not to INSERT; one statement keeps the
        # test fast instead of six hundred round trips through `append_event`.
        await conn.execute(
            "INSERT INTO session_events (organization_id, workspace_id, session_id, seq, kind, "
            "payload) SELECT $1, $2, $3, n, 'STATUS', '{}'::jsonb FROM generate_series(1, $4) n",
            choice.organization_id,
            choice.workspace_id,
            session.id,
            events,
        )

    nxt, created = await store.open_host_session(**_store_args(choice))

    assert (created is False) is reused
    assert (nxt.id == session.id) is reused


async def test_a_finished_session_is_never_reused(pool, tenant) -> None:
    choice = await _project(pool, tenant)
    store = SessionStore(pool)
    done, _ = await store.open_host_session(**_store_args(choice))
    await store.set_status(done.id, organization_id=choice.organization_id, status=STATUS_DONE)

    fresh, created = await store.open_host_session(**_store_args(choice))

    assert created is True and fresh.id != done.id


async def test_a_wenext_run_conversation_is_not_taken_for_a_plugin_one(pool, tenant) -> None:
    """Reuse looks at the marker, not at 'the latest conversation of the project'."""
    choice = await _project(pool, tenant)
    store = SessionStore(pool)
    ordinary = await store.create(
        organization_id=choice.organization_id,
        workspace_id=choice.workspace_id,
        project_id=choice.project_id,
        goal="zwykła rozmowa",
        budget={},
        status=STATUS_WAITING_USER,
    )
    session, created = await store.open_host_session(**_store_args(choice))
    assert created is True and session.id != ordinary.id


async def test_two_windows_opened_together_agree_on_one_session(pool, tenant) -> None:
    """The advisory lock: both find nothing, both would create, and only one may."""
    choice = await _project(pool, tenant)
    store = SessionStore(pool)

    results = await asyncio.gather(*(store.open_host_session(**_store_args(choice)) for _ in range(4)))

    assert len({session.id for session, _ in results}) == 1
    assert sum(1 for _, created in results if created) == 1


# ---------------------------------------------------------------------------
# What the plugin server writes around the session
# ---------------------------------------------------------------------------


async def test_a_new_plugin_session_opens_with_a_status_and_never_a_user_message(
    pool, tenant
) -> None:
    choice = await _project(pool, tenant)

    session, created = await open_session(pool, choice, "codex")
    again, created_again = await open_session(pool, choice, "codex")

    assert created is True and created_again is False and again.id == session.id
    events = await _events(pool, session.id)
    assert [e["kind"] for e in events] == [EVENT_STATUS], "jedno zdanie na start, bez powtórek"
    detail = events[0]["payload"]["detail"]
    assert HOST_LABELS["codex"] in detail and "zatwierdzasz szkice" in detail
    assert not LONG_DASHES.search(detail)
    assert "status" not in events[0]["payload"], "to zdanie, nie zmiana stanu sesji"


async def test_the_audit_row_carries_ids_and_the_host_as_the_actor(pool, tenant) -> None:
    choice = await _project(pool, tenant)
    session, created = await open_session(pool, choice, "claude-code")

    await record_audit(pool, choice, session, "claude-code", created=created)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT kind, subject_ref, actor_ref, workspace_id, payload FROM audit_events "
            "WHERE organization_id = $1 AND kind = 'PLUGIN_SESSION_STARTED' AND subject_ref = $2",
            choice.organization_id,
            f"session:{session.id}",
        )
    assert row is not None, "wydanie zdolności agentowi bez wpisu w dzienniku"
    assert row["actor_ref"] == "plugin:claude-code"
    assert row["workspace_id"] == choice.workspace_id
    payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
    assert payload == {
        "session_id": str(session.id),
        "project_id": str(choice.project_id),
        "created": True,
    }


async def test_the_project_listing_skips_archived_ones(pool, tenant) -> None:
    kept = await _project(pool, tenant)
    gone = await _project(pool, tenant)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE projects SET archived_at = now() WHERE id = $1", gone.project_id)

    found = await list_projects(pool, str(tenant["organization"]))

    ids = {one.project_id for one in found}
    assert kept.project_id in ids and gone.project_id not in ids


# ---------------------------------------------------------------------------
# A host session cannot be told to think by the dashboard
# ---------------------------------------------------------------------------


def _routes(tenant, choice: ProjectChoice, session_id: UUID) -> str:
    return (
        f"/api/workspaces/{tenant['workspace']}/projects/{choice.project_id}/sessions/{session_id}"
    )


async def test_the_routes_that_would_start_a_turn_answer_409_for_a_host_session(
    pool, tenant, logged_in_client
) -> None:
    choice = await _project(pool, tenant)
    session, _ = await open_session(pool, choice, "codex")
    base = _routes(tenant, choice, session.id)

    for suffix, body in (
        ("/message", {"text": "dopisz coś"}),
        ("/resume", None),
        ("/continue", None),
        ("/interrupt", None),
    ):
        response = await logged_in_client.post(base + suffix, json=body)
        assert response.status_code == 409, (suffix, response.text)
        assert response.json()["detail"] == REFUSAL, suffix
        assert not LONG_DASHES.search(response.json()["detail"])

    events = await _events(pool, session.id)
    assert [e["kind"] for e in events] == [EVENT_STATUS], (
        "odmowa nie zostawiła w śladzie ani wiadomości, ani przerwania"
    )
    async with pool.acquire() as conn:
        turns = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1 AND job_type = 'session.turn'",
            choice.organization_id,
        )
        status = await conn.fetchval("SELECT status FROM agent_sessions WHERE id = $1", session.id)
        children = await conn.fetchval(
            "SELECT count(*) FROM agent_sessions WHERE parent_session_id = $1", session.id
        )
    assert turns == 0, "odmowa po cichu zleciła turę"
    assert status == STATUS_WAITING_USER
    assert children == 0, "`continue` założył rozmowę-dziecko mimo odmowy"


async def test_a_normal_session_still_takes_a_message(pool, tenant, logged_in_client) -> None:
    """The guard keys on the marker: an ordinary conversation is not caught by it."""
    choice = await _project(pool, tenant)
    ordinary = await SessionStore(pool).create(
        organization_id=choice.organization_id,
        workspace_id=choice.workspace_id,
        project_id=choice.project_id,
        goal="zwykła rozmowa",
        budget={},
    )
    response = await logged_in_client.post(
        _routes(tenant, choice, ordinary.id) + "/message", json={"text": "dopisz coś"}
    )
    assert response.status_code == 202, response.text


async def test_a_host_session_does_not_claim_to_have_a_budget_somebody_chose(
    pool, tenant, logged_in_client
) -> None:
    """`wlasny` says "this limit is the person's setting"; the marker is not a limit."""
    choice = await _project(pool, tenant)
    session, _ = await open_session(pool, choice, "codex")
    response = await logged_in_client.get(_routes(tenant, choice, session.id))
    assert response.status_code == 200, response.text
    assert response.json()["budzet"]["wlasny"] is False


async def test_the_turn_handler_gives_a_host_session_back_and_runs_no_model(pool, tenant) -> None:
    """Defence in depth: the routes refuse, but a job that reached the handler must not run a
    second model on the person's conversation."""
    choice = await _project(pool, tenant)
    session, _ = await open_session(pool, choice, "codex")
    store = SessionStore(pool)

    def _no_engine(*_args, **_kwargs):
        raise AssertionError("silnik uruchomiony dla sesji prowadzonej w agencie człowieka")

    async def _no_home(_session):
        raise AssertionError("dom Codexa wybrany dla sesji prowadzonej w agencie człowieka")

    handlers = SessionTurnHandlers(
        pool=pool,
        sessions=store,
        queue=None,  # type: ignore[arg-type]
        organization_id=choice.organization_id,
        dsn=DSN_PLACEHOLDER,
        codex_home_for=_no_home,
        client_factory=_no_engine,
    )

    result = await handlers.run_once(session.id, reason="message-1")

    assert result.outcome == OUTCOME_STOPPED, result
    assert "Model nie był uruchamiany" in result.detail
    after = await store.get(organization_id=choice.organization_id, session_id=session.id)
    assert after is not None and after.status == STATUS_WAITING_USER
    assert after.engine_thread_id is None
    kinds = [e["kind"] for e in await _events(pool, session.id)]
    assert kinds == [EVENT_STATUS, EVENT_STATUS], "start i jedno zdanie o odmowie, nic więcej"


# ---------------------------------------------------------------------------
# What the host agent may not write into memory
# ---------------------------------------------------------------------------


def _registry(pool, choice: ProjectChoice, session_id: UUID, host: str | None) -> ToolRegistry:
    return ToolRegistry(
        capability=issue(
            session_id=session_id,
            organization_id=choice.organization_id,
            workspace_id=choice.workspace_id,
            project_id=choice.project_id,
            dsn=DSN_PLACEHOLDER,
            host=host,
        ),
        pool=pool,
    )


async def test_a_host_agent_cannot_record_a_preference_even_when_it_points_at_seq_one(
    pool, tenant
) -> None:
    """THE reason the seed is a STATUS: seq 1 exists, and it is not the person speaking. The
    refusal comes with the way out and does not depend on that event check."""
    choice = await _project(pool, tenant)
    session, _ = await open_session(pool, choice, "codex")
    registry = _registry(pool, choice, session.id, "codex")

    refused = await registry.call(
        "memory.write",
        {
            "kind": "PREFERENCE",
            "statement": "Założyciel woli krótkie odpowiedzi",
            "payload": {"confirmed_at_seq": 1},
        },
    )
    assert refused["stan"] == "NIEDOSTEPNE", refused
    assert "dashboardzie" in refused["powod"] and "INTERPRETATION" in refused["wskazowka"]
    assert not LONG_DASHES.search(refused["powod"] + refused["wskazowka"])

    hypothesis = await registry.call(
        "memory.write",
        {
            "kind": "INTERPRETATION",
            "statement": "Hipoteza uzgodniona z założycielem",
            "payload": {"hypothesis": True, "confirmed_at_seq": 1},
        },
    )
    assert hypothesis["stan"] == "NIEDOSTEPNE", hypothesis

    found = await registry.call("memory.search", {})
    assert found["ustalenia"] == [], "mimo odmowy coś trafiło do pamięci"


async def test_a_host_agent_may_still_write_its_own_interpretation(pool, tenant) -> None:
    choice = await _project(pool, tenant)
    session, _ = await open_session(pool, choice, "codex")
    registry = _registry(pool, choice, session.id, "codex")

    written = await registry.call(
        "memory.write",
        {"kind": "INTERPRETATION", "statement": "Odpowiedzi pod świeżym postem dostają odzew"},
    )
    assert written["stan"] == "WYKONANE", written


async def test_the_catalog_a_host_agent_lists_is_the_narrow_one(pool, tenant) -> None:
    choice = await _project(pool, tenant)
    session, _ = await open_session(pool, choice, "codex")
    registry = _registry(pool, choice, session.id, "codex")

    listed = await registry.call("catalog.list", {})
    names = {entry["name"] for entry in listed["narzedzia"]}

    assert listed["liczba"] == 47 == len(names)
    assert "drafts.publish" not in names and "session.handoff" not in names
    wenext_run = await _registry(pool, choice, session.id, None).call("catalog.list", {})
    assert wenext_run["liczba"] == 54


# ---------------------------------------------------------------------------
# The starting context of a Wenext-run conversation does not list plugin ones
# ---------------------------------------------------------------------------


async def test_the_lists_of_previous_conversations_leave_out_plugin_sessions(pool, tenant) -> None:
    choice = await _project(pool, tenant)
    store = SessionStore(pool)
    ordinary = await store.create(
        organization_id=choice.organization_id,
        workspace_id=choice.workspace_id,
        project_id=choice.project_id,
        goal="Sprawdź, dlaczego odzew jest mały.",
        budget={},
        status=STATUS_WAITING_USER,
    )
    plugin, _ = await open_session(pool, choice, "codex")
    # A plugin session has no USER_MESSAGE, so plant one to see the filter work on the events too.
    await store.append_event(
        organization_id=choice.organization_id,
        session_id=plugin.id,
        kind=EVENT_USER_MESSAGE,
        payload={"text": "zdanie, którego lista kierunków nie ma pokazać"},
    )
    current = await store.create(
        organization_id=choice.organization_id,
        workspace_id=choice.workspace_id,
        project_id=choice.project_id,
        goal="Nowa rozmowa.",
        budget={},
        status=STATUS_WAITING_USER,
    )

    context = await starting_context(
        pool,
        capability=issue(
            session_id=current.id,
            organization_id=choice.organization_id,
            workspace_id=choice.workspace_id,
            project_id=choice.project_id,
            dsn=DSN_PLACEHOLDER,
        ),
        goal="Nowa rozmowa.",
    )

    titles = [row[0] for row in context.conversations]
    assert titles == ["Sprawdź, dlaczego odzew jest mały."], titles
    texts = [row[3] for row in context.recent_directions]
    assert all("lista kierunków" not in text for text in texts), texts
    assert any("odzew" in text for text in texts), "zwykła rozmowa zniknęła razem z wtyczkową"
    assert ordinary.id != plugin.id


# ---------------------------------------------------------------------------
# A person's approval publishes now
# ---------------------------------------------------------------------------


async def _approvable(pool, tenant, choice: ProjectChoice) -> tuple[str, str]:
    """A READY_FOR_APPROVAL revision of a project that has a signed delegation. (account, revision)"""
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id, state) "
            "VALUES ($1, 'REDDIT', $2, 'READY') RETURNING id",
            tenant["organization"],
            f"konto-{uuid4().hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            choice.project_id,
            account_id,
        )
    from datetime import UTC, datetime, timedelta

    store = ApprovalStore(pool)
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=account_id,
    )
    revision = await store.record_revision(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        proposal_id=proposal_id,
        revision_no=1,
        hash_input=ApprovalHashInput(
            organization=tenant["organization"],
            workspace=tenant["workspace"],
            assignment_version=uuid4(),
            account=account_id,
            platform="REDDIT",
            action_type=SemanticCapability.SUBMIT_COMMENT,
            target_external_id="t3_zgoda01",
            target_url="https://www.reddit.com/r/devops/comments/zgoda01/x/",
            source_fingerprint=f"session:{uuid4()}",
            text_and_media="odpowiedź agenta",
            intelligence_profile_version=uuid4(),
            plan_version=choice.project_id,
            memory_snapshot=uuid4(),
            adapter_version="wenext-session-draft/1",
        ),
        state=ActionProposalState.READY_FOR_APPROVAL,
        expires_at=datetime.now(UTC) + timedelta(hours=6),
    )
    return str(account_id), str(revision.id)


async def test_a_persons_approval_nudges_the_publisher_once_per_revision(
    pool, tenant, logged_in_client
) -> None:
    """D-030 for the person's click: the draft is minutes old, so one publisher tick now rather
    than the chain's next ten-minute slot. The key is the one `drafts.publish` uses."""
    choice = await _project(pool, tenant)
    account_id, revision_id = await _approvable(pool, tenant, choice)
    base = f"/api/workspaces/{tenant['workspace']}/projects/{choice.project_id}"
    signed = await logged_in_client.post(
        f"{base}/delegation",
        json={
            "mode": "REVIEW",
            "accounts": [account_id],
            "communities": ["r/devops"],
            "actions": ["REPLY"],
            "budget": {"publications_per_day": 6},
        },
    )
    assert signed.status_code == 201, signed.text

    async with pool.acquire() as conn:
        before = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1 AND job_type = 'pilot.publish' "
            "AND idempotency_key LIKE '%nudge:issued:%'",
            tenant["organization"],
        )
    approved = await logged_in_client.post(f"{base}/approvals/{revision_id}/approve")
    assert approved.status_code == 202, approved.text

    async with pool.acquire() as conn:
        keys = [
            row["idempotency_key"]
            for row in await conn.fetch(
                "SELECT idempotency_key FROM jobs WHERE organization_id = $1 "
                "AND job_type = 'pilot.publish' AND idempotency_key LIKE '%nudge:issued:%'",
                tenant["organization"],
            )
        ]
    assert before == 0
    assert keys == [f"publish:{tenant['organization']}:nudge:issued:{revision_id}"], keys

    # A second click is a 409 and must not add a second tick.
    again = await logged_in_client.post(f"{base}/approvals/{revision_id}/approve")
    assert again.status_code == 409
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1 AND job_type = 'pilot.publish' "
            "AND idempotency_key LIKE '%nudge:issued:%'",
            tenant["organization"],
        )
    assert total == 1


async def test_a_refused_approval_does_not_nudge(pool, tenant, logged_in_client) -> None:
    """No delegation, no request, and so nothing for the publisher to look at."""
    choice = await _project(pool, tenant)
    _, revision_id = await _approvable(pool, tenant, choice)
    base = f"/api/workspaces/{tenant['workspace']}/projects/{choice.project_id}"

    refused = await logged_in_client.post(f"{base}/approvals/{revision_id}/approve")

    assert refused.status_code == 409, refused.text
    async with pool.acquire() as conn:
        nudges = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1 AND job_type = 'pilot.publish' "
            "AND idempotency_key LIKE '%nudge:issued:%'",
            tenant["organization"],
        )
    assert nudges == 0
