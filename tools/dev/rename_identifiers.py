#!/usr/bin/env python3
"""Renaming identifiers in Python code — without touching strings and comments.

Why a separate tool instead of `sed`:

Error messages in this repo are in Polish DELIBERATELY: the operator reads them, and some
tests match against them through `pytest.raises(match=...)`. Report text and gate evidence
are Polish for the same reason (D-010 keeps both out of the migration). A text substitution
would rewrite all of that along with the code and silently break both the operator-facing
output and the tests that pin it.

That is why we walk over TOKENS: we replace only tokens of type NAME. A string, a comment
and a number stay untouched, whatever stands inside them.

The second thing this tool guards: **names that are DATA, not code.**
Gate-control identifiers (`unattended_rozstrzygniete`, `brak_publicznego_cdp`…)
live as strings in `criteria.yaml` and in the saved JSON artifacts. Since we do not touch
strings, they are safe by themselves — but if somebody added them to the map as
identifiers, they would break the link between the report and the evidence. Hence the
`FORBIDDEN_NAMES` list.

Usage:
    python3 tools/dev/rename_identifiers.py map.json [--apply] [path ...]

Without `--apply` it only shows what it would change.
"""

from __future__ import annotations

import argparse
import io
import json
import pathlib
import sys
import tokenize

#: Names that MUST NOT be renamed, because they are at the same time keys in the data.
#: Changing any of them decouples the Go/No-Go report from the artifacts with evidence.
FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        # gate-control identifiers — see tools/gonogo/criteria.yaml
        "unattended_rozstrzygniete",
        "brak_publicznego_cdp",
        "zgodnosc_builda",
        "persistent_context",
        "locatory",
        "screenshoty",
        "popupy",
        "traces",
        "reattach_gatewaya",
        "stale_devtools_port",
        "kanoniczne_argumenty",
        "proces_przezywa_browser_close",
        "headful_startuje_na_tym_samym_profilu",
        "sesja_przezywa_przelaczenie_do_headless",
        "brak_kopiowania_cookies",
        "powod_spoza_listy_odrzucony",
        "przejscie_trybu_tworzy_nowa_sesje",
        "sdk_wystawia_model_effort_schema",
        "model_list_pelna_paginacja",
        "cztery_role_w_osobnych_watkach",
        "brak_shell_i_exec",
        "tylko_dozwolony_mcp",
        # pola SoakRun z tools/dev/soak_queue.py — asdict() wypisuje je WPROST jako klucze
        # w artifacts/soak/soak-queue.json, tak samo jak UnattendedRun w bramce nr 3.
        # Zmiana którejkolwiek rozspaja artefakt od dokumentu, który go cytuje.
        "rundy",
        "jobow_wstawionych",
        "claimow_wydanych",
        "claimow_unikalnych",
        "podwojnych_claimow",
        "workerow_zabitych",
        "prob_odzyskanych_przez_reapera",
        "jobow_zterminalizowanych",
        "przekroczen_limitu_prob",
        "jobow_zgubionych",
        "wyjatkow_w_petli_reapera",
        "naruszenia",
        # keys in the JSON of the artifacts
        "wymaga_czlowieka",
        "kontrole",
        "zdane",
        "dowod",
        "szczegoly",
        "nazwa",
        "wymog_z_dokumentu",
        "przebieg",
        # Dataclass fields that `asdict()` writes STRAIGHT into the artifacts. They look
        # like ordinary identifiers and that is exactly why they are dangerous: renaming
        # a field silently changes a key in the evidence file, and the report stops
        # finding it. Sources: `GateResult` in tools/gonogo/report.py → gonogo.json;
        # `UnattendedRun` and `RunEvent` in codex_runtime/unattended.py → gate3.
        "bramki",
        "werdykt",
        "pytanie",
        "uzasadnienie",
        "rozpoczety",
        "zakonczony",
        "przerwany_przez_czlowieka",
        "restartow",
        "reauth_wymagane",
        "czas",
        "rodzaj",
        "szczegol",
    }
)

SKIPPED_DIRS = {".git", ".venv", "__pycache__", "artifacts", "node_modules", ".pytest_cache", "var"}


def rename_in_content(content: str, mapping: dict[str, str]) -> tuple[str, int]:
    """Returns (new content, number of replacements). Replaces only NAME tokens."""
    result: list[tokenize.TokenInfo] = []
    replacements = 0
    read = io.StringIO(content).readline

    for token in tokenize.generate_tokens(read):
        if token.type == tokenize.NAME and token.string in mapping:
            result.append(token._replace(string=mapping[token.string]))
            replacements += 1
        else:
            result.append(token)

    return tokenize.untokenize(result), replacements


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zmiana nazw identyfikatorów")
    parser.add_argument("map_file", type=pathlib.Path, help="JSON: {stara_nazwa: nowa_nazwa}")
    parser.add_argument("paths", nargs="*", type=pathlib.Path, default=[pathlib.Path(".")])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    mapping: dict[str, str] = json.loads(args.map_file.read_text(encoding="utf-8"))

    collisions = sorted(set(mapping) & FORBIDDEN_NAMES)
    if collisions:
        print(
            "ODMOWA: mapa zawiera nazwy, które są jednocześnie kluczami w danych "
            f"(criteria.yaml / artefakty): {', '.join(collisions)}",
            file=sys.stderr,
        )
        return 2

    reverse = [n for n in mapping.values() if n in mapping]
    if reverse:
        print(f"ODMOWA: nowa nazwa jest też starą — łańcuchowa zamiana: {reverse}", file=sys.stderr)
        return 2

    files: list[pathlib.Path] = []
    for path in args.paths:
        if path.is_file():
            files.append(path)
        else:
            files.extend(p for p in path.rglob("*.py") if not (SKIPPED_DIRS & set(p.parts)))

    total_replacements = 0
    touched = 0
    for file_path in sorted(set(files)):
        oryginal = file_path.read_text(encoding="utf-8")
        try:
            updated, replacements = rename_in_content(oryginal, mapping)
        except tokenize.TokenError as exc:
            print(f"  POMINIĘTY {file_path}: {exc}", file=sys.stderr)
            continue
        if replacements:
            total_replacements += replacements
            touched += 1
            print(f"  {file_path}: {replacements}")
            if args.apply:
                file_path.write_text(updated, encoding="utf-8")

    mode = "zastosowano" if args.apply else "podgląd (bez --apply nic nie zapisano)"
    print(f"\n{mode}: {total_replacements} zamian w {touched} plikach")
    return 0


if __name__ == "__main__":
    sys.exit(main())
