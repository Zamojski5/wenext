"""The capability matrix is ENFORCED, not just described (P6 etap A).

`workspace/platforms.py` has said since P1 what this product can do on each platform. Saying it
is half the job: an X account assigned to a project could still be named in a delegation for
REPLY, still be handed to `draft.create`, and still appear in a catalog that called the tool
available. Each of those is a promise no adapter here can keep, and this file is the guard on
every one of them.

THE FOUR PLACES, AND WHY ALL FOUR:

  1. the CATALOG, so the model reads "[WYMAGA_POLACZENIA: ...]" before it spends a call;
  2. the DELEGATED DECISION, so a signature naming an impossible action refuses instead of
     queueing a request the publisher can only skip;
  3. the TOOL, so a call made anyway is refused with the platform named;
  4. the EFFECT READBACK, so a platform we cannot check does not answer with an empty list of
     observations. An empty list reads as "sprawdzono, nic nie ma", and P0 exists because that
     sentence was once printed about a comment that had been removed.

ONE RULE IN ONE PLACE. Every check here goes through `platforms.support_for` and
`presence/scope.py::action_supported`. A second `if platform == "X"` anywhere would disagree
within a week, and the disagreement would be a screen offering work no code performs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from control_plane.presence.scope import (
    ACTION_OWN_POST,
    ACTION_REPLY,
    ACTION_RESEARCH,
    MODE_AUTONOMOUS,
    action_supported,
    covers,
    parse_budget,
    parse_scope,
)
from control_plane.presence.store import PresenceStore
from control_plane.tools.catalog import (
    STATE_AVAILABLE,
    STATE_NEEDS_CONNECTION,
    listing,
    manifest,
)
from control_plane.workspace.platforms import support_for

pytestmark = [pytest.mark.p6]


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "action", "possible"),
    [
        ("REDDIT", ACTION_REPLY, True),
        # Prawda od 0023: kapsuła umie założyć wątek. Macierz mówi, CO POTRAFI KOD; czy wolno,
        # rozstrzyga powierzenie, a czy wypada, karta głosu społeczności.
        ("REDDIT", ACTION_OWN_POST, True),
        ("HACKER_NEWS", ACTION_REPLY, False),
        # Prawda od 26.09: kapsuła odpowiada i publikuje na X (`x_writer`), publikator czyta skutek.
        ("X", ACTION_REPLY, True),
        ("X", ACTION_OWN_POST, True),
        ("LINKEDIN", ACTION_REPLY, False),
        ("LINKEDIN", ACTION_OWN_POST, False),
    ],
)
def test_the_rule_answers_from_the_matrix(platform, action, possible) -> None:
    assert action_supported(action, support_for(platform)) is possible


def test_research_needs_nothing_from_an_adapter(platform="LINKEDIN") -> None:
    """Reading and remembering is something the agent does with materials. A platform with no
    integration does not stop a project from thinking about it."""
    assert action_supported(ACTION_RESEARCH, support_for(platform)) is True


def test_a_platform_this_product_does_not_know_supports_nothing() -> None:
    assert action_supported(ACTION_REPLY, support_for("MASTODON")) is False
    assert action_supported(ACTION_RESEARCH, support_for("MASTODON")) is False


# ---------------------------------------------------------------------------
# 1. The catalog
# ---------------------------------------------------------------------------


def _entry(entries, name):
    return next(one for one in entries if one["name"] == name)


def test_the_catalog_marks_a_publication_tool_unavailable_without_a_commenting_account() -> None:
    """A project whose only account is on LinkedIn. The tool is still LISTED: a tool that vanishes is
    indistinguishable from a tool this product does not have."""
    entries = listing(frozenset())
    draft = _entry(entries, "draft.create")
    assert draft["state"] == STATE_NEEDS_CONNECTION
    assert "komentować" in draft["missing"]
    assert any(one["name"] == "draft.create" for one in entries), "pozycja zostaje na liście"


def test_the_catalog_leaves_it_available_when_an_account_can_comment() -> None:
    entries = listing(frozenset({"read", "comment", "effect_readback"}))
    assert _entry(entries, "draft.create")["state"] == STATE_AVAILABLE
    assert _entry(entries, "observations.read")["state"] == STATE_AVAILABLE


def test_the_effect_readback_tool_says_so_when_no_account_can_be_checked() -> None:
    """Hacker News can be read but not checked afterwards: `effect_readback` is false there."""
    entries = listing(frozenset({"read"}))
    observations = _entry(entries, "observations.read")
    assert observations["state"] == STATE_NEEDS_CONNECTION
    assert "skutek publikacji" in observations["missing"]


def test_an_unnarrowed_catalog_describes_the_product_not_a_project() -> None:
    """`None` means "not measured", and the catalog then answers as it did before P6. A database
    hiccup in the MCP server must not look like a project with no accounts."""
    assert _entry(listing(None), "draft.create")["state"] == STATE_AVAILABLE
    assert _entry(listing(), "draft.create")["state"] == STATE_AVAILABLE


def test_the_mcp_manifest_carries_the_state_into_the_description() -> None:
    """The model reads the description, not the state field, so the state has to be IN it."""
    described = _entry(manifest(frozenset()), "draft.create")["description"]
    assert described.startswith(f"[{STATE_NEEDS_CONNECTION}:")
    assert "komentować" in described


def test_narrowing_never_makes_an_unimplemented_tool_look_available() -> None:
    """The narrowing only ever takes capability away. A tool that is NIEWDROZONE stays that way
    whatever the accounts can do, because the missing thing is the code."""
    for capabilities in (None, frozenset(), frozenset({"comment", "own_post", "read"})):
        for entry in listing(capabilities):
            if entry["state"] != STATE_AVAILABLE:
                assert entry.get("missing"), entry


# ---------------------------------------------------------------------------
# 2, 3 and 4: the decision, the tool, the readback
# ---------------------------------------------------------------------------


async def _project_with_account(pool, tenant, *, platform: str):
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
            "VALUES ($1, $2, $3) RETURNING id",
            tenant["organization"],
            platform,
            f"konto-{uuid4().hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            account_id,
        )
    return project_id, account_id


@pytest.mark.db
async def test_a_delegation_naming_an_x_account_for_reply_refuses_the_proposal(
    pool, tenant
) -> None:
    """THE CASE THE ASSIGNMENT NAMES. The signature is accepted, because a person may well sign
    it in good faith and revoking it should be their decision; what is refused is the PROPOSAL,
    with a sentence naming the platform, so the reason is actionable rather than mysterious.
    """
    from test_presence_decision import _approver, _propose

    project_id, account_id = await _project_with_account(pool, tenant, platform="LINKEDIN")
    scope = parse_scope(
        {"accounts": [str(account_id)], "communities": ["r/devops"], "actions": ["REPLY"]}
    )
    await PresenceStore(pool).sign(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        mode=MODE_AUTONOMOUS,
        scope=scope,
        budget=parse_budget({}),
        operator_id=tenant["operator"],
    )
    # The scope alone would cover it: the account, the action and the community are all listed.
    assert covers(
        mode=MODE_AUTONOMOUS,
        scope=scope,
        action=ACTION_REPLY,
        account_id=account_id,
        community_key="r/devops",
    ).covered
    # The platform is what refuses.
    verdict = covers(
        mode=MODE_AUTONOMOUS,
        scope=scope,
        action=ACTION_REPLY,
        account_id=account_id,
        community_key="r/devops",
        support=support_for("LINKEDIN"),
    )
    assert not verdict.covered
    assert "LinkedIn" in verdict.reason and "REPLY" in verdict.reason

    seeded = {"project": project_id, "account": account_id}
    await _propose(pool, tenant, seeded, account_id=account_id)
    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=project_id, now=now()
    )
    assert [one.outcome for one in considered] == ["ODMOWA"], considered
    assert "LinkedIn" in considered[0].reason
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM publication_requests WHERE project_id = $1", project_id
            )
            == 0
        )


@pytest.mark.db
async def test_the_draft_tool_refuses_an_account_whose_platform_cannot_comment(
    pool, tenant
) -> None:
    """Already true before P6 and measured here so it stays true: the tool names the platform."""
    from control_plane.tools.capability import issue
    from control_plane.tools.registry import ToolRegistry

    project_id, account_id = await _project_with_account(pool, tenant, platform="LINKEDIN")
    async with pool.acquire() as conn:
        handle = await conn.fetchval(
            "SELECT external_account_id FROM social_accounts WHERE id = $1", account_id
        )
        session_id = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            project_id,
        )
    registry = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session_id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=project_id,
            dsn="postgresql://atrapa/nieuzywane",
        ),
    )
    result = await registry.call(
        "draft.create",
        {
            "target_external_id": "t3_abc123",
            "target_url": "https://www.linkedin.com/feed/update/abc123/",
            "text": "propozycja",
            "account": handle,
        },
    )
    assert result["stan"] == "NIEDOSTEPNE", result
    assert "LINKEDIN" in result["powod"] or "LinkedIn" in result["powod"]


@pytest.mark.db
async def test_the_catalog_a_session_reads_is_narrowed_to_its_own_accounts(pool, tenant) -> None:
    """The narrowing is not theoretical: it is computed from the rows of THIS project."""
    from control_plane.tools.capability import issue
    from control_plane.tools.registry import ToolRegistry

    async def registry_for(platform: str):
        project_id, _account = await _project_with_account(pool, tenant, platform=platform)
        async with pool.acquire() as conn:
            session_id = await conn.fetchval(
                "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
                "VALUES ($1, $2, $3, 'cel') RETURNING id",
                tenant["organization"],
                tenant["workspace"],
                project_id,
            )
        return ToolRegistry(
            pool=pool,
            capability=issue(
                session_id=session_id,
                organization_id=tenant["organization"],
                workspace_id=tenant["workspace"],
                project_id=project_id,
                dsn="postgresql://atrapa/nieuzywane",
            ),
        )

    on_linkedin = await registry_for("LINKEDIN")
    assert await on_linkedin.platform_capabilities() == frozenset()
    listed = (await on_linkedin.call("catalog.list", {}))["narzedzia"]
    assert _entry(listed, "draft.create")["state"] == STATE_NEEDS_CONNECTION

    on_x = await registry_for("X")
    assert {"read", "comment", "own_post", "effect_readback"} <= await (
        on_x.platform_capabilities()
    )

    on_reddit = await registry_for("REDDIT")
    assert "comment" in await on_reddit.platform_capabilities()
    listed = (await on_reddit.call("catalog.list", {}))["narzedzia"]
    assert _entry(listed, "draft.create")["state"] == STATE_AVAILABLE


@pytest.mark.db
async def test_reading_the_effect_on_a_platform_we_cannot_check_is_a_refusal_not_an_empty_list(
    pool, tenant
) -> None:
    """THE P0 RULE, ONE PLATFORM FURTHER.

    Hacker News can be read but not checked after publishing: `effect_readback` is false. An
    empty list of control reads would say "sprawdziliśmy i nic nie ma", and P0 exists because
    that exact sentence was once printed about a comment AutoModerator had removed. Here nobody
    checked and there is nothing to check with, and the tool says which of the two it is.
    """
    from control_plane.approval.store import APPROVED_DECISION, ApprovalStore
    from control_plane.pilot.publication import PublicationStore
    from control_plane.tools.capability import issue
    from control_plane.tools.registry import ToolRegistry
    from test_presence_decision import _propose

    project_id, account_id = await _project_with_account(pool, tenant, platform="HACKER_NEWS")
    delegation = await PresenceStore(pool).sign(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        mode=MODE_AUTONOMOUS,
        scope=parse_scope(
            {"accounts": [str(account_id)], "communities": ["news"], "actions": ["REPLY"]}
        ),
        budget=parse_budget({}),
        operator_id=tenant["operator"],
    )
    revision_id = await _propose(
        pool, tenant, {"project": project_id, "account": account_id}, account_id=account_id
    )
    approvals = ApprovalStore(pool)
    decision, approved = await approvals.record_decision(
        revision_id=revision_id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        decision=APPROVED_DECISION,
        operator_id=tenant["operator"],
        reason=f"delegacja {delegation.id}",
    )
    publication = PublicationStore(pool)
    request = await publication.create_project_request(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        delegation_id=delegation.id,
        proposal_revision_id=approved.id,
        approval_decision_id=decision.id,
        position=await publication.next_position(organization_id=tenant["organization"]),
        consent_source=f"delegacja {delegation.id}",
        draft_sha256="0" * 64,
        target_external_id="t3_abc123",
        target_url="https://news.ycombinator.com/item?id=1",
    )

    async with pool.acquire() as conn:
        session_id = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            project_id,
        )
    registry = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session_id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=project_id,
            dsn="postgresql://atrapa/nieuzywane",
        ),
    )
    result = await registry.call("observations.read", {"request_id": str(request.id)})
    assert result["stan"] == "NIEDOSTEPNE", result
    assert "Hacker News" in result["powod"]
    # It still says what IS known: the request exists and has a status. The refusal is about the
    # control reads, not about the publication.
    assert result["publikacja"]["request_id"] == str(request.id)
    assert "sprawdziliśmy i nic nie ma" in result["wskazowka"]
