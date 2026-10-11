"""The plugin server as the host agent meets it: a real process, stdio, a real database.

THE UNIT TESTS OF THIS FEATURE ARE EACH GREEN WITHOUT THIS ONE, and that is the reason it exists
(KONWENCJE: klej między sprawdzonymi częściami). The session store, the capability, the narrowed
catalog, the shared trace payloads and the MCP loop all have their own tests. What none of them
shows is that a `tools/call` made over a pipe by a process that is NOT the test ends up as a draft
card on the dashboard, and that the same call asked twice is one draft. So this starts
`python -m control_plane.tools.plugin_server serve` exactly as the launcher does (minus
`docker compose exec`), talks MCP to it, and reads what it left in the database.

WHAT IT DOES NOT SHOW: `docker compose exec` itself, the launcher, and Codex. The first two are
covered by `test_plugin_launcher.py` with a fake `docker`; the third is not covered at all here.
"""

from __future__ import annotations

import json
import os
import queue
import stat
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from control_plane.sessions.projection import cards_for
from control_plane.sessions.store import SessionStore
from control_plane.sessions.trace_payloads import args_digest
from control_plane.tools import APP_ONLY_TOOLS, PLUGIN_TOOLS, load_from_file

pytestmark = [pytest.mark.db, pytest.mark.p2]

CONTROL_PLANE = Path(__file__).resolve().parents[1]
WAIT_S = 60.0

DRAFT_ARGS = {
    "target_external_id": "t3_plugin01",
    "target_url": "https://www.reddit.com/r/devops/comments/plugin01/x/",
    "text": "Odpowiedź przygotowana przez agenta człowieka.",
    "account": "PluginMaintainer",
}


class _Mcp:
    """A plugin server process with a line reader on each pipe, so a wait has a deadline."""

    def __init__(self, env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "control_plane.tools.plugin_server", "serve"],
            cwd=CONTROL_PLANE,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.frames: queue.Queue[str] = queue.Queue()
        self.raw_stdout: list[str] = []
        self.stderr_lines: list[str] = []
        self._ident = 0
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.raw_stdout.append(line)
            self.frames.put(line)

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr_lines.append(line)

    def send(self, method: str, params: dict | None = None, *, notify: bool = False) -> int | None:
        frame: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            frame["params"] = params
        ident = None
        if not notify:
            self._ident += 1
            ident = frame["id"] = self._ident
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(frame) + "\n")
        self.proc.stdin.flush()
        return ident

    def reply(self, ident: int | None) -> dict:
        try:
            line = self.frames.get(timeout=WAIT_S)
        except queue.Empty:
            self.proc.kill()
            raise AssertionError(
                f"serwer nie odpowiedział na {ident} w {WAIT_S} s; stderr: "
                f"{''.join(self.stderr_lines)[-800:]}"
            ) from None
        frame = json.loads(line)
        assert frame["id"] == ident, frame
        return frame

    def call(self, name: str, arguments: dict) -> dict:
        return self.reply(self.send("tools/call", {"name": name, "arguments": arguments}))

    def finish(self) -> int:
        """EOF on stdin is how a host ends a session; the server must leave by itself."""
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        try:
            return self.proc.wait(timeout=WAIT_S)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            raise AssertionError("serwer nie wyszedł po EOF na stdin") from None


def _content(response: dict) -> dict:
    """The JSON a tool returned, out of the MCP envelope."""
    return json.loads(response["result"]["content"][0]["text"])


async def _seed(pool, tenant) -> dict:
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel projektu') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"plugin-{uuid4().hex[:8]}",
        )
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id, state) "
            "VALUES ($1, 'REDDIT', $2, 'READY') RETURNING id",
            tenant["organization"],
            DRAFT_ARGS["account"],
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            account_id,
        )
    return {"project": project_id, "account": account_id}


def _env(tenant, seeded, run_dir: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
    env.update(
        {
            "DATABASE_URL": os.environ["WENEXT_TEST_DSN"],
            "WENEXT_ORGANIZATION": str(tenant["organization"]),
            "WENEXT_PROJECT": str(seeded["project"]),
            "WENEXT_HOST": "codex",
            "WENEXT_SESSION_RUN_DIR": str(run_dir),
            "WENEXT_DASHBOARD_URL": "http://127.0.0.1:3999",
            "PYTHONPATH": str(CONTROL_PLANE),
        }
    )
    return env


async def test_a_host_agent_session_end_to_end(pool, tenant, tmp_path: Path) -> None:
    seeded = await _seed(pool, tenant)
    run_dir = tmp_path / "run"
    server = _Mcp(_env(tenant, seeded, run_dir))
    try:
        # -- the handshake, and the capability file the server wrote for itself ------------------
        initialized = server.reply(
            server.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        )
        assert initialized["result"]["serverInfo"]["name"] == "wenext"
        server.send("notifications/initialized", notify=True)

        files = sorted(run_dir.glob("*.json"))
        assert len(files) == 1, f"jeden plik zdolności na proces, jest {files}"
        assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
        capability = load_from_file(files[0])
        assert capability.host == "codex"
        assert capability.project_id == seeded["project"]
        assert files[0].name == f"{capability.session_id}-{server.proc.pid}.json", (
            "plik ma nazwę procesu: dwa okna jednej sesji nie mogą sobie nawzajem go usuwać"
        )

        # -- the narrowed catalog ----------------------------------------------------------------
        listed = server.reply(server.send("tools/list"))
        names = {tool["name"] for tool in listed["result"]["tools"]}
        # What the agent may use is the allowlist. The card's own tools ride in the same list,
        # marked `visibility: ["app"]` (MCP Apps), which the host must keep from the agent; they
        # are exactly the three of `card_send` (`test_the_cards_tools_in_the_real_process`).
        app_only = {
            tool["name"]
            for tool in listed["result"]["tools"]
            if tool.get("_meta", {}).get("ui", {}).get("visibility") == ["app"]
        }
        assert app_only == APP_ONLY_TOOLS
        assert names - app_only == PLUGIN_TOOLS and len(names - app_only) == 47
        assert "drafts.publish" not in names

        # -- an excluded tool is a protocol error, not content -----------------------------------
        refused = server.call("drafts.publish", {})
        assert refused["error"]["code"] == -32601, refused
        assert "nie jest dostępne" in refused["error"]["message"]

        # -- a read, then the one write a host agent has -----------------------------------------
        project = server.call("project.read", {})
        assert project["result"]["isError"] is False
        assert _content(project)["stan"] == "WYKONANE"

        draft = server.call("draft.create", DRAFT_ARGS)
        assert draft["result"]["isError"] is False
        created = _content(draft)
        assert created["stan"] == "OCZEKUJE", created
        revision = created["propozycja"]["revision_id"]
        assert [card["rodzaj"] for card in created["karty"]] == ["SZKIC"]

        # -- the same call again is the same draft -----------------------------------------------
        repeated = _content(server.call("draft.create", DRAFT_ARGS))
        assert repeated.get("powtorzone") is True, (
            "powtórzone żądanie zapisu wykonało się drugi raz: ślad nie niesie koperty wyniku "
            f"w kształcie, który rozpoznaje `_already_done`: {repeated}"
        )
        assert repeated["poprzedni_wynik"]["content"][0]["type"] == "text"

        assert server.finish() == 0, "".join(server.stderr_lines)[-800:]
    finally:
        if server.proc.poll() is None:
            server.proc.kill()

    # -- stdout was the protocol and nothing else ------------------------------------------------
    assert all(json.loads(line)["jsonrpc"] == "2.0" for line in server.raw_stdout if line.strip())
    # -- and the capability, with its DSN, is gone -----------------------------------------------
    assert list(run_dir.glob("*.json")) == [], "plik zdolności z DSN został po zamknięciu serwera"

    # -- what the person sees on the dashboard ---------------------------------------------------
    store = SessionStore(pool)
    session = await store.get(
        organization_id=tenant["organization"], session_id=capability.session_id
    )
    assert session is not None
    assert session.kind == "CONVERSATION" and session.status == "WAITING_USER"
    assert session.budget == {"host": "codex"} and session.codex_connection_id is None

    events = await store.events(
        organization_id=tenant["organization"], session_id=session.id, limit=100
    )
    kinds = [event.kind for event in events]
    assert "USER_MESSAGE" not in kinds, "zalążek wtyczki udaje wypowiedź człowieka"
    assert kinds == [
        "STATUS",
        "TOOL_CALL", "TOOL_RESULT",  # drafts.publish, refused
        "TOOL_CALL", "TOOL_RESULT",  # project.read
        "TOOL_CALL", "TOOL_RESULT",  # draft.create
        "TOOL_CALL", "TOOL_RESULT",  # draft.create again
    ], kinds  # fmt: skip

    publish_call, publish_result, _, _, draft_call, draft_result, again_call, again_result = events[
        1:
    ]
    assert publish_call.payload["tool"] == "drafts.publish"
    assert publish_result.payload["status"] == "failed", "odmowa to nie 'wykonane'"
    assert draft_call.payload["args_sha256"] == args_digest("draft.create", DRAFT_ARGS)
    assert draft_call.payload["summary"] == "przygotował propozycję tekstu"
    assert draft_result.payload["tool"] == "draft.create"
    assert draft_result.payload["status"] == "completed"
    assert draft_result.payload["outcome"] == "OCZEKUJE"
    assert draft_result.payload["result"]["content"][0]["type"] == "text"
    assert [card["rodzaj"] for card in draft_result.payload["karty"]] == ["SZKIC"], (
        "karta szkicu nie dojechała do śladu: dashboard nie pokaże człowiekowi nic do zatwierdzenia"
    )
    assert again_call.payload["args_sha256"] == draft_call.payload["args_sha256"]
    assert again_result.payload["outcome"] == "WYKONANE"

    cards = await cards_for(
        pool,
        list(events),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=seeded["project"],
    )
    drafts = [card for card in cards if card["rodzaj"] == "SZKIC"]
    assert len(drafts) == 1, f"ekran rozmowy pokaże {len(drafts)} szkiców, ma być jeden"
    assert drafts[0]["rewizja"] == revision

    async with pool.acquire() as conn:
        proposals = await conn.fetchval(
            "SELECT count(*) FROM action_proposals WHERE organization_id = $1 "
            "AND social_account_id = $2",
            tenant["organization"],
            seeded["account"],
        )
        publications = await conn.fetchval(
            "SELECT count(*) FROM publication_requests WHERE organization_id = $1",
            tenant["organization"],
        )
        audits = await conn.fetch(
            "SELECT actor_ref, subject_ref, payload FROM audit_events WHERE organization_id = $1 "
            "AND kind = 'PLUGIN_SESSION_STARTED'",
            tenant["organization"],
        )
    assert proposals == 1, "powtórzone draft.create założyło drugą propozycję"
    assert publications == 0, "wtyczka nic nie opublikowała"
    assert [(row["actor_ref"], row["subject_ref"]) for row in audits] == [
        ("plugin:codex", f"session:{session.id}")
    ]

    # -- and the person is told where to look, on stderr -----------------------------------------
    expected_url = (
        f"http://127.0.0.1:3999/w/{tenant['workspace']}/p/{seeded['project']}/c/{session.id}"
    )
    assert any(expected_url in line for line in server.stderr_lines), server.stderr_lines


async def test_a_second_start_joins_the_same_conversation(pool, tenant, tmp_path: Path) -> None:
    """The plugin starts every time the person opens their agent; the project must not fill up
    with one empty conversation per start."""
    seeded = await _seed(pool, tenant)
    sessions = []
    for _ in range(2):
        server = _Mcp(_env(tenant, seeded, tmp_path / "run"))
        try:
            server.reply(
                server.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
            )
            assert server.finish() == 0, "".join(server.stderr_lines)[-800:]
        finally:
            if server.proc.poll() is None:
                server.proc.kill()
        sessions.append(server)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM agent_sessions WHERE organization_id = $1 AND project_id = $2",
            tenant["organization"],
            seeded["project"],
        )
        audits = await conn.fetch(
            "SELECT subject_ref, payload FROM audit_events WHERE organization_id = $1 "
            "AND kind = 'PLUGIN_SESSION_STARTED' ORDER BY occurred_at",
            tenant["organization"],
        )
    assert len(rows) == 1, f"dwa starty, {len(rows)} rozmów"
    assert len(audits) == 2, "każde wydanie zdolności ma wpis w dzienniku"
    flags = [
        (json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"])[
            "created"
        ]
        for row in audits
    ]
    assert flags == [True, False]
    assert any("ta sama rozmowa" in line for line in sessions[1].stderr_lines)


async def test_a_project_that_cannot_be_chosen_refuses_before_serving(
    pool, tenant, tmp_path: Path
) -> None:
    """Exit 4, a sentence on stderr that says what to set, and NOTHING on stdout."""
    seeded = await _seed(pool, tenant)
    env = _env(tenant, seeded, tmp_path / "run")
    env["WENEXT_PROJECT"] = "Projekt, którego nie ma"
    done = subprocess.run(
        [sys.executable, "-m", "control_plane.tools.plugin_server", "serve"],
        cwd=CONTROL_PLANE,
        env=env,
        input="",
        capture_output=True,
        text=True,
        timeout=WAIT_S,
    )
    assert done.returncode == 4, (done.returncode, done.stdout, done.stderr)
    assert done.stdout == ""
    assert "Nie ma projektu" in done.stderr
    assert not (tmp_path / "run").exists() or list((tmp_path / "run").glob("*.json")) == []


async def test_projects_prints_one_line_per_project_as_id_tab_name(
    pool, tenant, tmp_path: Path
) -> None:
    """The contract with the launcher: `<id>\t<name>` on stdout, one project per line, and
    nothing else there. An archived project is not listed."""
    seeded = await _seed(pool, tenant)
    async with pool.acquire() as conn:
        archived = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal, archived_at) "
            "VALUES ($1, $2, 'Zarchiwizowany', '', now()) RETURNING id",
            tenant["organization"],
            tenant["workspace"],
        )
        name = await conn.fetchval("SELECT name FROM projects WHERE id = $1", seeded["project"])
    done = subprocess.run(
        [sys.executable, "-m", "control_plane.tools.plugin_server", "projects"],
        cwd=CONTROL_PLANE,
        env=_env(tenant, seeded, tmp_path / "run"),
        input="",
        capture_output=True,
        text=True,
        timeout=WAIT_S,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == f"{seeded['project']}\t{name}\n", done.stdout
    assert str(archived) not in done.stdout


async def test_a_capability_file_that_cannot_be_written_is_a_clean_exit_2(
    pool, tenant, tmp_path: Path
) -> None:
    """A run directory under a regular file: `mkdir` fails. Exit 2 with a sentence, nothing on
    stdout, and no capability left behind."""
    seeded = await _seed(pool, tenant)
    blocker = tmp_path / "plik"
    blocker.write_text("to nie katalog", encoding="utf-8")
    env = _env(tenant, seeded, blocker / "run")
    done = subprocess.run(
        [sys.executable, "-m", "control_plane.tools.plugin_server", "serve"],
        cwd=CONTROL_PLANE,
        env=env,
        input="",
        capture_output=True,
        text=True,
        timeout=WAIT_S,
    )
    assert done.returncode == 2, (done.returncode, done.stdout, done.stderr[-400:])
    assert done.stdout == ""
    assert "Nie mogę zapisać pliku zdolności" in done.stderr


async def test_the_cards_tools_in_the_real_process(pool, tenant, tmp_path: Path) -> None:
    """The card's three tools, through the real stdio server: listed for the card only, refused
    without a human's confirmation, and the secret argument never reaches the trace."""
    seeded = await _seed(pool, tenant)
    run_dir = tmp_path / "run"
    server = _Mcp(_env(tenant, seeded, run_dir))
    try:
        server.reply(
            server.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        )
        server.send("notifications/initialized", notify=True)
        listed = server.reply(server.send("tools/list"))["result"]["tools"]
        by_name = {tool["name"]: tool for tool in listed}
        for name in APP_ONLY_TOOLS:
            assert by_name[name]["_meta"] == {"ui": {"visibility": ["app"]}}, name

        # `catalog.list` is what the MODEL reads: it never names the card's tools.
        catalog = _content(server.call("catalog.list", {}))
        assert not {one["name"] for one in catalog["narzedzia"]} & APP_ONLY_TOOLS
        described = _content(server.call("catalog.describe", {"tool": "room.send_draft"}))
        assert described["stan"] == "BLAD", "model nie ma się dowiedzieć, jak działa wysyłka"

        # A send with a guessed confirmation is refused, with the person-facing sentence.
        refused = server.call(
            "room.send_draft", {"revision_id": str(uuid4()), "token": "zgadnięte-przez-model"}
        )
        answer = _content(refused)
        assert answer["stan"] == "NIEDOSTEPNE" and answer["kod"] == "POTWIERDZENIE", answer
        assert "_meta" not in refused["result"], "odmowa nie niesie potwierdzenia"
        assert server.finish() == 0, "".join(server.stderr_lines)[-800:]
    finally:
        if server.proc.poll() is None:
            server.proc.kill()

    store = SessionStore(pool)
    sessions = await pool.fetch(
        "SELECT id FROM agent_sessions WHERE organization_id = $1 AND project_id = $2",
        tenant["organization"],
        seeded["project"],
    )
    events = []
    for one in sessions:
        events += await store.events(
            organization_id=tenant["organization"], session_id=one["id"], limit=100
        )
    sent = [e for e in events if e.kind == "TOOL_CALL" and e.payload["tool"] == "room.send_draft"]
    assert len(sent) == 1
    assert "zgadnięte-przez-model" not in json.dumps(sent[0].payload, ensure_ascii=False), (
        "tajny argument karty trafił do śladu, który czyta ekran i `sessions.read`"
    )
    assert sent[0].payload["arguments"]["token"] == "[pominięte w śladzie]"
    assert sent[0].payload["args_sha256"] == args_digest(
        "room.send_draft",
        {
            "revision_id": sent[0].payload["arguments"]["revision_id"],
            "token": "zgadnięte-przez-model",
        },
    ), "`_already_done` dopasowuje po odcisku prawdziwych argumentów"
