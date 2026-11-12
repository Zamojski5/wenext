"""The agent's X tools and the X draft, through `ToolRegistry.call` as the MCP server calls them.

* without an X account the tools return the "Połącz X" card, never an empty result;
* the identity is checked once per turn, and another account is a refusal, not a read of a
  stranger's bubble;
* a read maps to posts with their relation and READ counters, and an X refusal keeps its code;
* `accounts.connect(platform=X)` for a project with no X account gives a card with two actions;
* `draft.create` on an X account aims a reply at one post (the id comes from the address) and an
  own post at the account's timeline, refuses a title and a text over X's weighted limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from community_observer.x_browser import XPage, XPlatformRefused, XPost, XSessionState
from control_plane.tools.capability import issue
from control_plane.tools.registry import ToolRegistry
from control_plane.tools.x_tools import x_text_weight

HANDLE = "demo_agent"
POST_URL = "https://x.com/jack/status/2103587050346250341"


def test_x_counts_a_url_as_23_and_wide_characters_as_two() -> None:
    assert x_text_weight("hello") == 5
    assert x_text_weight("see https://example.com/a/very/long/path") == 4 + 23
    assert x_text_weight("żółć") == 4
    assert x_text_weight("日本") == 4


#: The text of the founder's post that `draft.create` accepted as 275 and the actuator refused as
#: 285 of 280 (11.10.2026), character for character.
FOUNDER_POST = (
    "I'm good at building things. Getting people to use them, not so much.\n\n"
    'So I\'m building Wenext: type "find my first users" in the Codex chat where you built your '
    "app. It finds people on Reddit who need it and drafts your reply.\n\n"
    "Nothing posts without your click. getwenext.com"
)


def test_the_draft_and_the_actuator_count_with_one_function() -> None:
    """The cause of 11.10.2026 was two counters. `x_text_weight` is the shared one, by identity,
    so no edit to one side can leave the other behind; the vectors repeat the founder's post."""
    from community_os_contracts import x_text

    assert x_text_weight is x_text.x_weighted_length
    assert len(FOUNDER_POST) == 275
    assert x_text_weight(FOUNDER_POST) == 285
    assert x_text_weight("getwenext.com") == 23
    assert x_text_weight("see x.co/a, then getwenext.com.") == 4 + 23 + 7 + 23 + 1


def _bare_registry() -> ToolRegistry:
    return ToolRegistry(
        pool=None,  # type: ignore[arg-type]  # the target and the length need no database
        capability=issue(
            session_id=uuid4(),
            organization_id=uuid4(),
            workspace_id=uuid4(),
            project_id=uuid4(),
            dsn="postgresql://atrapa/nieuzywane",
        ),
    )


@pytest.mark.parametrize("kind", ["OWN_POST", "REPLY"])
def test_an_x_draft_over_the_actuators_count_is_refused_with_the_actuators_number(
    kind: str,
) -> None:
    """The founder's post, which the old draft counter let through, is refused HERE with 285."""
    refused = _bare_registry()._x_draft_target(
        kind=kind, text=FOUNDER_POST, title="", account=HANDLE, target_url=POST_URL
    )
    assert refused["stan"] == "NIEDOSTEPNE", refused
    assert "285" in refused["powod"] and "280" in refused["powod"]
    assert "getwenext.com" in refused["wskazowka"], "the hint names the rule that bit"

    shorter = FOUNDER_POST.removesuffix(" getwenext.com")
    fits = _bare_registry()._x_draft_target(
        kind=kind, text=shorter, title="", account=HANDLE, target_url=POST_URL
    )
    assert "stan" not in fits and fits["target_external_id"], fits


@dataclass
class DumbSource:
    identity: XSessionState
    page: Any = None
    identity_reads: int = 0
    reads: list[tuple[str, bool]] = field(default_factory=list)

    async def verify_identity(self, expected: str) -> XSessionState:
        self.identity_reads += 1
        return self.identity

    async def search(self, query: str, *, mode: str = "live") -> XPage:
        self.reads.append((f"search:{query}:{mode}", False))
        return self._answer()

    async def read(self, url: str, *, anonymous: bool = False) -> XPage:
        self.reads.append((url, anonymous))
        return self._answer()

    def _answer(self) -> XPage:
        if isinstance(self.page, Exception):
            raise self.page
        return self.page

    async def aclose(self) -> None:
        return None


def _page(*posts: XPost) -> XPage:
    return XPage(
        requested_url=POST_URL,
        final_url=POST_URL,
        capability="post",
        anonymous=False,
        viewer=HANDLE,
        login_wall=False,
        logged_in=True,
        unavailable=None,
        posts=posts,
        profile=None,
        text="",
        limitations=("Odczytano 2 postów z 2 widocznych.",),
    )


async def _registry(pool, tenant, *, platform: str | None = "X") -> tuple[ToolRegistry, Any]:
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
        if platform is not None:
            account_id = await conn.fetchval(
                "INSERT INTO social_accounts (organization_id, platform, external_account_id, "
                "state) VALUES ($1, $2, $3, 'READY') RETURNING id",
                tenant["organization"],
                platform,
                HANDLE if platform == "X" else f"r{uuid4().hex[:8]}",
            )
            await conn.execute(
                "INSERT INTO project_account_assignments (organization_id, workspace_id, "
                "project_id, social_account_id) VALUES ($1, $2, $3, $4)",
                tenant["organization"],
                tenant["workspace"],
                project_id,
                account_id,
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
    return registry, project_id


def _use(monkeypatch, source: DumbSource) -> None:
    monkeypatch.setattr(
        "control_plane.observe.capsule_session.x_probe_from_env", lambda env=None: source
    )


@pytest.mark.db
async def test_without_an_x_account_the_tools_return_the_connect_card(pool, tenant) -> None:
    registry, _ = await _registry(pool, tenant, platform=None)
    result = await registry.call("x.search", {"query": "agent cart approval"})
    assert result["stan"] == "NIEDOSTEPNE", result
    card = result["karty"][0]
    assert card["platforma"] == "X" and card["konto"] == ""
    assert [one["nazwa"] for one in card["akcje"]] == ["polacz", "sprawdz"]
    assert card["akcje"][0]["payload"] == {"platform": "X"}
    assert card["akcje"][1]["trasa"]["sciezka"] == "/api/x/session/confirm"


@pytest.mark.db
async def test_a_read_as_another_account_is_refused(pool, tenant, monkeypatch) -> None:
    registry, _ = await _registry(pool, tenant)
    source = DumbSource(XSessionState(True, "SomeoneElse", datetime.now(UTC)))
    _use(monkeypatch, source)
    result = await registry.call("x.read", {"url": POST_URL})
    assert result["stan"] == "NIEDOSTEPNE"
    assert source.reads == [], "cudzym kontem nie czytamy nic"


@pytest.mark.db
async def test_a_post_page_comes_back_with_relations_and_read_counters(
    pool, tenant, monkeypatch
) -> None:
    registry, _ = await _registry(pool, tenant)
    focal = XPost(
        "2103587050346250341",
        "jack",
        "Jack",
        "the question",
        "post",
        POST_URL,
        None,
        "focal",
        {"replies": 4},
    )
    reply = XPost("2103587050346250342", "ana", "Ana", "an answer", "post", None, None, "reply")
    source = DumbSource(XSessionState(True, HANDLE, datetime.now(UTC)), page=_page(focal, reply))
    _use(monkeypatch, source)

    first = await registry.call("x.read", {"url": POST_URL})
    second = await registry.call("x.search", {"query": "cart approval", "mode": "top"})

    assert first["stan"] == "WYKONANE", first
    assert [one["relation"] for one in first["posty"]] == ["focal", "reply"]
    assert first["posty"][0]["metrics"] == {"replies": 4}
    assert first["ograniczenia"] == ["Odczytano 2 postów z 2 widocznych."]
    assert second["stan"] == "WYKONANE"
    assert source.identity_reads == 1, "tożsamość raz na turę, nie przy każdym odczycie"


async def _seed_actuator_evidence(
    pool, tenant, *, account: str, external_id: str, seconds_ago: float
) -> None:
    """One row of what the actuator wrote, `seconds_ago` ago. The foreign keys to the attempt are
    not the subject here (the query reads the evidence alone), so they are switched off for this
    one transaction instead of rebuilding a whole approval chain around a row."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "INSERT INTO execution_evidence (organization_id, workspace_id, attempt_id, "
            "external_id, permalink, account, observed_at, source) "
            "VALUES ($1, $2, $3, $4, 'https://x.com/a/status/1', $5, "
            "now() - make_interval(secs => $6), 'ACTUATOR')",
            tenant["organization"],
            tenant["workspace"],
            uuid4(),
            external_id,
            account,
            seconds_ago,
        )


@pytest.mark.db
async def test_the_writers_proof_is_the_age_of_its_last_write_as_that_account(pool, tenant) -> None:
    from control_plane.workspace.account_state import writer_proof_age_s

    org = tenant["organization"]
    assert await writer_proof_age_s(pool, organization_id=org, platform="X", handle=HANDLE) is None
    await _seed_actuator_evidence(
        pool, tenant, account="@Demo_Agent", external_id="x:2109722413122371639", seconds_ago=300
    )
    await _seed_actuator_evidence(
        pool, tenant, account=HANDLE, external_id="x:2109722713741029594", seconds_ago=120
    )
    await _seed_actuator_evidence(
        pool, tenant, account=HANDLE, external_id="t1_reddit01", seconds_ago=10
    )
    x = await writer_proof_age_s(pool, organization_id=org, platform="X", handle="@DEMO_agent")
    assert x is not None and 115 <= x <= 135, "the newest X write, the handle in any case"
    reddit = await writer_proof_age_s(pool, organization_id=org, platform="REDDIT", handle=HANDLE)
    assert reddit is not None and 5 <= reddit <= 25, "a Reddit write is not an X proof, nor back"
    assert await writer_proof_age_s(pool, organization_id=org, platform="X", handle="other") is None
    assert (
        await writer_proof_age_s(pool, organization_id=uuid4(), platform="X", handle=HANDLE) is None
    ), "another organization's writes prove nothing here"
    assert await writer_proof_age_s(pool, organization_id=org, platform="X", handle=" ") is None


@pytest.mark.db
async def test_a_look_that_could_not_tell_is_not_a_lost_session_and_the_writer_can_vouch(
    pool, tenant, monkeypatch
) -> None:
    """13.10.2026 through the tool the model calls, against the real evidence table: the browser
    did not answer, so there is no verdict; with no write behind the account the agent is told to
    retry (not to log in), and once the actuator has published as the account the same call reads."""
    registry, _ = await _registry(pool, tenant)
    source = DumbSource(
        XSessionState(
            False,
            None,
            datetime.now(UTC),
            error="czytnik kapsuły nie odpowiedział w czasie",
            failure="TIMEOUT",
        ),
        page=_page(),
    )
    _use(monkeypatch, source)

    refused = await registry.call("x.read", {"url": POST_URL})
    assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == "SESJA_NIEPEWNA", refused
    assert "karty" not in refused and source.reads == []

    await _seed_actuator_evidence(
        pool, tenant, account=HANDLE, external_id="x:2109722413122371639", seconds_ago=90
    )
    read = await registry.call("x.read", {"url": POST_URL})
    assert read["stan"] == "WYKONANE", read
    assert source.reads == [(POST_URL, False)]
    assert any("publikowało" in line for line in read["ograniczenia"]), read["ograniczenia"]


@pytest.mark.db
async def test_an_x_refusal_keeps_its_code_and_says_not_to_retry(pool, tenant, monkeypatch) -> None:
    registry, _ = await _registry(pool, tenant)
    source = DumbSource(
        XSessionState(True, HANDLE, datetime.now(UTC)), page=XPlatformRefused(429, POST_URL)
    )
    _use(monkeypatch, source)
    result = await registry.call("x.search", {"query": "cart approval"})
    assert result["stan"] == "NIEDOSTEPNE" and result["kod_platformy"] == 429
    assert "Nie ponawiaj" in result["wskazowka"]


@pytest.mark.db
async def test_connecting_a_new_x_account_from_the_chat_gives_two_actions(pool, tenant) -> None:
    registry, _ = await _registry(pool, tenant, platform="REDDIT")
    result = await registry.call("accounts.connect", {"platform": "X"})
    assert result["stan"] == "OCZEKUJE", result
    card = result["karty"][0]
    assert card["platforma"] == "X" and card["stan"] == "brak_polaczenia"
    assert [one["nazwa"] for one in card["akcje"]] == ["polacz", "sprawdz"]


@pytest.mark.db
async def test_an_x_reply_draft_aims_at_the_post_named_by_its_address(pool, tenant) -> None:
    registry, _ = await _registry(pool, tenant)
    result = await registry.call(
        "draft.create",
        {
            "text": "Separating the proposal from the effect helped us most.",
            "account": HANDLE,
            "target_url": POST_URL + "?s=46",
            # A second field the model could fill differently: ignored, the address decides.
            "target_external_id": "t3_abc123",
        },
    )
    assert result["stan"] == "OCZEKUJE", result
    assert result["propozycja"]["cel"] == "x:2103587050346250341"
    assert result["karty"][0]["tytul"] == "Szkic odpowiedzi na X"


@pytest.mark.db
async def test_an_own_x_post_has_no_title_and_respects_the_weighted_limit(pool, tenant) -> None:
    registry, _ = await _registry(pool, tenant)
    with_title = await registry.call(
        "draft.create",
        {
            "kind": "OWN_POST",
            "text": "Question for agent builders.",
            "title": "T",
            "account": HANDLE,
        },
    )
    assert with_title["stan"] == "NIEDOSTEPNE" and "tytuł" in with_title["powod"]

    too_long = await registry.call(
        "draft.create", {"kind": "OWN_POST", "text": "x" * 281, "account": HANDLE}
    )
    assert too_long["stan"] == "NIEDOSTEPNE" and "281" in too_long["powod"]

    fine = await registry.call(
        "draft.create",
        {"kind": "OWN_POST", "text": "Question for agent builders.", "account": HANDLE},
    )
    assert fine["stan"] == "OCZEKUJE", fine
    assert fine["propozycja"]["cel"] == f"x:@{HANDLE}"


@pytest.mark.db
async def test_the_founders_x_post_is_refused_at_the_draft_and_the_shorter_one_is_not(
    pool, tenant
) -> None:
    """The regression of 11.10.2026 through the tool the model calls: 275 characters with a bare
    domain is 285 as X counts it, so no consent is ever spent on it."""
    registry, _ = await _registry(pool, tenant)
    refused = await registry.call(
        "draft.create", {"kind": "OWN_POST", "text": FOUNDER_POST, "account": HANDLE}
    )
    assert refused["stan"] == "NIEDOSTEPNE", refused
    assert "285" in refused["powod"]

    reply = await registry.call(
        "draft.create", {"text": FOUNDER_POST, "account": HANDLE, "target_url": POST_URL}
    )
    assert reply["stan"] == "NIEDOSTEPNE" and "285" in reply["powod"], reply

    fine = await registry.call(
        "draft.create",
        {
            "kind": "OWN_POST",
            "text": FOUNDER_POST.removesuffix(" getwenext.com"),
            "account": HANDLE,
        },
    )
    assert fine["stan"] == "OCZEKUJE", fine


@pytest.mark.db
async def test_an_own_x_post_carries_one_screenshot_and_a_reply_carries_none(pool, tenant) -> None:
    """27.09: a screenshot (a `browser.inspect` artifact) rides under an own X post. The consent
    covers the text AND the bytes: the field ends in the image line with the artifact's checksum,
    the card and `drafts.read` show the text without it and name the image apart."""
    from uuid import UUID

    from control_plane.pilot.publication import MediaRef, split_media
    from control_plane.storage.artifacts import ArtifactStore
    from control_plane.storage.config import ArtifactAcl, ArtifactKind

    registry, _ = await _registry(pool, tenant)
    async with pool.acquire() as conn:
        kept = await ArtifactStore().record(
            conn,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            kind=ArtifactKind.SOURCE,
            acl=ArtifactAcl.OPERATOR_AND_RUNNER,
            content=b"\x89PNG\r\n\x1a\n" + b"\x02" * 64,
            encrypted=False,
            retention_until=datetime.now(UTC).replace(year=2099),
        )
    caption = "Nowy model agentów wyszedł dziś rano. Najciekawsze jest to, czego nie pokazali."

    result = await registry.call(
        "draft.create",
        {"kind": "OWN_POST", "text": caption, "account": HANDLE, "image": str(kept.artifact_id)},
    )
    assert result["stan"] == "OCZEKUJE", result
    assert result["propozycja"]["obraz"] == str(kept.artifact_id)
    card = result["karty"][0]
    assert card["tresc"] == caption and card["obraz"] == str(kept.artifact_id)
    revision_id = result["propozycja"]["revision_id"]
    async with pool.acquire() as conn:
        field = await conn.fetchval(
            "SELECT payload ->> 'text_and_media' FROM action_proposal_revisions WHERE id = $1",
            UUID(revision_id),
        )
    assert split_media(field) == (caption, MediaRef(kept.artifact_id, kept.checksum))

    read = await registry.call("drafts.read", {"revision_id": revision_id})
    assert read["szkic"]["tresc"] == caption, read
    assert read["szkic"]["obraz"] == str(kept.artifact_id)

    reply = await registry.call(
        "draft.create",
        {
            "text": "Zgoda.",
            "account": HANDLE,
            "target_url": POST_URL,
            "image": str(kept.artifact_id),
        },
    )
    assert reply["stan"] == "BLAD" and "tylko do własnego posta" in reply["powod"], reply

    missing = await registry.call(
        "draft.create",
        {"kind": "OWN_POST", "text": "Inny tekst.", "account": HANDLE, "image": str(uuid4())},
    )
    assert missing["stan"] == "NIEDOSTEPNE" and "nie ma takiego obrazu" in missing["powod"]


@pytest.mark.db
async def test_an_issued_request_nudges_the_publisher_once_per_revision(pool, tenant) -> None:
    """D-030 for delegated requests: a request issued by `drafts.publish` gets one publisher tick
    now, keyed by its revision, instead of the next ten-minute slot (27.09)."""
    from types import SimpleNamespace

    registry, _ = await _registry(pool, tenant)
    revision = uuid4()
    await registry._nudge_publisher([SimpleNamespace(revision_id=revision)])
    await registry._nudge_publisher([SimpleNamespace(revision_id=revision)])
    async with pool.acquire() as conn:
        keys = await conn.fetch(
            "SELECT idempotency_key FROM jobs WHERE organization_id = $1 "
            "AND job_type = 'pilot.publish'",
            tenant["organization"],
        )
    assert [row["idempotency_key"] for row in keys] == [
        f"publish:{tenant['organization']}:nudge:issued:{revision}"
    ], "jedno szturchnięcie na rewizję, drugie wywołanie niczego nie dokłada"


@pytest.mark.db
async def test_a_refused_call_is_not_already_done_and_a_written_one_is(pool, tenant) -> None:
    """27.09: `drafts.publish` refused by the hourly limit, the limit freed, the agent asked again
    and got the refusal back as "already done": nothing had been written the first time."""
    import json as json_

    from control_plane.tools.registry import _digest

    registry, _ = await _registry(pool, tenant)
    args = {"revision_id": str(uuid4())}

    async def trace(seq: int, kind: str, payload: dict) -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO session_events (organization_id, workspace_id, session_id, seq, "
                "kind, payload) VALUES ($1, $2, $3, $4, $5, $6::jsonb)",
                tenant["organization"],
                tenant["workspace"],
                registry.capability.session_id,
                seq,
                kind,
                json_.dumps(payload),
            )

    call = {"tool": "drafts.publish", "args_sha256": _digest("drafts.publish", args)}
    await trace(1, "TOOL_CALL", call)
    await trace(
        2,
        "TOOL_RESULT",
        {
            "tool": "drafts.publish",
            "status": "completed",
            "outcome": "NIEDOSTEPNE",
            "result": {"stan": "NIEDOSTEPNE"},
        },
    )
    assert await registry._already_done("drafts.publish", args) is None, (
        "odmowa niczego nie zapisała"
    )

    await trace(3, "TOOL_CALL", call)
    await trace(
        4,
        "TOOL_RESULT",
        {
            "tool": "drafts.publish",
            "status": "completed",
            "outcome": "OCZEKUJE",
            "result": {"stan": "OCZEKUJE"},
        },
    )
    repeated = await registry._already_done("drafts.publish", args)
    assert repeated is not None and repeated["poprzedni_wynik"] == {"stan": "OCZEKUJE"}


@pytest.mark.db
@pytest.mark.parametrize("with_call_id", [True, False], ids=["call_id", "legacy_rows"])
async def test_a_call_whose_own_result_failed_is_not_done_by_another_calls_success(
    pool, tenant, with_call_id: bool
) -> None:
    """12.10: six `draft.create` calls failed on a missing migration and were repeated after it.
    The first repeat succeeded; every later one came back as "already done" with THAT result,
    because the pairing took any later successful result of the same tool, and no draft was
    written for them. The sequence is the one of the day: A and B fail, A succeeds, B is asked
    again and must be neither done nor silently dropped."""
    import json as json_
    from uuid import UUID

    from control_plane.sessions.trace_payloads import tool_call_payload, tool_result_payload
    from control_plane.tools.registry import render_content

    registry, _ = await _registry(pool, tenant)
    a = {"text": "Pierwszy.", "account": HANDLE, "target_url": POST_URL}
    b = {
        "text": "Drugi.",
        "account": HANDLE,
        "target_url": "https://x.com/martinleblanc/status/2103587050346250342",
    }

    async def trace(kind: str, payload: dict) -> None:
        if not with_call_id:
            payload = {k: v for k, v in payload.items() if k != "call_id"}
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO session_events (organization_id, workspace_id, session_id, seq, "
                "kind, payload) VALUES ($1, $2, $3, "
                "(SELECT coalesce(max(seq), 0) + 1 FROM session_events WHERE session_id = $3), "
                "$4, $5::jsonb)",
                tenant["organization"],
                tenant["workspace"],
                registry.capability.session_id,
                kind,
                json_.dumps(payload),
            )

    async def failing_call(args: dict) -> None:
        item = {"id": uuid4().hex, "tool": "draft.create", "server": "wenext", "arguments": args}
        await trace("TOOL_CALL", tool_call_payload(item))
        item.update(status="failed", error="UndefinedColumnError: missing migration", result=None)
        await trace("TOOL_RESULT", tool_result_payload(item))

    async def real_call(args: dict) -> dict:
        """What the plugin server does around `registry.call`: call row, the call, result row."""
        item = {"id": uuid4().hex, "tool": "draft.create", "server": "wenext", "arguments": args}
        await trace("TOOL_CALL", tool_call_payload(item))
        result = await registry.call("draft.create", args)
        item.update(
            status="completed",
            result={"content": render_content(result), "isError": False},
        )
        await trace("TOOL_RESULT", tool_result_payload(item))
        return result

    await failing_call(a)
    await failing_call(b)
    assert await registry._already_done("draft.create", a) is None
    assert await registry._already_done("draft.create", b) is None

    first = await real_call(a)
    assert first["stan"] == "OCZEKUJE", first
    # A is done now, with its OWN result ...
    done_a = await registry._already_done("draft.create", a)
    assert done_a is not None and done_a["stan"] == "WYKONANE"
    # ... and B, whose own result failed, is not, though a successful draft.create is later.
    assert await registry._already_done("draft.create", b) is None

    second = await real_call(b)
    assert second["stan"] == "OCZEKUJE" and "powtorzone" not in second, second
    assert second["propozycja"]["revision_id"] != first["propozycja"]["revision_id"]
    assert second["propozycja"]["cel"] == "x:2103587050346250342"
    async with pool.acquire() as conn:
        drafts = await conn.fetchval(
            "SELECT count(*) FROM action_proposal_revisions WHERE id = ANY($1::uuid[])",
            [UUID(first["propozycja"]["revision_id"]), UUID(second["propozycja"]["revision_id"])],
        )
    assert drafts == 2, "każdy z dwóch różnych celów ma własny szkic"
    # And a genuine repeat of B is now recognised, with B's result and not A's.
    repeated = await registry._already_done("draft.create", b)
    assert repeated is not None
    assert "x:2103587050346250342" in json_.dumps(repeated["poprzedni_wynik"])


@pytest.mark.db
async def test_an_x_post_page_gives_the_post_alone_as_a_second_image(
    pool, tenant, tmp_path, monkeypatch
) -> None:
    """27.09: on one X post's page the research browser also shoots the post alone. The tool
    keeps it as its own artifact, returns `post_screenshot_artifact_id` and shows the model that
    image; a broken second shot is dropped and the viewport shot still stands."""
    import base64
    import json

    import httpx

    registry, _ = await _registry(pool, tenant)
    viewport, post = b"\x89PNG\r\n\x1a\nviewport", b"\x89PNG\r\n\x1a\npost-only"
    answer: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/research"
        return httpx.Response(200, json=answer)

    real_client = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setenv("WENEXT_BROWSER_READER_URL", "http://capsule:8765")
    monkeypatch.setenv("WENEXT_BROWSER_READER_TOKEN", "t")
    monkeypatch.setenv("WENEXT_ARTIFACTS_DIR", str(tmp_path))
    page = {
        "url": POST_URL,
        "title": "Post",
        "status": 200,
        "partial": False,
        "text": "Separating the proposal from the effect.",
        "screenshot": base64.b64encode(viewport).decode("ascii"),
        "post_screenshot": base64.b64encode(post).decode("ascii"),
        "post_id": "2103587050346250341",
    }
    answer.update(page)

    seen = await registry.call("browser.inspect", {"url": POST_URL, "javascript": True})

    assert seen["stan"] == "WYKONANE", seen
    assert seen["post_screenshot_artifact_id"] not in (None, seen["screenshot_artifact_id"])
    async with pool.acquire() as conn:
        sizes = {
            str(row["id"]): row["size_bytes"]
            for row in await conn.fetch(
                "SELECT id, size_bytes FROM artifacts WHERE organization_id = $1",
                tenant["organization"],
            )
        }
    assert sizes[seen["post_screenshot_artifact_id"]] == len(post)
    assert sizes[seen["screenshot_artifact_id"]] == len(viewport)
    assert base64.b64decode(seen["_mcp_images"][0]["data"]) == post
    assert "post_screenshot" not in json.dumps(seen["page"])

    answer.clear()
    answer.update({**page, "url": POST_URL + "?x=1", "post_screenshot": "nie-base64!!"})
    broken = await registry.call("browser.inspect", {"url": POST_URL + "?x=1", "javascript": True})
    assert broken["stan"] == "WYKONANE", broken
    assert broken["post_screenshot_artifact_id"] is None
    assert base64.b64decode(broken["_mcp_images"][0]["data"]) == viewport
