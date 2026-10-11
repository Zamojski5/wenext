"""The plugin launcher (`plugins/wenext/bin/wenext`) against a FAKE `docker` on PATH.

WHAT IS UNDER TEST is the part of the plugin that no Python test of the control plane reaches: a
POSIX shell script that runs on the person's machine before any of our code does. Its failures
are the ones a founder meets first, and each has a sentence that says what to do.

The fake is deliberately dumb and deliberately hostile in one way: every call EXCEPT `serve`
READS A LINE FROM STDIN, as the real `docker compose exec -T` would, so a preflight call that
forgot `</dev/null` swallows the first frame of the MCP stream here exactly as it would on a real
machine (`test_the_mcp_stream_reaches_the_server_untouched`).

WHAT IT CANNOT SHOW: that real Docker behaves like the fake. The shape of `compose ps -q`,
`docker inspect --format` and `exec -T` is the documented one, and the launcher was run once
against a real Docker with the stack down (the refusal path); the healthy path against the live
stack is a step of its own.

Three properties are asserted on EVERY refusal, because they are the contract with the host:
stdout is empty (it is the MCP channel), the message is Polish and carries the sentence that says
what to do, and it contains neither U+2013 nor U+2014. A fourth is asserted with them: nothing is
left behind in the temporary directory.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "plugins" / "wenext" / "bin" / "wenext"
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")

#: The tools the launcher itself needs, linked into a private directory so a test can build a PATH
#: that has them and has no `docker` (a host with Docker in /usr/local/bin could not otherwise test
#: a missing one). A tool the launcher starts using without being listed here fails every test.
NEEDED = (
    "sh", "sed", "head", "tail", "cut", "grep", "awk", "mktemp", "rm", "mkdir", "dirname",
    "cat", "sleep", "kill", "mv", "tr", "date",
)  # fmt: skip

FAKE_DOCKER = r'''
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
env = os.environ


def say(kind: str) -> None:
    line = {
        "kind": kind,
        "argv": args,
        "docker_config": env.get("DOCKER_CONFIG"),
        "path": env.get("PATH"),
    }
    with open(env["FAKE_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(line) + "\n")


def sequence_state(name: str) -> str:
    """The Nth answer of a comma separated sequence, N counted across calls of this fake."""
    counter = Path(env["FAKE_STATE"]) / "inspect_calls"
    seen = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(seen + 1))
    values = env.get(name, "healthy").split(",")
    return values[min(seen, len(values) - 1)]


kind = args[0] if args else ""
rest = args[1:]
if kind == "compose":
    index = 1
    while index < len(args) and args[index] in ("-f", "-p", "--project-directory"):
        index += 2
    kind = "compose " + (args[index] if index < len(args) else "")
    rest = args[index + 1 :]

say(kind)

if kind != "compose exec" or rest[-1] != "serve":
    # What the real `exec -T` does with a stdin it is given, and what any careless client does.
    sys.stdin.readline()

if kind == "info":
    if env.get("FAKE_INFO_SLEEP"):
        time.sleep(float(env["FAKE_INFO_SLEEP"]))
    sys.exit(int(env.get("FAKE_INFO_STATUS", "0")))
if kind == "inspect":
    if env.get("FAKE_INSPECT_SLEEP"):
        time.sleep(float(env["FAKE_INSPECT_SLEEP"]))
    if env.get("FAKE_INSPECT_STATUS"):
        sys.exit(int(env["FAKE_INSPECT_STATUS"]))
    print(sequence_state("FAKE_HEALTH"))
    sys.exit(0)
if kind == "compose ps":
    if env.get("FAKE_PS_STATUS"):
        sys.exit(int(env["FAKE_PS_STATUS"]))
    print(env.get("FAKE_PS_ID", "abc123def456"), end="")
    sys.exit(0)
if kind == "compose exec" and rest[-1] == "serve":
    print(env.get("FAKE_SERVE_STDERR", "serwer wystartował"), file=sys.stderr)
    sys.stdout.write(sys.stdin.read())
    sys.stdout.flush()
    sys.exit(int(env.get("FAKE_SERVE_STATUS", "0")))
if kind == "compose exec" and rest[-1] == "projects":
    sys.stdout.write(env.get("FAKE_PROJECTS", ""))
    sys.exit(int(env.get("FAKE_PROJECTS_STATUS", "0")))
print("fake docker: nieobsłużone wywołanie: " + " ".join(args), file=sys.stderr)
sys.exit(99)
'''

ONE = "11111111-1111-1111-1111-111111111111"
TWO = "22222222-2222-2222-2222-222222222222"

DASHES = [None, *([shutil.which("dash")] if shutil.which("dash") else [])]

# The tests image (tests/Dockerfile) does not copy `plugins/`, and it has no `tools/dev` runner
# either. Skipping is honest THERE; in a checkout (the runner is present) a missing launcher is a
# failure, never a skip.
pytestmark = pytest.mark.skipif(
    not LAUNCHER.is_file() and not (ROOT / "tools" / "dev" / "run_all_tests.sh").is_file(),
    reason=f"brak {LAUNCHER}: plugins/ nie jest kopiowane do obrazu tests",
)


@pytest.fixture
def kit(tmp_path: Path) -> dict:
    """A fake checkout, a fake docker, a private PATH, an empty HOME and an empty TMPDIR."""
    home = tmp_path / "checkout"
    (home / "infra" / "docker-config").mkdir(parents=True)
    (home / "docker-compose.yml").write_text("name: wenext\n", encoding="utf-8")
    (home / ".env").write_text("POSTGRES_PASSWORD=x\n", encoding="utf-8")
    (home / "infra" / "docker-config" / "config.json").write_text("{}\n", encoding="utf-8")
    user = tmp_path / "home"
    user.mkdir()
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    state = tmp_path / "state"
    state.mkdir()

    tools = tmp_path / "tools"
    tools.mkdir()
    for name in NEEDED:
        found = shutil.which(name)
        assert found, f"brak {name} na hoście testu"
        (tools / name).symlink_to(found)

    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "fake_docker.py").write_text(FAKE_DOCKER, encoding="utf-8")
    docker = fake / "docker"
    docker.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{fake / "fake_docker.py"}" "$@"\n', encoding="utf-8"
    )
    docker.chmod(0o755)
    return {
        "home": home.resolve(),
        "user": user,
        "tmp": scratch,
        "state": state,
        "tools": tools,
        "fake": fake,
        "log": tmp_path / "docker.log",
    }


def _run(
    kit: dict,
    *args: str,
    env: dict[str, str] | None = None,
    stdin: str = "",
    with_docker: bool = True,
    shell: str | None = None,
    launcher: Path = LAUNCHER,
    cwd: Path | None = None,
    timeout: float = 60,
) -> subprocess.CompletedProcess[str]:
    path = str(kit["tools"])
    if with_docker:
        path = f"{kit['fake']}{os.pathsep}{path}"
    # A clean environment: nothing of the developer's own WENEXT_* or DOCKER_* may decide a test,
    # and the usual Docker Desktop locations are switched off so a real docker cannot be found.
    full = {
        "PATH": path,
        "HOME": str(kit["user"]),
        "TMPDIR": str(kit["tmp"]),
        "FAKE_LOG": str(kit["log"]),
        "FAKE_STATE": str(kit["state"]),
        "WENEXT_EXTRA_PATH": "",
    }
    full.update(env or {})
    command = [str(launcher), *args] if shell is None else [shell, str(launcher), *args]
    return subprocess.run(
        command, input=stdin, env=full, capture_output=True, text=True, timeout=timeout, cwd=cwd
    )


def _calls(kit: dict) -> list[dict]:
    if not kit["log"].exists():
        return []
    return [json.loads(line) for line in kit["log"].read_text("utf-8").splitlines()]


def _kinds(kit: dict) -> list[str]:
    return [call["kind"] for call in _calls(kit)]


def _exec(kit: dict) -> dict:
    found = [call for call in _calls(kit) if call["kind"] == "compose exec"]
    assert len(found) == 1, found
    return found[0]


def _refused(kit: dict, done: subprocess.CompletedProcess[str], code: int, sentence: str) -> None:
    """The four properties of every refusal: empty stdout, the sentence, no long dashes, no litter."""
    assert done.returncode == code, (done.returncode, done.stdout, done.stderr)
    assert done.stdout == "", f"stdout to kanał MCP: {done.stdout!r}"
    assert sentence in done.stderr, done.stderr
    assert not LONG_DASHES.search(done.stderr), done.stderr
    assert list(kit["tmp"].iterdir()) == [], "launcher zostawił pliki tymczasowe"


def _env(kit: dict, **extra: str) -> dict[str, str]:
    return {"WENEXT_HOME": str(kit["home"]), **extra}


# ---------------------------------------------------------------------------
# The file itself
# ---------------------------------------------------------------------------


def test_the_launcher_is_executable_posix_sh_without_long_dashes() -> None:
    mode = stat.S_IMODE(LAUNCHER.stat().st_mode)
    assert mode == 0o755, oct(mode)
    text = LAUNCHER.read_text("utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert not LONG_DASHES.search(text)
    assert "#!/bin/bash" not in text and "[[ " not in text, "bashizm w skrypcie /bin/sh"
    assert subprocess.run(["sh", "-n", str(LAUNCHER)]).returncode == 0
    if shutil.which("dash"):
        assert subprocess.run(["dash", "-n", str(LAUNCHER)]).returncode == 0


def test_no_arguments_and_unknown_commands_print_usage_to_stderr(kit) -> None:
    for args in ((), ("nie-ma-takiego",)):
        done = _run(kit, *args)
        assert done.returncode == 64, done
        assert done.stdout == "" and "Użycie:" in done.stderr
        assert not LONG_DASHES.search(done.stderr)


# ---------------------------------------------------------------------------
# The preflight, step by step. Each refusal says what to do.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shell", DASHES)
def test_a_missing_docker_is_refused_with_the_install_sentence(kit, shell) -> None:
    done = _run(kit, "mcp", env=_env(kit), with_docker=False, shell=shell)
    _refused(kit, done, 69, "Nie znaleziono polecenia docker. Zainstaluj Docker Desktop.")


@pytest.mark.parametrize("shell", DASHES)
def test_a_daemon_that_is_down_is_refused(kit, shell) -> None:
    done = _run(kit, "mcp", env=_env(kit, FAKE_INFO_STATUS="1"), shell=shell)
    _refused(
        kit, done, 69, "Docker nie odpowiada. Uruchom Docker Desktop i uruchom agenta ponownie."
    )
    assert _kinds(kit) == ["info"], "po odmowie nie wolno już pytać o nic więcej"


def test_a_daemon_that_hangs_is_cut_off_instead_of_hanging_the_host(kit) -> None:
    """A stopped Docker Desktop hangs `docker info` rather than failing it; macOS has no
    `timeout`, so the launcher brings its own."""
    started = time.monotonic()
    done = _run(kit, "mcp", env=_env(kit, FAKE_INFO_SLEEP="30", WENEXT_PREFLIGHT_INFO_SECONDS="1"))
    assert time.monotonic() - started < 15, "launcher czekał na wiszący docker info"
    _refused(
        kit, done, 69, "Docker nie odpowiada. Uruchom Docker Desktop i uruchom agenta ponownie."
    )


def test_a_slow_but_answering_daemon_is_not_cut_off(kit) -> None:
    done = _run(
        kit,
        "mcp",
        env=_env(kit, FAKE_INFO_SLEEP="1", WENEXT_PREFLIGHT_INFO_SECONDS="10"),
        stdin="x\n",
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == "x\n"


@pytest.mark.parametrize("shell", DASHES)
def test_a_stopped_stack_is_refused_with_the_command_to_start_it(kit, shell) -> None:
    done = _run(kit, "mcp", env=_env(kit, FAKE_PS_ID=""), shell=shell)
    _refused(
        kit,
        done,
        69,
        "Wenext nie działa: kontener api jest zatrzymany. "
        f"W katalogu {kit['home']} uruchom make up-observe, poczekaj na stan healthy "
        "i uruchom agenta ponownie.",
    )
    assert "compose exec" not in _kinds(kit)


def test_a_failing_ps_is_not_taken_for_a_stopped_stack(kit) -> None:
    """`ps` that errors prints nothing too, and "api jest zatrzymany" would send the person to
    start a stack that may be running."""
    done = _run(kit, "mcp", env=_env(kit, FAKE_PS_STATUS="1"))
    _refused(kit, done, 69, "Nie udało się odczytać stanu Wenext przez docker compose (kod 1)")
    assert "zatrzymany" not in done.stderr


def test_a_container_that_never_becomes_healthy_is_refused_after_the_wait(kit) -> None:
    started = time.monotonic()
    done = _run(
        kit, "mcp", env=_env(kit, FAKE_HEALTH="starting", WENEXT_PREFLIGHT_HEALTH_SECONDS="2")
    )
    assert 1.5 < time.monotonic() - started < 24  # below the 25 s default even on a loaded host
    _refused(kit, done, 69, "kontener api nie jest zdrowy (stan: starting)")
    assert "compose exec" not in _kinds(kit)


def test_a_container_that_becomes_healthy_during_the_wait_is_used(kit) -> None:
    done = _run(
        kit,
        "mcp",
        env=_env(
            kit, FAKE_HEALTH="starting,starting,healthy", WENEXT_PREFLIGHT_HEALTH_SECONDS="20"
        ),
        stdin="x\n",
    )
    assert done.returncode == 0, done.stderr
    assert _kinds(kit).count("inspect") == 3


def test_the_health_deadline_is_the_clock_not_a_count_of_rounds(kit) -> None:
    """One `inspect` may itself take longer than the whole wait. A loop that counted rounds would
    run (rounds x (call + pause)) seconds; one that reads the clock stops right after the first
    call that ended past the deadline."""
    started = time.monotonic()
    done = _run(
        kit,
        "mcp",
        env=_env(
            kit,
            FAKE_HEALTH="starting",
            FAKE_INSPECT_SLEEP="3",
            WENEXT_PREFLIGHT_HEALTH_SECONDS="2",
        ),
    )
    assert 2.5 < time.monotonic() - started < 5.5
    _refused(kit, done, 69, "kontener api nie jest zdrowy (stan: starting)")


def test_a_failing_inspect_is_waited_out_and_reported_as_unknown(kit) -> None:
    started = time.monotonic()
    done = _run(
        kit, "mcp", env=_env(kit, FAKE_INSPECT_STATUS="1", WENEXT_PREFLIGHT_HEALTH_SECONDS="2")
    )
    assert 1.5 < time.monotonic() - started < 15
    _refused(kit, done, 69, "kontener api nie jest zdrowy (stan: nieznany)")


def test_an_unhealthy_container_is_refused_at_once(kit) -> None:
    started = time.monotonic()
    done = _run(kit, "mcp", env=_env(kit, FAKE_HEALTH="unhealthy"))
    assert time.monotonic() - started < 10, "czekał na kontener, który już jest niezdrowy"
    _refused(kit, done, 69, "kontener api nie jest zdrowy (stan: unhealthy)")


def test_a_container_without_a_healthcheck_is_accepted(kit) -> None:
    done = _run(kit, "mcp", env=_env(kit, FAKE_HEALTH="none"), stdin="x\n")
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("shell", DASHES)
def test_a_missing_checkout_is_refused_with_both_ways_out(kit, shell) -> None:
    sentence = (
        "Nie widzę repozytorium Wenext. Ustaw WENEXT_HOME na katalog z docker-compose.yml "
        "albo uruchom: bin/wenext setup"
    )
    _refused(kit, _run(kit, "mcp", shell=shell), 78, sentence)
    # A stale variable: it points at a directory that is not a checkout.
    stale = _run(kit, "mcp", env={"WENEXT_HOME": str(kit["user"])}, shell=shell)
    _refused(kit, stale, 78, sentence)
    # A stale config: the same, from the file.
    (kit["user"] / ".wenext").mkdir()
    (kit["user"] / ".wenext" / "config.json").write_text(
        json.dumps({"home": str(kit["user"] / "gone")}), encoding="utf-8"
    )
    _refused(kit, _run(kit, "mcp", shell=shell), 78, sentence)
    assert "compose exec" not in _kinds(kit)


@pytest.mark.parametrize("shell", DASHES)
def test_a_missing_env_file_is_refused_with_the_command_that_makes_it(kit, shell) -> None:
    """`compose exec` interpolates the whole compose file and refuses without POSTGRES_PASSWORD;
    the person would see a compose error instead of the sentence."""
    (kit["home"] / ".env").unlink()
    done = _run(kit, "mcp", env=_env(kit), shell=shell)
    _refused(kit, done, 78, f"Brakuje pliku .env w {kit['home']}. Uruchom tam: make env")
    assert _kinds(kit) == [], "przed sprawdzeniem .env nie wolno wołać dockera"


def test_docker_is_looked_for_in_the_usual_places_after_the_path(kit) -> None:
    """A host started from the desktop may have no /usr/local/bin on PATH."""
    done = _run(
        kit,
        "mcp",
        env=_env(kit, WENEXT_EXTRA_PATH=f"/nie/ma/takiego:{kit['fake']}"),
        with_docker=False,
        stdin="x\n",
    )
    assert done.returncode == 0, done.stderr
    assert _calls(kit)[0]["path"].endswith(f"{kit['fake']}")


# ---------------------------------------------------------------------------
# The server call: arguments, stdin, exit code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shell", DASHES)
def test_the_server_is_started_with_the_exact_arguments(kit, shell) -> None:
    done = _run(
        kit,
        "mcp",
        env=_env(kit, WENEXT_PROJECT="Moja Alfa", WENEXT_PLUGIN_TTL_HOURS="4"),
        stdin='{"jsonrpc":"2.0"}\n',
        shell=shell,
    )
    assert done.returncode == 0, done.stderr
    home = kit["home"]
    assert _exec(kit)["argv"] == [
        "compose", "-f", f"{home}/docker-compose.yml", "-p", "wenext", "exec", "-T",
        "-e", "WENEXT_PROJECT=Moja Alfa",
        "-e", "WENEXT_ORGANIZATION=",
        "-e", "WENEXT_HOST=codex",
        "-e", "WENEXT_PLUGIN_TTL_HOURS=4",
        "-e", "WENEXT_DASHBOARD_URL=http://127.0.0.1:3000",
        "-e", "WENEXT_LANG=",
        "api", "python", "-m", "control_plane.tools.plugin_server", "serve",
    ]  # fmt: skip
    assert _kinds(kit) == ["info", "compose ps", "inspect", "compose exec"]


def test_the_preflight_asks_docker_the_documented_questions(kit) -> None:
    done = _run(kit, "mcp", env=_env(kit), stdin="x\n")
    assert done.returncode == 0, done.stderr
    calls = _calls(kit)
    home = kit["home"]
    assert calls[0]["argv"] == ["info"]
    assert calls[1]["argv"] == [
        "compose", "-f", f"{home}/docker-compose.yml", "-p", "wenext",
        "ps", "--status", "running", "-q", "api",
    ]  # fmt: skip
    assert calls[2]["argv"][:2] == ["inspect", "--format"]
    assert "State.Health.Status" in calls[2]["argv"][2]
    assert calls[2]["argv"][3] == "abc123def456", "inspect pyta o kontener, który zwrócił ps"


@pytest.mark.parametrize("shell", DASHES)
def test_the_mcp_stream_reaches_the_server_untouched(kit, shell) -> None:
    """THE test of the stdin rule: every fake call before `serve` reads a line from its stdin, as
    the real `exec -T` would. If the preflight let it, the first frame below would never reach
    `serve`."""
    frames = '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n{"jsonrpc":"2.0","method":"ping"}\n'
    done = _run(kit, "mcp", env=_env(kit), stdin=frames, shell=shell)
    assert done.returncode == 0, done.stderr
    assert done.stdout == frames, "preflight zjadł początek strumienia MCP"
    assert not LONG_DASHES.search(done.stderr)


@pytest.mark.parametrize("code", [2, 3, 4, 5])
def test_the_servers_exit_code_and_message_pass_through(kit, code) -> None:
    """Exit 4 is "the project cannot be chosen": the server prints the Polish list, the launcher
    adds nothing and changes nothing."""
    listing = (
        f"wenext-plugin: Jest kilka projektów. Ustaw WENEXT_PROJECT. Dostępne: Alfa ({ONE}), "
        f"Beta ({TWO})"
    )
    done = _run(kit, "mcp", env=_env(kit, FAKE_SERVE_STATUS=str(code), FAKE_SERVE_STDERR=listing))
    assert done.returncode == code, done
    assert done.stdout == ""
    assert listing in done.stderr
    assert _exec(kit)["argv"][-1] == "serve"


@pytest.mark.parametrize(
    ("env", "flag", "expected"),
    [
        ({}, None, "codex"),
        ({"CLAUDE_PLUGIN_ROOT": "/cache/wenext"}, None, "claude-code"),
        ({"WENEXT_HOST": "claude-code"}, None, "claude-code"),
        ({"WENEXT_HOST": "codex", "CLAUDE_PLUGIN_ROOT": "/cache/wenext"}, None, "codex"),
        ({"WENEXT_HOST": "codex"}, "claude-code", "claude-code"),
        ({"CLAUDE_PLUGIN_ROOT": "/cache/wenext"}, "codex", "codex"),
    ],
)
def test_the_host_comes_from_the_flag_then_the_environment_then_the_plugin_root(
    kit, env, flag, expected
) -> None:
    args = ["mcp", *(["--host", flag] if flag else [])]
    done = _run(kit, *args, env=_env(kit, **env), stdin="x\n")
    assert done.returncode == 0, done.stderr
    assert f"WENEXT_HOST={expected}" in _exec(kit)["argv"]


def test_an_unknown_host_is_refused_before_docker_is_asked(kit) -> None:
    _refused(
        kit,
        _run(kit, "mcp", env=_env(kit, WENEXT_HOST="vim")),
        78,
        "Nieznany agent 'vim'. Dozwolone: codex, claude-code.",
    )
    assert _kinds(kit) == []
    done = _run(kit, "mcp", "--host")
    assert done.returncode == 64 and done.stdout == ""


def test_the_project_comes_from_the_environment_then_the_config_then_nothing(kit) -> None:
    def project(**env: str) -> str:
        kit["log"].unlink(missing_ok=True)
        kit["state"].joinpath("inspect_calls").unlink(missing_ok=True)
        done = _run(kit, "mcp", env=_env(kit, **env), stdin="x\n")
        assert done.returncode == 0, done.stderr
        (value,) = [a for a in _exec(kit)["argv"] if a.startswith("WENEXT_PROJECT=")]
        return value

    assert project() == "WENEXT_PROJECT=", "bez projektu wybiera serwer, nie launcher"
    (kit["user"] / ".wenext").mkdir()
    config = kit["user"] / ".wenext" / "config.json"
    config.write_text(json.dumps({"home": str(kit["home"]), "project": "Z pliku"}), "utf-8")
    assert project(WENEXT_HOME="") == "WENEXT_PROJECT=Z pliku"
    assert project(WENEXT_PROJECT=ONE) == f"WENEXT_PROJECT={ONE}", "zmienna wygrywa z plikiem"


def test_the_organization_comes_from_the_environment_then_the_config_then_nothing(kit) -> None:
    # A database with several organizations (the founder's has two) makes the server refuse
    # without one; the launcher must carry it the same way it carries the project.
    def organization(**env: str) -> str:
        kit["log"].unlink(missing_ok=True)
        kit["state"].joinpath("inspect_calls").unlink(missing_ok=True)
        done = _run(kit, "mcp", env=_env(kit, **env), stdin="x\n")
        assert done.returncode == 0, done.stderr
        (value,) = [a for a in _exec(kit)["argv"] if a.startswith("WENEXT_ORGANIZATION=")]
        return value

    assert organization() == "WENEXT_ORGANIZATION="
    (kit["user"] / ".wenext").mkdir()
    config = kit["user"] / ".wenext" / "config.json"
    config.write_text(json.dumps({"home": str(kit["home"]), "organization": "org-z-pliku"}), "utf-8")
    assert organization(WENEXT_HOME="") == "WENEXT_ORGANIZATION=org-z-pliku"
    assert organization(WENEXT_ORGANIZATION="org-env") == "WENEXT_ORGANIZATION=org-env"


def test_the_demo_project_stays_english_and_on_its_own_project_next_to_a_polish_global_config(
    kit,
) -> None:
    """The recording sandbox (`scripts/demo/codex_project.py`): Codex hands the launcher the `env` of
    the project's own server definition. Whatever the founder's `~/.wenext/config.json` says (Polish
    card, his real project and organization), that env wins, and without it his config is what runs."""
    import importlib.util
    import tomllib

    path = ROOT / "scripts" / "demo" / "codex_project.py"
    # No skip: the module-level skipif already covers the tests image, and wherever the launcher is,
    # the scripts are.
    assert path.is_file(), path
    spec = importlib.util.spec_from_file_location("codex_project_launcher_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    demo_env = tomllib.loads(module.render_config())["mcp_servers"]["wenext"]["env"]

    (kit["user"] / ".wenext").mkdir()
    (kit["user"] / ".wenext" / "config.json").write_text(
        json.dumps({"home": str(kit["home"]), "project": "Wenext", "organization": ONE, "lang": "pl"}),
        "utf-8",
    )

    def argv_with(env: dict[str, str]) -> list[str]:
        kit["log"].unlink(missing_ok=True)
        kit["state"].joinpath("inspect_calls").unlink(missing_ok=True)
        done = _run(kit, "mcp", env={**env, "WENEXT_HOME": str(kit["home"])}, stdin="x\n")
        assert done.returncode == 0, done.stderr
        return _exec(kit)["argv"]

    plain = argv_with({})
    assert "WENEXT_PROJECT=Wenext" in plain and f"WENEXT_ORGANIZATION={ONE}" in plain
    assert "WENEXT_LANG=pl" in plain, "the founder's default is untouched"

    sandbox = argv_with({key: value for key, value in demo_env.items() if key != "WENEXT_HOME"})
    assert "WENEXT_PROJECT=tallymark" in sandbox
    assert "WENEXT_ORGANIZATION=dddddddd-0000-4000-8000-000000000001" in sandbox
    assert "WENEXT_LANG=en" in sandbox
    assert "WENEXT_HOST=codex" in sandbox


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("pl", "pl"),
        ("PL", "pl"),
        ("pl_PL", "pl"),
        ("pl-PL", "pl"),
        ("en", "en"),
        ("EN-gb", "en"),
        ("en_US", "en"),
        ("auto", ""),
        ("", ""),
    ],
)
def test_the_card_language_is_passed_normalized_and_empty_means_the_host_decides(
    kit, value, expected
) -> None:
    done = _run(kit, "mcp", env=_env(kit, WENEXT_LANG=value), stdin="x\n")
    assert done.returncode == 0, done.stderr
    assert f"WENEXT_LANG={expected}" in _exec(kit)["argv"]


def test_the_card_language_comes_from_the_environment_then_the_config_then_nothing(kit) -> None:
    def language(**env: str) -> str:
        kit["log"].unlink(missing_ok=True)
        kit["state"].joinpath("inspect_calls").unlink(missing_ok=True)
        done = _run(kit, "mcp", env=_env(kit, **env), stdin="x\n")
        assert done.returncode == 0, done.stderr
        (value,) = [a for a in _exec(kit)["argv"] if a.startswith("WENEXT_LANG=")]
        return value

    assert language() == "WENEXT_LANG="
    (kit["user"] / ".wenext").mkdir()
    config = kit["user"] / ".wenext" / "config.json"
    config.write_text(json.dumps({"home": str(kit["home"]), "lang": "pl"}), "utf-8")
    assert language(WENEXT_HOME="") == "WENEXT_LANG=pl"
    assert language(WENEXT_LANG="en") == "WENEXT_LANG=en", "zmienna wygrywa z plikiem"


def test_an_unknown_card_language_is_said_on_stderr_and_does_not_stop_the_agent(kit) -> None:
    done = _run(kit, "mcp", env=_env(kit, WENEXT_LANG="polski"), stdin="x\n")
    assert done.returncode == 0, done.stderr
    assert "Nieznany język karty 'polski'" in done.stderr
    assert "WENEXT_LANG=" in _exec(kit)["argv"], "pusty: karta wybierze język hosta"
    assert not LONG_DASHES.search(done.stderr)


def test_the_shell_locale_is_never_taken_for_the_card_language(kit) -> None:
    # `LANG` is the shell's own variable (pl_PL.UTF-8 on a Polish machine) and says nothing about
    # which language the person wants the card in: only WENEXT_LANG does.
    done = _run(kit, "mcp", env=_env(kit, LANG="pl_PL.UTF-8", LC_ALL="pl_PL.UTF-8"), stdin="x\n")
    assert done.returncode == 0, done.stderr
    assert "WENEXT_LANG=" in _exec(kit)["argv"]


def test_codex_hands_the_launcher_the_card_language() -> None:
    # Codex starts an MCP server with only the variables its config lists: without this one the
    # language a person set in their environment would never reach the launcher.
    config = json.loads((ROOT / "plugins" / "wenext" / ".codex-mcp.json").read_text("utf-8"))
    assert "WENEXT_LANG" in config["mcpServers"]["wenext"]["env_vars"]


def test_setup_remembers_the_card_language_and_keeps_it_when_run_again(kit) -> None:
    config = kit["user"] / ".wenext" / "config.json"
    done = _run(kit, "setup", str(kit["home"]), "--lang", "pl_PL", env=_env(kit, WENEXT_HOME=""))
    assert done.returncode == 0, done.stderr
    assert json.loads(config.read_text("utf-8"))["lang"] == "pl"
    assert "lang: pl" in done.stdout
    # Run again for another reason: the language is not forgotten (a Polish install stays Polish).
    done = _run(kit, "setup", str(kit["home"]), "--project", "Alfa", env=_env(kit, WENEXT_HOME=""))
    assert done.returncode == 0, done.stderr
    assert json.loads(config.read_text("utf-8"))["lang"] == "pl"
    done = _run(kit, "setup", str(kit["home"]), "--lang=en", env=_env(kit, WENEXT_HOME=""))
    assert json.loads(config.read_text("utf-8"))["lang"] == "en"
    done = _run(kit, "setup", str(kit["home"]), "--lang", "auto", env=_env(kit, WENEXT_HOME=""))
    assert done.returncode == 0, done.stderr
    assert "lang" not in json.loads(config.read_text("utf-8")), "auto: karta pyta hosta"


def test_setup_refuses_an_unknown_language_and_writes_nothing(kit) -> None:
    done = _run(kit, "setup", str(kit["home"]), "--lang", "klingon", env=_env(kit, WENEXT_HOME=""))
    assert done.returncode == 64, done
    assert "Dozwolone: pl, en." in done.stderr
    assert not (kit["user"] / ".wenext" / "config.json").exists()
    missing = _run(kit, "setup", str(kit["home"]), "--lang", env=_env(kit, WENEXT_HOME=""))
    assert missing.returncode == 64 and "Brakuje wartości po --lang." in missing.stderr


def test_doctor_says_which_card_language_is_in_use(kit) -> None:
    done = _run(kit, "doctor", env=_env(kit, WENEXT_LANG="pl"))
    assert done.returncode == 0, done.stderr
    assert "Język karty: pl" in done.stdout.splitlines()
    done = _run(kit, "doctor", env=_env(kit))
    assert "Język karty: (nie wybrano; karta użyje języka hosta)" in done.stdout.splitlines()


def test_setup_remembers_the_organization(kit) -> None:
    done = _run(kit, "setup", str(kit["home"]), "--organization", "org-1", env=_env(kit, WENEXT_HOME=""))
    assert done.returncode == 0, done.stderr
    saved = json.loads((kit["user"] / ".wenext" / "config.json").read_text("utf-8"))
    assert saved["organization"] == "org-1"


def test_the_lifetime_is_passed_when_set_and_empty_when_not(kit) -> None:
    done = _run(kit, "mcp", env=_env(kit), stdin="x\n")
    assert done.returncode == 0, done.stderr
    assert "WENEXT_PLUGIN_TTL_HOURS=" in _exec(kit)["argv"], "pusty znaczy: domyślny z serwera"


@pytest.mark.parametrize(
    ("env_file", "env", "expected"),
    [
        (None, {}, "http://127.0.0.1:3000"),
        ("WENEXT_DASHBOARD_HOST_PORT=3456\n", {}, "http://127.0.0.1:3456"),
        ('WENEXT_DASHBOARD_HOST_PORT="3457"  # comment\n', {}, "http://127.0.0.1:3457"),
        ("WENEXT_DASHBOARD_HOST_PORT=\n", {}, "http://127.0.0.1:3000"),
        ("WENEXT_DASHBOARD_HOST_PORT=abc\n", {}, "http://127.0.0.1:3000"),
        (
            "WENEXT_DASHBOARD_HOST_PORT=3456\n",
            {"WENEXT_DASHBOARD_HOST_PORT": "4000"},
            "http://127.0.0.1:4000",
        ),
    ],
)
def test_the_dashboard_address_follows_the_port_compose_publishes(
    kit, env_file, env, expected
) -> None:
    if env_file is not None:
        (kit["home"] / ".env").write_text("POSTGRES_PASSWORD=x\n" + env_file, "utf-8")
    done = _run(kit, "mcp", env=_env(kit, **env), stdin="x\n")
    assert done.returncode == 0, done.stderr
    assert f"WENEXT_DASHBOARD_URL={expected}" in _exec(kit)["argv"]


def test_the_compose_file_publishes_the_dashboard_on_the_variable_the_launcher_reads() -> None:
    """The launcher cannot know the port except by reading what compose reads."""
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    assert '"127.0.0.1:${WENEXT_DASHBOARD_HOST_PORT:-3000}:3000"' in compose


def test_docker_gets_the_checkouts_own_config_directory_when_make_env_made_it(kit) -> None:
    """D-008: the credential helper of Docker Desktop hangs on this host; the Makefile exports
    this directory and so must the launcher, on EVERY call, the preflight included."""
    done = _run(kit, "mcp", env=_env(kit, DOCKER_CONFIG="/home/obce"), stdin="x\n")
    assert done.returncode == 0, done.stderr
    expected = f"{kit['home']}/infra/docker-config"
    assert [call["docker_config"] for call in _calls(kit)] == [expected] * 4


def test_a_config_directory_without_config_json_is_not_used(kit) -> None:
    """DOCKER_CONFIG also moves the place Docker looks for its CLI plugins; a directory `make env`
    has not filled would hide `docker compose` itself (tools/dev/make_docker_config.py)."""
    (kit["home"] / "infra" / "docker-config" / "config.json").unlink()
    done = _run(kit, "mcp", env=_env(kit), stdin="x\n")
    assert done.returncode == 0, done.stderr
    assert {call["docker_config"] for call in _calls(kit)} == {None}


def test_a_signal_during_the_preflight_leaves_nothing_behind(kit) -> None:
    command = [str(LAUNCHER), "mcp"]
    env = {
        "PATH": f"{kit['fake']}{os.pathsep}{kit['tools']}",
        "HOME": str(kit["user"]),
        "TMPDIR": str(kit["tmp"]),
        "FAKE_LOG": str(kit["log"]),
        "FAKE_STATE": str(kit["state"]),
        "WENEXT_EXTRA_PATH": "",
        "WENEXT_HOME": str(kit["home"]),
        "FAKE_INFO_SLEEP": "30",
    }
    process = subprocess.Popen(
        command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        deadline = time.monotonic() + 10
        while not any(kit["tmp"].iterdir()) and time.monotonic() < deadline:
            time.sleep(0.05)
        process.terminate()
        process.wait(timeout=10)
    finally:
        process.kill()
        process.communicate()
    assert process.returncode == 143
    assert list(kit["tmp"].iterdir()) == []


# ---------------------------------------------------------------------------
# projects and doctor
# ---------------------------------------------------------------------------


def test_projects_prints_what_the_server_lists_and_never_reads_the_callers_stdin(kit) -> None:
    listing = f"{ONE}\tMoja przestrzeń / Alfa\n{TWO}\tMoja przestrzeń / Beta\n"
    done = _run(kit, "projects", env=_env(kit, FAKE_PROJECTS=listing), stdin="x\n")
    assert done.returncode == 0, done.stderr
    assert done.stdout == listing
    home = kit["home"]
    assert _exec(kit)["argv"] == [
        "compose", "-f", f"{home}/docker-compose.yml", "-p", "wenext", "exec", "-T",
        "-e", "WENEXT_ORGANIZATION=",
        "api", "python", "-m", "control_plane.tools.plugin_server", "projects",
    ]  # fmt: skip


def test_projects_refuses_like_mcp_when_docker_is_down(kit) -> None:
    done = _run(kit, "projects", env=_env(kit, FAKE_INFO_STATUS="1"))
    _refused(kit, done, 69, "Docker nie odpowiada.")


def test_projects_passes_the_servers_exit_code_through(kit) -> None:
    done = _run(kit, "projects", env=_env(kit, FAKE_PROJECTS_STATUS="3"))
    assert done.returncode == 3


def test_doctor_reports_each_step_and_exits_zero_when_all_is_well(kit) -> None:
    done = _run(kit, "doctor", env=_env(kit, WENEXT_PROJECT="Alfa"))
    assert done.returncode == 0, done.stderr
    for line in (
        f"OK  Repozytorium Wenext: {kit['home']}",
        "OK  Plik .env jest",
        "OK  Docker odpowiada",
        "OK  Kontener api działa i jest zdrowy",
        "Agent: codex",
        "Projekt: Alfa",
        "Dashboard: http://127.0.0.1:3000",
    ):
        assert line in done.stdout.splitlines(), done.stdout
    assert not LONG_DASHES.search(done.stdout + done.stderr)
    assert "compose exec" not in _kinds(kit), "doctor nie startuje serwera"
    assert list(kit["tmp"].iterdir()) == []


def test_doctor_fails_with_the_same_sentence_and_claims_nothing_it_did_not_check(kit) -> None:
    done = _run(kit, "doctor", env=_env(kit, FAKE_PS_ID=""))
    assert done.returncode == 69, done
    assert "uruchom make up-observe" in done.stderr
    assert "OK  Docker odpowiada" in done.stdout
    assert "Kontener api" not in done.stdout and "Agent:" not in done.stdout


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


def _config(kit: dict) -> dict:
    return json.loads((kit["user"] / ".wenext" / "config.json").read_text("utf-8"))


def test_setup_without_arguments_remembers_the_checkout_the_script_sits_in(kit) -> None:
    done = _run(kit, "setup")
    assert done.returncode == 0, done.stderr
    assert _config(kit) == {"home": str(ROOT.resolve())}
    assert str(ROOT.resolve()) in done.stdout


def test_setup_from_a_cache_copy_takes_the_current_directory(kit, tmp_path) -> None:
    cache = tmp_path / "cache" / "bin"
    cache.mkdir(parents=True)
    copy = cache / "wenext"
    shutil.copy(LAUNCHER, copy)
    copy.chmod(0o755)
    done = _run(kit, "setup", launcher=copy, cwd=kit["home"])
    assert done.returncode == 0, done.stderr
    assert _config(kit) == {"home": str(kit["home"])}
    elsewhere = _run(kit, "setup", launcher=copy, cwd=tmp_path)
    assert elsewhere.returncode == 78
    assert f"W katalogu {tmp_path.resolve()} nie ma pliku docker-compose.yml" in elsewhere.stderr
    assert "bin/wenext setup /ścieżka/do/wenext" in elsewhere.stderr


def test_setup_takes_an_explicit_path_and_a_project(kit) -> None:
    done = _run(kit, "setup", str(kit["home"]), "--project", "Moja Alfa")
    assert done.returncode == 0, done.stderr
    assert _config(kit) == {"home": str(kit["home"]), "project": "Moja Alfa"}
    # The project can come from the environment the person types it in.
    again = _run(kit, "setup", str(kit["home"]), env={"WENEXT_PROJECT": "Beta"})
    assert again.returncode == 0, again.stderr
    assert _config(kit) == {"home": str(kit["home"]), "project": "Beta"}
    # And a rerun without a project forgets it: a project belongs to one checkout's database.
    plain = _run(kit, "setup", str(kit["home"]))
    assert plain.returncode == 0, plain.stderr
    assert _config(kit) == {"home": str(kit["home"])}


def test_setup_writes_valid_json_with_an_absolute_path_whatever_the_path_looks_like(
    kit, tmp_path
) -> None:
    """A quote, a backslash, a space and Polish letters in a directory name must come back from
    the file as the same string, through both the writer and the launcher's own reader."""
    awkward = tmp_path / 'ąę "quoted" \\ back slash'
    awkward.mkdir()
    (awkward / "docker-compose.yml").write_text("name: wenext\n", encoding="utf-8")
    (awkward / ".env").write_text("POSTGRES_PASSWORD=x\n", encoding="utf-8")
    project = 'Alfa "pro" \\ ąę'
    done = _run(kit, "setup", str(awkward), "--project", project)
    assert done.returncode == 0, done.stderr
    written = _config(kit)
    assert written == {"home": str(awkward.resolve()), "project": project}
    assert os.path.isabs(written["home"])
    # Round trip through the reader: the exec of `mcp` has the same strings.
    started = _run(kit, "mcp", stdin="x\n")
    assert started.returncode == 0, started.stderr
    argv = _exec(kit)["argv"]
    assert argv[2] == f"{awkward.resolve()}/docker-compose.yml"
    assert f"WENEXT_PROJECT={project}" in argv


def test_setup_expands_a_relative_path_to_an_absolute_one(kit) -> None:
    done = _run(kit, "setup", "checkout", cwd=kit["home"].parent)
    assert done.returncode == 0, done.stderr
    assert _config(kit) == {"home": str(kit["home"])}


def test_setup_refuses_a_directory_that_is_not_a_checkout_and_writes_nothing(kit) -> None:
    done = _run(kit, "setup", str(kit["user"]))
    assert done.returncode == 78
    assert done.stdout == ""
    assert f"W katalogu {kit['user']} nie ma pliku docker-compose.yml" in done.stderr
    assert not (kit["user"] / ".wenext").exists()
    assert not LONG_DASHES.search(done.stderr)


def test_setup_refuses_values_that_json_would_have_to_escape_with_control_characters(kit) -> None:
    done = _run(kit, "setup", str(kit["home"]), "--project", "dwie\nlinie")
    assert done.returncode == 78
    assert "znak sterujący" in done.stderr
    assert not (kit["user"] / ".wenext").exists()


def test_setup_rejects_unknown_arguments_and_two_paths(kit) -> None:
    assert _run(kit, "setup", "--nie-ma").returncode == 64
    assert _run(kit, "setup", str(kit["home"]), str(kit["user"])).returncode == 64
    assert not (kit["user"] / ".wenext").exists()


def test_the_config_file_alone_is_enough_to_start(kit) -> None:
    """The plugin's own configuration passes no WENEXT_HOME: `setup` is the whole setup."""
    assert _run(kit, "setup", str(kit["home"])).returncode == 0
    done = _run(kit, "mcp", stdin="x\n")
    assert done.returncode == 0, done.stderr
    assert _exec(kit)["argv"][2] == f"{kit['home']}/docker-compose.yml"


def test_a_hand_edited_config_is_read_whatever_its_layout(kit) -> None:
    (kit["user"] / ".wenext").mkdir()
    config = kit["user"] / ".wenext" / "config.json"
    config.write_text(f'{{"project":"W jednej linii","home":"{kit["home"]}"}}\n', encoding="utf-8")
    done = _run(kit, "mcp", stdin="x\n")
    assert done.returncode == 0, done.stderr
    assert "WENEXT_PROJECT=W jednej linii" in _exec(kit)["argv"]
    assert _exec(kit)["argv"][2] == f"{kit['home']}/docker-compose.yml"
