"""Where one Codex connection keeps its `CODEX_HOME`, and how that directory is created.

ONE VOLUME, MANY DIRECTORIES. The deployment mounts a single `codex-home` volume
(`docker-compose.yml`) and every connection gets a subdirectory of it, except the one that IS the
volume root: the founder's existing login, on which the live pilot runs. Measured on the pinned
binary 2026-09-19: two `codex app-server` processes on two different `CODEX_HOME` directories run
at the same time, each reports its own `codexHome` from `initialize`, each gets its own
`installation_id`, and a login in one leaves the other's `auth.json` untouched byte for byte.

THE ROOT IS NEVER PREPARED, ONLY ADOPTED. `prepare_connection_home` refuses `home_ref == "."`.
That directory already holds the founder's `auth.json` and a `config.toml` written when the
account was logged in; rewriting it from here would change the hardening under a running pilot,
and "accepting" an existing connection must not modify anything.

OD E9 (21.09) TEN ZAKAZ OBOWIĄZUJE CAŁY KOD, a nie tylko tę funkcję. Do tego dnia mówił on
prawdę o zakładaniu katalogu i nieprawdę o pisaniu: tura sesji na połączeniu założyciela
przepisywała ten sam `config.toml` przy KAŻDEJ turze (`sessions/handlers.py`), razem z wpisem
serwera narzędzi niosącym ścieżkę do jej pliku zdolności. Teraz tura podaje całą swoją
konfigurację w `--config` przy starcie procesu i do domu nie pisze nic; jedyne, co jeszcze robi
z katalogiem, to zakłada go, gdyby zniknął.

`home_ref` IS VALIDATED TWICE, HERE AND IN THE DATABASE. The CHECK in migration 0014 allows only
`.` or a UUID, and `resolve_home` refuses anything else again before it touches the filesystem.
Two checks rather than one because they fail differently: the CHECK stops a bad row being
written, this one stops a bad row that already exists (a restored backup, a manual edit) from
being turned into a path.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

#: The environment variable naming the ROOT of the volume. The same name `observe/wiring.py`
#: already reads, deliberately: the founder's connection is that root, and a second variable would
#: make "which directory is the pilot using" a question with two answers.
HOME_ROOT_VARIABLE = "WENEXT_CODEX_HOME"

#: `home_ref` of the connection that is the volume root itself.
ROOT_REF = "."

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

#: Why the volume ROOT is never opened with an app-server of ours, in the words the screen prints.
#:
#: L2159 forbids two App Servers on one `CODEX_HOME`. The root is `WENEXT_CODEX_HOME`, which is
#: also the directory `observe/wiring.py::build_turns` hands to the turn runner, and that runner
#: starts its own app-server FOR EACH TURN through the SDK. Neither side takes
#: `CodexConnectionSupervisor`'s lease — nothing in the control plane uses it, and there is no
#: `wenext-runtime.lease` on the live volume (checked 2026-09-19) — so an overlap would not be
#: refused, it would simply happen. The concrete damage is a concurrent OAuth refresh rewriting
#: `auth.json` and the live pilot losing its login in the middle of a signed policy window.
#:
#: WHEN THIS STOPS BEING TRUE: the day the pilot no longer runs on the volume root, or the day
#: both sides take one lease. Until then the screen reports what can be read from disk and says
#: outright that the rest was not measured.
ROOT_READ_DISABLED_REASON = (
    "stanu, planu i limitów nie odczytano: na tym katalogu pracuje pilot, a dwa app-servery na "
    "jednym CODEX_HOME są zakazane (L2159). Pokazujemy tylko to, co widać w plikach"
)


class UnsafeHomeRef(ValueError):
    """A `home_ref` that is neither the root nor a UUID. Refused before it becomes a path."""


class CodexHomeUnavailable(RuntimeError):
    """There is no configured volume root, so no connection has a directory to live in."""


def home_root(env: Mapping[str, str] | None = None) -> Path | None:
    """The configured root, or None. None is a FACT the screen states, not an exception.

    A deployment without `WENEXT_CODEX_HOME` simply has no engine directory yet; the connections
    screen says so and offers nothing, which is different from an error.
    """
    value = (os.environ if env is None else env).get(HOME_ROOT_VARIABLE)
    return Path(value) if value else None


def require_home_root(env: Mapping[str, str] | None = None) -> Path:
    root = home_root(env)
    if root is None:
        raise CodexHomeUnavailable(
            f"brak {HOME_ROOT_VARIABLE} — nie ma korzenia wolumenu, w którym mogłyby mieszkać "
            "połączenia Codex. To błąd konfiguracji wdrożenia, nie brak połączeń"
        )
    return root


def is_valid_home_ref(home_ref: str) -> bool:
    return home_ref == ROOT_REF or bool(_UUID.match(home_ref))


def resolve_home(root: Path, home_ref: str) -> Path:
    """The absolute directory of this connection. Raises rather than guessing.

    The result is checked to be inside the root even after resolution, so a symlink planted in the
    volume cannot move a connection's home somewhere else.
    """
    if not is_valid_home_ref(home_ref):
        raise UnsafeHomeRef(
            f"home_ref={home_ref!r} nie jest ani korzeniem '.', ani UUID-em. Ścieżka spoza "
            "wolumenu kazałaby runtime'owi otworzyć cudzy katalog"
        )
    # THE ROOT IS RESOLVED FIRST AND THE CANDIDATE IS BUILT FROM THE RESOLVED ROOT. Measured on
    # macOS, where `/var` is a symlink to `/private/var`: resolving only the root and comparing it
    # with an unresolved candidate made every directory under a `mktemp` root look as if it fell
    # outside the volume, and the connection could not be created at all. The end-to-end run
    # caught it; the unit tests did not, because pytest already hands out a resolved `tmp_path`.
    resolved_root = Path(root).resolve()
    candidate = resolved_root if home_ref == ROOT_REF else resolved_root / home_ref
    # Resolve the candidate too, but only when it EXISTS — that is the check against a symlink
    # planted inside the volume, and a path that is not there yet cannot be one.
    final = candidate.resolve() if candidate.exists() else candidate
    if final != resolved_root and resolved_root not in final.parents:
        raise UnsafeHomeRef(
            f"katalog {final} wypada poza korzeń {resolved_root} — prawdopodobnie dowiązanie "
            "symboliczne w wolumenie"
        )
    return candidate


def prepare_connection_home(root: Path, home_ref: str) -> Path:
    """Create the directory of a NEW connection: 0700, with the hardened `config.toml`.

    The hardening is not re-implemented here. `codex_runtime.hardening.prepare_codex_home` is the
    one place that knows `cli_auth_credentials_store = "file"` is what makes a separate
    `CODEX_HOME` isolate anything at all (L2205), which five feature flags default to ENABLED and
    therefore have to be written out, and that `web_search` is a top-level key. A second copy of
    that knowledge here would drift, and the drift would be a connection that quietly stores its
    credentials in a shared keyring.

    The import is LAZY and its failure is loud, in the shape `observe/turns.py` already uses for
    the SDK: the package is part of the image, and if it is missing the answer is "this
    deployment cannot do it", never a directory prepared without hardening.
    """
    if home_ref == ROOT_REF:
        raise UnsafeHomeRef(
            "korzeń wolumenu nie jest przygotowywany, tylko przyjmowany. Mieszka w nim auth.json "
            "założyciela i config.toml zapisany przy logowaniu; nadpisanie go zmieniłoby "
            "hardening pod działającym pilotem"
        )
    path = resolve_home(root, home_ref)
    try:
        from codex_runtime.hardening import HardeningConfig, prepare_codex_home
    except ImportError as exc:  # pragma: no cover - the image installs the package
        raise CodexHomeUnavailable(
            "pakiet `wenext-codex-runtime` nie jest zainstalowany w tym procesie — bez niego "
            "katalog połączenia powstałby BEZ hardeningu, a wtedy osobny CODEX_HOME niczego nie "
            "izoluje (L2205)"
        ) from exc
    # `cli_auth_credentials_store="file"` is the default of `HardeningConfig` and the reason this
    # whole design works; `"auto"` is refused by the dataclass itself.
    prepare_codex_home(path, HardeningConfig())
    return path


def home_facts(path: Path) -> dict[str, object]:
    """What is actually in this directory. Read only, and every answer is a measurement.

    `logged_in` here means "there is an `auth.json`", which is weaker than what `account/read`
    says and is labelled as such everywhere it is used: the file can exist and hold an expired
    session. It is the cheap check the screen can make without starting an app-server.
    """
    path = Path(path)
    if not path.exists():
        return {"exists": False, "has_auth": False, "installation_id": None}
    installation = path / "installation_id"
    identifier: str | None = None
    if installation.is_file():
        try:
            identifier = installation.read_text(encoding="utf-8").strip() or None
        except OSError:
            identifier = None
    return {
        "exists": True,
        "has_auth": (path / "auth.json").is_file(),
        "installation_id": identifier,
    }


#: Nazwa serwera narzędzi w `config.toml`. Jedna, bo jeden serwer i jedna nazwa w całym produkcie.
TOOL_SERVER_SECTION = "[mcp_servers.wenext]"


def strip_tool_server(text: str) -> str:
    """`config.toml` bez sekcji serwera narzędzi. Reszta pliku bez zmian.

    Cięcie po tekście, a nie przez przepisanie pliku z `prepare_codex_home`, i to jest decyzja,
    nie skrót: przepisanie postawiłoby CAŁY hardening na nowo, więc dom z ręcznie dostrojoną
    wartością straciłby ją przy sprzątaniu martwego wpisu. Kształt sekcji znamy, bo generuje ją
    nasz własny renderer, a test porównuje wynik z plikiem NAPRAWDĘ przez niego wygenerowanym.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped == TOOL_SERVER_SECTION:
            skipping = True
            continue
        if skipping:
            # Sekcja kończy się na następnym nagłówku; puste linie po drodze też odpadają.
            if stripped.startswith("[") and stripped.endswith("]"):
                skipping = False
            else:
                continue
        out.append(line)
    return "".join(out)


def reconcile_home(path: Path, *, now: datetime | None = None) -> str | None:
    """Zdejmij z domu wpis serwera narzędzi, który NIE MOŻE wstać. Zwraca zdanie do logu.

    PO CO TO ISTNIEJE, zmierzone na live 21.09. Dom Codexa bywa WSPÓLNY dla pilota i rozmów.
    Rozmowa pisze w nim `[mcp_servers.wenext]` z `required = true` i ze ścieżką do swojego pliku
    zdolności; plik żyje w katalogu tymczasowym i znika przy restarcie kontenera, a wpis zostaje.
    Każda następna tura W TYM DOMU, także cudza, dostaje wtedy od app-servera „required MCP
    servers failed to initialize" i pada w całości. Sprzątanie przy starcie procesu zamyka to
    okno: dom, który został po restarcie z martwym wpisem, wstaje czysty.

    `None` znaczy „nie było czego sprzątać" i jest normalną odpowiedzią, nie ciszą. Wpis ŻYWY
    zostaje nietknięty: tura, która go napisała, może właśnie trwać.

    DO USUNIĘCIA, I TO JEST ZAPISANE NA WIDOKU. Od E9 (21.09) tura nie pisze już wpisu do
    `config.toml`, tylko podaje go procesowi w `--config`, więc nowych wpisów nie przybywa. To
    sprzątanie dotyczy wyłącznie domów zostawionych przez wdrożenia wcześniejsze. Wolno je
    skasować po DWÓCH wdrożeniach, w których żaden przebieg nie zdjął ani jednego wpisu, czyli
    nie wcześniej niż po czternastym; póki co kosztuje jeden odczyt pliku na katalog przy starcie.
    """
    from control_plane.tools.capability import CapabilityError, load_from_file

    config = path / "config.toml"
    if not config.exists():
        return None
    try:
        text = config.read_text(encoding="utf-8")
    except OSError as exc:
        return f"{path}: nie dało się przeczytać config.toml: {exc}"
    if TOOL_SERVER_SECTION not in text:
        return None

    match = re.search(r'WENEXT_SESSION_CAPABILITY_FILE"?\s*=\s*"([^"]+)"', text)
    reason: str
    if match is None:
        reason = "wpis serwera narzędzi bez pliku zdolności"
    else:
        capability_file = Path(match.group(1))
        if not capability_file.exists():
            reason = f"plik zdolności {capability_file} nie istnieje"
        else:
            try:
                load_from_file(capability_file).check_live(now)
            except CapabilityError as exc:
                reason = f"zdolność z {capability_file} nie jest ważna: {exc}"
            else:
                return None

    config.write_text(strip_tool_server(text), encoding="utf-8")
    return f"{path}: zdjęto wpis serwera narzędzi ({reason})"


def reconcile_homes(root: Path, *, now: datetime | None = None) -> list[str]:
    """To samo dla korzenia wolumenu i każdego katalogu połączenia pod nim.

    Skanujemy katalogi zamiast pytać bazę o połączenia, bo sprzątanie ma działać także wtedy,
    gdy baza jeszcze nie wstała, i dotyczy PLIKÓW, a nie wierszy.
    """
    if not root.exists():
        return []
    done: list[str] = []
    for candidate in (root, *sorted(one for one in root.iterdir() if one.is_dir())):
        note = reconcile_home(candidate, now=now)
        if note is not None:
            done.append(note)
    return done
