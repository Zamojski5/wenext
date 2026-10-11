"""The tools a session reaches the product through (P2): scope, honesty, and one write.

FOUR PROPERTIES, and each one is a way an agent could produce a confident wrong answer:

  1. **The scope is the capability and nothing the model said.** No tool takes an organization,
     a workspace or a project; a thread from another workspace is simply not there. A tool that
     accepted an id from the conversation would let a model that read a web page asking it to
     "check project X" do exactly that.
  2. **A missing thing says which kind of missing it is.** `NIEDOSTEPNE` (the product cannot do
     this here) and `BLAD` (the attempt failed) are different, and only one is worth retrying.
     `sources.*` is `NIEWDROZONE` and says which milestone owns it, so the model reports a gap
     instead of inventing the fact it could not look up.
  3. **Truncation is always reported.** `thread.read` on a long thread returns the count it
     returned AND the count that exists. Without both, a model that saw forty of two hundred
     comments will write "the thread shows" about the forty.
  4. **`draft.create` proposes and never publishes.** It ends `OCZEKUJE`, it leaves a revision
     in the existing consent path, and it creates no publication request. If this test ever
     passes with a queued publication, an agent gained the ability to post.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from control_plane.tools import (
    BY_NAME,
    CATALOG,
    OUTCOME_DONE,
    OUTCOME_PENDING,
    OUTCOME_UNAVAILABLE,
    STATE_AVAILABLE,
    CapabilityExpired,
    SessionCapability,
    ToolRegistry,
    issue,
)

pytestmark = [pytest.mark.db, pytest.mark.p2]

DSN_PLACEHOLDER = "postgresql://nieuzywane-w-tescie"


async def _scope(pool, tenant, *, goal: str = "sprawdź odzew") -> SessionCapability:
    """A project, a session and a capability over them, through the real tables."""
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel projektu') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
        session_id = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            goal,
        )
    return issue(
        session_id=session_id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        dsn=DSN_PLACEHOLDER,
    )


async def _community(pool, tenant, *, key: str = "r/devops") -> UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO communities (organization_id, workspace_id, platform, community_key, "
            "display_name, access_mode, poll_interval_seconds) "
            "VALUES ($1, $2, 'REDDIT', $3, $4, 'ACCOUNT_REQUIRED', 600) RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            key,
            f"Reddit {key}",
        )


async def _thread(pool, tenant, community_id, *, external_id: str, title: str, comments: int = 0):
    async with pool.acquire() as conn:
        thread_id = await conn.fetchval(
            """
            INSERT INTO observed_threads
                (organization_id, workspace_id, community_id, external_id, url, title, body,
                 author, created_at_source, first_observed_at, last_observed_at, raw_sha256)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'op', now(), now(), now(), $8)
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
            community_id,
            external_id,
            f"https://www.reddit.com/r/devops/comments/{external_id}/x/",
            title,
            "treść posta",
            "0" * 64,
        )
        for n in range(comments):
            # 0004 ties depth to the parent: a top-level comment has NO `parent_external_id`
            # and depth 0, a reply has a parent and a positive depth. Every second comment is a
            # reply so `comments.read` has a parent to filter on.
            is_reply = n > 0 and n % 2 == 1
            await conn.execute(
                """
                INSERT INTO observed_comments
                    (organization_id, workspace_id, thread_id, external_id, parent_external_id,
                     author, body, created_at_source, observed_at, depth, score)
                VALUES ($1, $2, $3, $4, $5, $6, $7, now(), now(), $8, 1)
                """,
                tenant["organization"],
                tenant["workspace"],
                thread_id,
                f"c{n}",
                "c0" if is_reply else None,
                f"autor{n}",
                f"komentarz numer {n}",
                1 if is_reply else 0,
            )
    return thread_id


async def _account(pool, tenant, capability, *, handle: str, platform: str = "REDDIT"):
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
            "VALUES ($1, $2, $3) RETURNING id",
            tenant["organization"],
            platform,
            handle,
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            capability.project_id,
            account_id,
        )
    return account_id


# ---------------------------------------------------------------------------
# The catalog, and its agreement with what exists
# ---------------------------------------------------------------------------


def test_every_catalog_entry_has_an_implementation_or_says_it_has_none() -> None:
    """A catalog and a registry that disagree give a model either a tool nobody can call or a
    capability nobody declared. Both are worse than a short catalog."""
    assert len(CATALOG) >= 10, "katalog podejrzanie krótki"
    registry = ToolRegistry(
        capability=issue(
            session_id=uuid4(),
            organization_id=uuid4(),
            workspace_id=uuid4(),
            project_id=uuid4(),
            dsn=DSN_PLACEHOLDER,
        ),
        pool=None,  # type: ignore[arg-type]  # nothing is called here
    )
    for spec in CATALOG:
        handler = getattr(registry, "_" + spec.name.replace(".", "_"), None)
        if spec.state == STATE_AVAILABLE:
            assert handler is not None, (
                f"{spec.name} jest w katalogu jako DOSTEPNE i nie ma implementacji"
            )
        else:
            assert spec.missing, f"{spec.name}: stan {spec.state} bez powodu"


def test_the_manifest_lists_unavailable_tools_too_and_marks_them(pool, tenant) -> None:
    """Hiding an unavailable tool makes it indistinguishable from one the product lacks.

    SINCE P3 ETAP B NO CATALOG ENTRY IS UNBUILT: `sources.search` and `sources.read`, the two
    that stood here as NIEWDROZONE, are implemented. The mechanism they exercised has to keep
    working for the next unbuilt tool, so it is checked against a spec built HERE rather than
    against whichever entry happens to be missing today. The manifest itself is still compared
    with the catalog, and every real entry is still required to carry its state.
    """
    from control_plane.tools import manifest
    from control_plane.tools.catalog import STATE_NOT_IMPLEMENTED, ToolSpec

    entries = {entry["name"]: entry for entry in manifest()}
    assert set(entries) == set(BY_NAME), "manifest i katalog się rozjechały"
    assert "NIEWDROZONE" not in entries["catalog.list"]["description"]
    for name, spec in BY_NAME.items():
        if spec.state != STATE_AVAILABLE:
            assert spec.state in entries[name]["description"], name
            assert spec.missing in entries[name]["description"], name

    unbuilt = ToolSpec(
        name="publish.now",
        summary="Opublikuj natychmiast.",
        detail="Nie istnieje.",
        state=STATE_NOT_IMPLEMENTED,
        missing="kamień P9: publikacja bez zgody człowieka",
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    described = unbuilt.manifest_entry()["description"]
    assert described.startswith("[NIEWDROZONE: kamień P9")
    assert "Opublikuj natychmiast." in described


async def test_catalog_list_names_the_scope_it_is_bound_to(pool, tenant) -> None:
    capability = await _scope(pool, tenant)
    registry = ToolRegistry(capability=capability, pool=pool)
    result = await registry.call("catalog.list", {})
    assert result["stan"] == OUTCOME_DONE
    assert result["liczba"] == len(CATALOG)
    assert result["zakres"]["projekt"] == str(capability.project_id)


async def test_describing_an_unknown_tool_is_an_error_not_an_empty_answer(pool, tenant) -> None:
    capability = await _scope(pool, tenant)
    registry = ToolRegistry(capability=capability, pool=pool)
    result = await registry.call("catalog.describe", {"tool": "publish.now"})
    assert result["stan"] == "BLAD" and "publish.now" in result["powod"]


async def test_a_tool_outside_the_catalog_raises_rather_than_returning_a_result(
    pool, tenant
) -> None:
    from control_plane.tools import NoSuchTool

    capability = await _scope(pool, tenant)
    registry = ToolRegistry(capability=capability, pool=pool)
    with pytest.raises(NoSuchTool):
        await registry.call("shell.exec", {"cmd": "ls"})


async def test_an_unbuilt_tool_says_which_milestone_owns_it(pool, tenant, monkeypatch) -> None:
    """The NIEWDROZONE path, on a synthetic entry.

    `sources.search` used to be the subject here and is built since P3 etap B, so the entry is
    injected into the catalog for the length of this test. The property being protected is not
    "this particular tool is missing" but "a tool the product declares and has not built says so
    on the call, and says what to do instead" — the moment at which a model otherwise invents the
    fact it could not look up.
    """
    from control_plane.tools import catalog as catalog_module
    from control_plane.tools.catalog import STATE_NOT_IMPLEMENTED, ToolSpec

    unbuilt = ToolSpec(
        name="publish.now",
        summary="Opublikuj natychmiast.",
        detail="Nie istnieje.",
        state=STATE_NOT_IMPLEMENTED,
        missing="kamień P9: publikacja bez zgody człowieka",
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    monkeypatch.setitem(catalog_module.BY_NAME, "publish.now", unbuilt)

    capability = await _scope(pool, tenant)
    registry = ToolRegistry(capability=capability, pool=pool)
    result = await registry.call("publish.now", {})
    assert result["stan"] == OUTCOME_UNAVAILABLE
    assert "P9" in result["powod"]
    assert "zgadywać" in result["wskazowka"], (
        "narzędzie niewdrożone nie mówi modelowi, co ma zrobić zamiast zgadywania, a to jest "
        "dokładnie moment, w którym powstaje zmyślony fakt"
    )


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


async def test_an_expired_capability_stops_every_call(pool, tenant) -> None:
    """Checked on EVERY call, not once at startup: the process lives as long as the turn."""
    capability = await _scope(pool, tenant)
    expired = SessionCapability(
        session_id=capability.session_id,
        organization_id=capability.organization_id,
        workspace_id=capability.workspace_id,
        project_id=capability.project_id,
        dsn=capability.dsn,
        issued_at=datetime.now(UTC) - timedelta(hours=2),
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    registry = ToolRegistry(capability=expired, pool=pool)
    with pytest.raises(CapabilityExpired):
        await registry.call("catalog.list", {})


async def test_threads_of_another_workspace_are_simply_not_there(pool, tenant) -> None:
    """Not forbidden: absent. The model has no way to name them and no way to learn they exist."""
    capability = await _scope(pool, tenant)
    mine = await _community(pool, tenant, key="r/kubernetes")
    await _thread(pool, tenant, mine, external_id="mine001", title="moj watek o odzewie")

    async with pool.acquire() as conn:
        other_workspace = await conn.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, $2) RETURNING id",
            tenant["organization"],
            f"obcy-{uuid4().hex[:6]}",
        )
    foreign_tenant = {"organization": tenant["organization"], "workspace": other_workspace}
    foreign_community = await _community(foreign_tenant and pool, foreign_tenant, key="r/obcy")
    await _thread(
        pool, foreign_tenant, foreign_community, external_id="other01", title="cudzy watek odzew"
    )

    registry = ToolRegistry(capability=capability, pool=pool)
    found = await registry.call("threads.search", {"query": "odzew"})
    external = [t["external_id"] for t in found["watki"]]
    assert external == ["mine001"], f"wyszukiwanie przekroczyło granicę workspace'u: {external}"

    direct = await registry.call("thread.read", {"thread_id": "other01"})
    assert direct["stan"] == OUTCOME_UNAVAILABLE, (
        "wątek z cudzego workspace'u dał się przeczytać po podaniu jego identyfikatora wprost"
    )


# ---------------------------------------------------------------------------
# Reading, and saying how much was read
# ---------------------------------------------------------------------------


async def test_thread_read_always_reports_how_much_it_returned_and_how_much_exists(
    pool, tenant
) -> None:
    capability = await _scope(pool, tenant)
    community = await _community(pool, tenant)
    await _thread(pool, tenant, community, external_id="long01", title="długi wątek", comments=30)
    registry = ToolRegistry(capability=capability, pool=pool)

    clipped = await registry.call("thread.read", {"thread_id": "long01", "limit": 5})
    assert clipped["stan"] == OUTCOME_DONE
    assert clipped["komentarzy_zwrocono"] == 5
    assert clipped["komentarzy_lacznie"] == 30
    assert clipped["przyciete"] is True, (
        "wątek przycięty do piątej części nie powiedział o tym. Model napisze „wątek pokazuje” "
        "o tym, co dostał, a dostał piątą część"
    )

    whole = await registry.call("thread.read", {"thread_id": "long01", "limit": 100})
    assert whole["komentarzy_zwrocono"] == 30 and whole["przyciete"] is False


async def test_a_thread_can_be_read_by_either_identifier(pool, tenant) -> None:
    """`threads.search` hands back both; demanding one of them is a trap with no upside."""
    capability = await _scope(pool, tenant)
    community = await _community(pool, tenant)
    thread_id = await _thread(pool, tenant, community, external_id="both01", title="oba")
    registry = ToolRegistry(capability=capability, pool=pool)
    by_platform = await registry.call("thread.read", {"thread_id": "both01"})
    by_database = await registry.call("thread.read", {"thread_id": str(thread_id)})
    assert by_platform["watek"]["id"] == by_database["watek"]["id"]


async def test_publications_report_public_visibility_next_to_the_status(pool, tenant) -> None:
    """`PUBLISHED` says what we did; `public_visibility` says what anybody else can see. A tool
    that returned only the first would let an agent report a removed comment as a result."""
    capability = await _scope(pool, tenant)
    registry = ToolRegistry(capability=capability, pool=pool)
    result = await registry.call("publications.list", {})
    assert result["stan"] == OUTCOME_DONE
    assert (
        "UNVERIFIED" in result["uwaga"] and "nie że publikacja jest w porządku" in result["uwaga"]
    )
    for publication in result["publikacje"]:
        assert "widocznosc_publiczna" in publication


async def test_a_project_with_no_accounts_says_so_instead_of_returning_nothing(
    pool, tenant
) -> None:
    capability = await _scope(pool, tenant)
    registry = ToolRegistry(capability=capability, pool=pool)
    result = await registry.call("accounts.list", {})
    assert result["stan"] == OUTCOME_UNAVAILABLE
    assert result["konta"] == []
    assert "człowieka" in result["wskazowka"]


async def test_an_assigned_account_carries_what_its_platform_can_actually_do(pool, tenant) -> None:
    """The capability matrix is a fact about this repository's code. Reported next to the
    account, so "no integration" is never read as "nothing happened to be there"."""
    capability = await _scope(pool, tenant)
    await _account(pool, tenant, capability, handle="Demo_Maintainer")
    await _account(pool, tenant, capability, handle="anna", platform="LINKEDIN")
    registry = ToolRegistry(capability=capability, pool=pool)

    result = await registry.call("accounts.list", {})
    by_platform = {a["platforma"]: a for a in result["konta"]}
    assert by_platform["REDDIT"]["moze"]["komentowac"] is True
    assert by_platform["LINKEDIN"]["integracja"] is False
    assert by_platform["LINKEDIN"]["moze"]["komentowac"] is False


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


async def test_memory_write_then_search_finds_it_with_its_source(pool, tenant) -> None:
    """An interpretation the agent may always record, found again with what it stands on."""
    capability = await _scope(pool, tenant)
    registry = ToolRegistry(capability=capability, pool=pool)
    written = await registry.call(
        "memory.write",
        {
            "kind": "INTERPRETATION",
            "statement": "wchodzimy do wątków za późno, stąd mały odzew",
            "source_span": "thread.read e2e001",
        },
    )
    assert written["stan"] == OUTCOME_DONE, written
    assert written["zapisano"]["rodzaj"] == "INTERPRETATION"

    found = await registry.call("memory.search", {"query": "za późno"})
    assert [u["tresc"] for u in found["ustalenia"]] == [
        "wchodzimy do wątków za późno, stąd mały odzew"
    ]
    entry = found["ustalenia"][0]
    assert entry["powiedzial"] == "AGENT"
    assert entry["z_sesji"] == str(capability.session_id), (
        "ustalenie nie wskazuje sesji, która je zapisała, więc nie da się prześledzić, skąd "
        "wzięła się zmiana zachowania"
    )
    assert "obowiązuje" in found["uwaga"]


async def test_a_fact_without_a_source_is_refused_with_the_reason(pool, tenant) -> None:
    """The rule lives in the database (0016), and the tool's job is to hand the model a sentence
    it can act on: pick INTERPRETATION, or find the source first."""
    capability = await _scope(pool, tenant)
    registry = ToolRegistry(capability=capability, pool=pool)
    result = await registry.call(
        "memory.write", {"kind": "FACT", "statement": "r/devops ma próg karmy"}
    )
    assert result["stan"] == "BLAD"
    assert "źród" in result["powod"].lower(), result["powod"]


async def test_an_agent_cannot_record_a_preference_the_person_never_spoke(pool, tenant) -> None:
    """THE refusal worth having. An agent writing "the user prefers X" creates somebody's will
    that nobody expressed, and the next session applies it as the person's own.

    OD E5 (audyt UX 20.09 punkt 5) agent MOŻE zapisać korektę człowieka, ale tylko wskazując
    palcem jego wypowiedź w tej rozmowie (`payload.confirmed_at_seq`), tak samo jak hipotezę z
    D-044. Tu nie ma żadnego wskazania, więc to jest dokładnie ten zapis, o który chodziło:
    zdanie o cudzej woli, którego nikt nie wypowiedział. Sam warunek bazy (0016: PREFERENCE musi
    być USER) stoi dalej i pilnuje go `test_knowledge_store.py` na poziomie zapisu.
    """
    capability = await _scope(pool, tenant)
    registry = ToolRegistry(capability=capability, pool=pool)
    result = await registry.call(
        "memory.write", {"kind": "PREFERENCE", "statement": "użytkownik woli krótkie odpowiedzi"}
    )
    assert result["stan"] == "BLAD", result
    assert "confirmed_at_seq" in result["powod"], result["powod"]

    found = await registry.call("memory.search", {})
    assert found["ustalenia"] == [], "preferencja mimo odmowy trafiła do pamięci"


async def test_an_experience_without_its_outcome_is_refused(pool, tenant) -> None:
    """Three fields or nothing: without an outcome it is a plan, without a situation an anecdote."""
    capability = await _scope(pool, tenant)
    registry = ToolRegistry(capability=capability, pool=pool)
    incomplete = await registry.call(
        "memory.write",
        {
            "kind": "EXPERIENCE",
            "statement": "odpowiedź w godzinę działa",
            "payload": {"situation": "świeży post", "decision": "odpowiedzieć od razu"},
        },
    )
    assert incomplete["stan"] == "BLAD"

    complete = await registry.call(
        "memory.write",
        {
            "kind": "EXPERIENCE",
            "statement": "odpowiedź w godzinę działa",
            "payload": {
                "situation": "świeży post",
                "decision": "odpowiedzieć od razu",
                "outcome": "jedenaście punktów i dwie odpowiedzi",
            },
        },
    )
    assert complete["stan"] == OUTCOME_DONE


async def test_memory_of_another_project_is_not_visible(pool, tenant) -> None:
    first = await _scope(pool, tenant)
    second = await _scope(pool, tenant)
    await ToolRegistry(capability=first, pool=pool).call(
        "memory.write", {"kind": "INTERPRETATION", "statement": "sekret pierwszego projektu"}
    )
    found = await ToolRegistry(capability=second, pool=pool).call("memory.search", {})
    assert [u["tresc"] for u in found["ustalenia"]] == []


# ---------------------------------------------------------------------------
# The one tool that proposes an action
# ---------------------------------------------------------------------------


async def test_draft_create_proposes_and_publishes_nothing(pool, tenant) -> None:
    """THE test of this file. If it ever passes with a queued publication, an agent can post."""
    capability = await _scope(pool, tenant)
    await _account(pool, tenant, capability, handle="Demo_Maintainer")
    registry = ToolRegistry(capability=capability, pool=pool)

    result = await registry.call(
        "draft.create",
        {
            "target_external_id": "t3_abc123",
            "target_url": "https://www.reddit.com/r/devops/comments/abc123/x/",
            "text": "Odpowiedź, którą proponuję.",
            "account": "Demo_Maintainer",
        },
    )

    assert result["stan"] == OUTCOME_PENDING, (
        f"draft.create zgłosił {result['stan']}. Z punktu widzenia człowieka nic się jeszcze "
        "nie stało, a WYKONANE znaczy, że się stało"
    )
    assert "nic nie zostało opublikowane" in result["powod"]
    revision_id = UUID(result["propozycja"]["revision_id"])

    async with pool.acquire() as conn:
        revision = await conn.fetchrow(
            "SELECT state, payload_hash FROM action_proposal_revisions WHERE id = $1", revision_id
        )
        decisions = await conn.fetchval(
            "SELECT count(*) FROM approval_decisions WHERE proposal_revision_id = $1", revision_id
        )
        publications = await conn.fetchval(
            "SELECT count(*) FROM publication_requests WHERE proposal_revision_id = $1",
            revision_id,
        )
        intents = await conn.fetchval(
            "SELECT count(*) FROM execution_intents WHERE organization_id = $1",
            tenant["organization"],
        )

    assert revision is not None and revision["state"] == "READY_FOR_APPROVAL"
    assert revision["payload_hash"] == result["propozycja"]["payload_hash"]
    assert decisions == 0, "powstała zgoda, której nikt nie wydał"
    assert publications == 0, "propozycja trafiła do kolejki publikacji"
    assert intents == 0, "powstał intent wykonania, czyli krok BEZPOŚREDNIO przed wysyłką"


async def test_draft_create_refuses_an_account_that_is_not_assigned_here(pool, tenant) -> None:
    capability = await _scope(pool, tenant)
    await _account(pool, tenant, capability, handle="Demo_Maintainer")
    registry = ToolRegistry(capability=capability, pool=pool)
    result = await registry.call(
        "draft.create",
        {
            "target_external_id": "t3_abc123",
            "target_url": "https://www.reddit.com/r/devops/comments/abc123/x/",
            "text": "treść",
            "account": "ktoś_inny",
        },
    )
    assert result["stan"] == OUTCOME_UNAVAILABLE and "nie jest przypisane" in result["powod"]


async def test_draft_create_refuses_a_platform_with_no_write_integration(pool, tenant) -> None:
    """A fact about the code, not about the account: no adapter would carry it out, so a
    proposal would be a promise nothing can keep."""
    capability = await _scope(pool, tenant)
    await _account(pool, tenant, capability, handle="anna", platform="LINKEDIN")
    registry = ToolRegistry(capability=capability, pool=pool)
    result = await registry.call(
        "draft.create",
        {
            "target_external_id": "urn:li:activity:1",
            "target_url": "https://www.linkedin.com/feed/update/1/",
            "text": "treść",
            "account": "anna",
        },
    )
    assert result["stan"] == OUTCOME_UNAVAILABLE and "integracji" in result["powod"]


async def test_project_update_writes_the_goal_the_person_agreed_to(pool, tenant) -> None:
    capability = await _scope(pool, tenant)
    registry = ToolRegistry(capability=capability, pool=pool)
    result = await registry.call("project.update", {"goal": "buduj obecność wokół niezawodności"})
    assert result["stan"] == OUTCOME_DONE and result["cel"].startswith("buduj obecność")
    read = await registry.call("project.read", {})
    assert read["projekt"]["cel"] == "buduj obecność wokół niezawodności"


async def test_project_update_refuses_an_unknown_mode_and_an_empty_goal(pool, tenant) -> None:
    capability = await _scope(pool, tenant)
    registry = ToolRegistry(capability=capability, pool=pool)
    assert (await registry.call("project.update", {"mode": "TURBO"}))["stan"] == "BLAD"
    assert (await registry.call("project.update", {"goal": "   "}))["stan"] == "BLAD"
    assert (await registry.call("project.update", {}))["stan"] == "BLAD"
