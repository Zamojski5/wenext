"""Polecenia uruchamiane w kontenerze muszą być tym, co do kontenera trafia.

ZMIERZONE NA LIVE 19.09: `make pilot-to-project` kończyło się

    python3: can't open file '/opt/wenext/tools/dev/pilot_to_project.py'

bo skrypt biegł przez `compose exec api`, a obraz api zawiera PAKIETY, nie katalog `tools/`.
Nic w tym łańcuchu nie kłamało: skrypt działał lokalnie, obraz był poprawny, tylko nikt nie
sprawdził, że jedno widzi drugie. Ten test pilnuje granicy: co ma biec w kontenerze, musi być
modułem pakietu, a moduł musi mieć wejście, które da się uruchomić bez bazy.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONTROL_PLANE = ROOT / "services" / "control_plane"

#: Linia `docker compose ... exec`, razem z jej kontynuacjami po `\`.
_EXEC = re.compile(r"docker compose[^\n]*\bexec\b((?:[^\n]*\\\n)*[^\n]*)")


#: Where a script may start a command in the `api` container: the dev tools, and the plugin's
#: launcher (`plugins/wenext/bin/wenext`, a script with no extension that `exec`s
#: `python -m control_plane.tools.plugin_server`). The launcher is the one that runs on the
#: PERSON's machine, so it is the one that cannot be fixed by rebuilding anything: a module name
#: that does not exist in the image fails on the first start of the first user.
SCRIPT_DIRECTORIES = (
    (ROOT / "tools" / "dev", "*.sh"),
    (ROOT / "plugins" / "wenext" / "bin", "*"),
)


def _container_commands() -> list[tuple[Path, str]]:
    found = []
    scripts = [
        script
        for directory, pattern in SCRIPT_DIRECTORIES
        for script in sorted(directory.glob(pattern))
        if script.is_file()
    ]
    for script in scripts:
        text = script.read_text("utf-8")
        for match in _EXEC.finditer(text):
            found.append((script, match.group(1).replace("\\\n", " ")))
    return found


def _module_path(module: str) -> Path | None:
    top, *rest = module.split(".")
    for service in sorted((ROOT / "services").iterdir()):
        path = service / top / Path(*rest).with_suffix(".py")
        if path.exists():
            return path
    return None


def test_container_commands_do_not_reach_for_the_tools_directory() -> None:
    """Obraz nie zawiera `tools/`, więc ścieżka do pliku stamtąd to polecenie, które padnie."""
    for script, command in _container_commands():
        assert "/opt/wenext/tools" not in command, (
            f"{script.name} woła plik z tools/ wewnątrz kontenera: {command.strip()!r}; "
            "obraz nie zawiera tego katalogu, więc to polecenie nie wystartuje"
        )


def test_every_container_command_is_a_module_with_an_entry_point() -> None:
    commands = _container_commands()
    assert commands, "nie znalazłem ani jednego polecenia w kontenerze, a mają być trzy"
    launcher = [script for script, _ in commands if script.parent.name == "bin"]
    assert launcher, "launcher wtyczki nie ma w kontenerze żadnego polecenia, a woła serwer"
    for script, command in commands:
        match = re.search(r"python3?\s+-m\s+([\w.]+)", command)
        assert match, f"{script.name}: polecenie w kontenerze nie jest modułem: {command.strip()!r}"
        module = match.group(1)
        path = _module_path(module)
        assert path is not None, f"{script.name}: nie widzę modułu {module} w żadnej usłudze"
        source = path.read_text("utf-8")
        assert "def main(" in source, f"{module}: brak funkcji main"
        assert '__name__ == "__main__"' in source, f"{module}: brak wejścia __main__"


def test_bridge_help_works_without_a_database() -> None:
    """`--help` ma odpowiedzieć bez DSN: to jedyny sposób sprawdzenia wejścia bez stacku live."""
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PYTHONPATH"] = str(CONTROL_PLANE)
    done = subprocess.run(
        [sys.executable, "-m", "control_plane.presence.bridge", "--help"],
        cwd=CONTROL_PLANE,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "--organization" in done.stdout, done.stdout


def test_bridge_refuses_plainly_when_there_is_no_database() -> None:
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PYTHONPATH"] = str(CONTROL_PLANE)
    done = subprocess.run(
        [sys.executable, "-m", "control_plane.presence.bridge"],
        cwd=CONTROL_PLANE,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 3, (done.returncode, done.stdout, done.stderr)
    assert "NIE_URUCHOMIONO" in done.stdout, done.stdout
