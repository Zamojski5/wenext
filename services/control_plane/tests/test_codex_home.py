"""`control_plane.workspace.codex_home` — where a connection's `CODEX_HOME` is, and what it is not.

No database and no app-server. Everything here is about a path, and paths are where "one
connection per directory" either holds or quietly stops holding.

THE ROOT IS THE CASE THAT MATTERS. The volume root is the founder's existing login and the live
pilot runs on it. `prepare_connection_home` refuses it, and this file is what makes that refusal
survive a later "simplification" — the tempting version of the function treats the root as just
another directory and rewrites its `config.toml`, changing the hardening under a running pilot.
"""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

import pytest
from control_plane.workspace.codex_home import (
    HOME_ROOT_VARIABLE,
    ROOT_REF,
    CodexHomeUnavailable,
    UnsafeHomeRef,
    home_facts,
    home_root,
    is_valid_home_ref,
    prepare_connection_home,
    require_home_root,
    resolve_home,
)

#: Shapes a `home_ref` may never take. The CHECK in migration 0014 refuses them on the way in;
#: this list is the second lock, for a row that is already there — a restored backup, a manual
#: edit — being turned into a path.
UNSAFE: tuple[str, ...] = (
    "..",
    "../inny",
    "/etc",
    "a/b",
    "",
    " ",
    "./.",
    "0000-0000",
    "AAAAAAAA-0000-4000-8000-000000000000",
)


def test_only_the_root_and_a_uuid_are_valid_refs() -> None:
    assert len(UNSAFE) == 9, UNSAFE
    assert is_valid_home_ref(ROOT_REF)
    assert is_valid_home_ref(str(uuid.uuid4()))
    for candidate in UNSAFE:
        assert not is_valid_home_ref(candidate), candidate


def test_resolve_refuses_every_unsafe_ref(tmp_path: Path) -> None:
    for candidate in UNSAFE:
        with pytest.raises(UnsafeHomeRef):
            resolve_home(tmp_path, candidate)


def test_resolve_puts_the_root_at_the_root_and_a_uuid_beside_it(tmp_path: Path) -> None:
    identifier = str(uuid.uuid4())
    assert resolve_home(tmp_path, ROOT_REF) == tmp_path
    assert resolve_home(tmp_path, identifier) == tmp_path / identifier


def test_a_symlink_cannot_move_a_connection_outside_the_volume(tmp_path: Path) -> None:
    """The CHECK constrains the NAME; a link constrains nothing, so the path is checked too.

    A directory planted in the volume as a link to somewhere else would otherwise hand one
    connection another's credentials, with a perfectly valid-looking `home_ref`.
    """
    root = tmp_path / "volume"
    root.mkdir()
    outside = tmp_path / "gdzie-indziej"
    outside.mkdir()
    identifier = str(uuid.uuid4())
    (root / identifier).symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeHomeRef):
        resolve_home(root, identifier)


def test_a_root_reached_through_a_symlink_is_still_the_root(tmp_path: Path) -> None:
    """The regression the end-to-end run caught, and the unit tests had not.

    On macOS `/var` is a symlink to `/private/var`, so a `mktemp` root arrives as `/var/folders/…`
    while its resolution is `/private/var/folders/…`. The first version resolved only the root and
    compared it with an UNRESOLVED candidate, so every subdirectory looked as if it fell outside
    the volume and no connection could be created at all. `pytest`'s own `tmp_path` is already
    resolved, which is exactly why no unit test saw it — this one builds the symlink by hand.
    """
    real = tmp_path / "prawdziwy"
    real.mkdir()
    link = tmp_path / "przez-dowiazanie"
    link.symlink_to(real, target_is_directory=True)

    identifier = str(uuid.uuid4())
    resolved = resolve_home(link, identifier)
    assert resolved == real / identifier
    assert resolve_home(link, ROOT_REF) == real

    home = prepare_connection_home(link, identifier)
    assert home.is_dir()
    assert (home / "config.toml").is_file()


def test_the_volume_root_is_never_prepared(tmp_path: Path) -> None:
    """Adopting an existing connection must not write into its directory.

    Asserted by bytes, not by the exception alone: a version that raised AFTER writing would pass
    a test that only checked the exception.
    """
    config = tmp_path / "config.toml"
    config.write_text('web_search = "disabled"\n', encoding="utf-8")
    before = config.read_bytes()

    with pytest.raises(UnsafeHomeRef):
        prepare_connection_home(tmp_path, ROOT_REF)
    assert config.read_bytes() == before


def test_a_new_connection_gets_a_0700_directory_with_the_hardened_config(tmp_path: Path) -> None:
    """The config is written by `codex_runtime.hardening`, and this checks what landed.

    `cli_auth_credentials_store = "file"` is the line the whole design rests on: with `auto` or a
    shared keyring a separate `CODEX_HOME` isolates nothing (L2205). It is asserted here by
    reading the file, because "we called the right function" is not the same claim.
    """
    identifier = str(uuid.uuid4())
    home = prepare_connection_home(tmp_path, identifier)

    assert home == tmp_path / identifier
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    config = (home / "config.toml").read_text(encoding="utf-8")
    assert 'cli_auth_credentials_store = "file"' in config
    assert 'web_search = "disabled"' in config
    assert "shell_tool = false" in config
    assert stat.S_IMODE((home / "config.toml").stat().st_mode) == 0o600


def test_preparing_two_connections_leaves_each_ones_files_alone(tmp_path: Path) -> None:
    """Two directories, two configs, and the first one untouched by the second.

    Byte comparison, because "the second one works" is compatible with the first one having been
    rewritten — and a rewritten hardening is invisible until a credential ends up somewhere else.
    """
    first = prepare_connection_home(tmp_path, str(uuid.uuid4()))
    # Stand-in for "the first connection is logged in". Not a real token.
    (first / "auth.json").write_text('{"atrapa": true}', encoding="utf-8")
    os.chmod(first / "auth.json", 0o600)
    before = {path.name: path.read_bytes() for path in sorted(first.iterdir()) if path.is_file()}

    second = prepare_connection_home(tmp_path, str(uuid.uuid4()))
    assert second != first
    after = {path.name: path.read_bytes() for path in sorted(first.iterdir()) if path.is_file()}
    assert after == before, "przygotowanie drugiego połączenia zmieniło pliki pierwszego"
    assert not (second / "auth.json").exists()


def test_home_facts_report_what_is_really_there(tmp_path: Path) -> None:
    missing = home_facts(tmp_path / "nie-ma")
    assert missing == {"exists": False, "has_auth": False, "installation_id": None}

    home = prepare_connection_home(tmp_path, str(uuid.uuid4()))
    empty = home_facts(home)
    assert empty["exists"] is True
    assert empty["has_auth"] is False
    assert empty["installation_id"] is None

    (home / "auth.json").write_text("{}", encoding="utf-8")
    (home / "installation_id").write_text("abc-123\n", encoding="utf-8")
    filled = home_facts(home)
    assert filled["has_auth"] is True
    assert filled["installation_id"] == "abc-123"


def test_a_deployment_without_the_variable_says_so_rather_than_guessing() -> None:
    """None is a FACT the screen states; `require_home_root` is for callers that cannot continue."""
    assert home_root({}) is None
    assert home_root({HOME_ROOT_VARIABLE: ""}) is None
    assert home_root({HOME_ROOT_VARIABLE: "/var/wenext/codex-home"}) == Path(
        "/var/wenext/codex-home"
    )
    with pytest.raises(CodexHomeUnavailable, match=HOME_ROOT_VARIABLE):
        require_home_root({})
