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
