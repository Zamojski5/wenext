"""The rename tool must actually run, and must refuse to touch data keys.

`docs/KONWENCJE.md` requires every identifier rename to go through
`tools/dev/rename_identifiers.py`. The tool had no test, and it was broken at its
entry point: it read `args.mapping` while argparse declared the argument as `mapa`,
so every invocation died with AttributeError before touching a single file.

`make test-all` stayed green throughout, because nothing exercised the tool. A tool
mandated by the conventions and covered by no test is a convention that silently
stops being enforced — hence this file.

The refusal path matters at least as much as the happy path: gate-control names and
artifact JSON keys are data, and renaming one of them decouples the Go/No-Go report
from the evidence it cites.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "dev" / "rename_identifiers.py"


def _run(
    mapping: dict[str, str], *paths: pathlib.Path, apply: bool = False, tmp_path: pathlib.Path
) -> subprocess.CompletedProcess[str]:
    map_file = tmp_path / "mapa.json"
    map_file.write_text(json.dumps(mapping), encoding="utf-8")
    argv = [sys.executable, str(TOOL), str(map_file), *[str(p) for p in paths]]
    if apply:
        argv.append("--apply")
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def test_tool_starts_at_all(tmp_path: pathlib.Path) -> None:
    """The regression proper: the entry point must not die on its own arguments."""
    target = tmp_path / "probka.py"
    target.write_text("stara_nazwa = 1\n", encoding="utf-8")

    result = _run({"stara_nazwa": "new_name"}, target, tmp_path=tmp_path)

    assert result.returncode == 0, f"tool failed to start: {result.stderr}"
    assert "AttributeError" not in result.stderr


def test_preview_does_not_write(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "probka.py"
    target.write_text("stara_nazwa = 1\n", encoding="utf-8")

    _run({"stara_nazwa": "new_name"}, target, tmp_path=tmp_path)

    assert target.read_text(encoding="utf-8") == "stara_nazwa = 1\n", (
        "without --apply the tool must change nothing on disk"
    )


def test_apply_renames_only_name_tokens(tmp_path: pathlib.Path) -> None:
    """Strings and comments keep the old spelling — that is the point of tokenising."""
    target = tmp_path / "probka.py"
    target.write_text(
        'stara_nazwa = "stara_nazwa"  # stara_nazwa\n',
        encoding="utf-8",
    )

    result = _run({"stara_nazwa": "new_name"}, target, apply=True, tmp_path=tmp_path)
    content = target.read_text(encoding="utf-8")

    assert result.returncode == 0
    assert content.startswith("new_name =")
    assert '"stara_nazwa"' in content, "string literals must not be rewritten"
    assert "# stara_nazwa" in content, "comments must not be rewritten"


def test_refuses_names_that_are_data_keys(tmp_path: pathlib.Path) -> None:
    """Renaming an artifact key would decouple the report from its evidence."""
    result = _run({"zdane": "passed"}, ROOT / "tools", tmp_path=tmp_path)

    assert result.returncode == 2
    assert "ODMOWA" in result.stderr
    assert "zdane" in result.stderr


def test_refuses_chained_renames(tmp_path: pathlib.Path) -> None:
    """A → B and B → C in one map would apply twice depending on token order."""
    result = _run({"a_name": "b_name", "b_name": "c_name"}, ROOT / "tools", tmp_path=tmp_path)

    assert result.returncode == 2
    assert "łańcuchowa" in result.stderr
