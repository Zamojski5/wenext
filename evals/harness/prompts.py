"""Prompt versioning. Every prompt is a file, and its version is the hash of its content.

L5308: "every draft names the exact knowledge snapshot, the prompt version, the model and
the effort". The harness applies this to EVERY eval, not only to the draft: an `EvalRecord`
carries the prompt name, its `sha256` and the runtime identity (model + effort).

So that a change of a prompt is VISIBLE rather than silent, the directory has a frozen
manifest `prompts/PROMPTS.lock`. The test `test_prompt_versioning.py` compares the computed
hashes with the manifest — editing a prompt without updating the manifest stops the suite,
and an update of the manifest is visible in the diff. This is the same mechanism as the
frozen gate thresholds (L4303, L4619), applied to prompts.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
LOCK_FILE = PROMPTS_DIR / "PROMPTS.lock"

_HEADER = re.compile(r"^---\n(?P<meta>.*?)\n---\n", re.DOTALL)


@dataclass(frozen=True)
class PromptVersion:
    """One prompt version. `sha256` computed over the FULL file content, header included."""

    name: str
    version: str
    path: Path
    text: str
    sha256: str

    @property
    def short(self) -> str:
        """Identifier for the record: `name@version+12 characters of the hash`."""
        return f"{self.name}@{self.version}+{self.sha256[:12]}"


class PromptError(RuntimeError):
    pass


def _parse(path: Path) -> PromptVersion:
    text = path.read_text(encoding="utf-8")
    match = _HEADER.match(text)
    if match is None:
        raise PromptError(
            f"Prompt {path.name} nie ma nagłówka `--- ... ---` z polami `name:` i `version:`. "
            "Bez nich rekord evala nie ma czego zapisać jako wersji promptu (L5308)."
        )
    meta: dict[str, str] = {}
    for line in match.group("meta").splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    missing = {"name", "version"} - meta.keys()
    if missing:
        raise PromptError(f"Prompt {path.name} nie ma pól: {', '.join(sorted(missing))}")
    return PromptVersion(
        name=meta["name"],
        version=meta["version"],
        path=path,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


@lru_cache(maxsize=1)
def load_all() -> dict[str, PromptVersion]:
    """All prompts from the directory, keyed by the name from the header."""
    prompts_by_name: dict[str, PromptVersion] = {}
    for path in sorted(PROMPTS_DIR.glob("*.md")):
        prompt = _parse(path)
        if prompt.name in prompts_by_name:
            raise PromptError(
                f"Dwa pliki deklarują prompt o nazwie {prompt.name!r}: "
                f"{prompts_by_name[prompt.name].path.name} i {path.name}"
            )
        prompts_by_name[prompt.name] = prompt
    if not prompts_by_name:
        raise PromptError(f"Brak promptów w {PROMPTS_DIR}")
    return prompts_by_name


def get(name: str) -> PromptVersion:
    prompts_by_name = load_all()
    try:
        return prompts_by_name[name]
    except KeyError as exc:
        raise PromptError(
            f"Nie ma promptu {name!r}. Dostępne: {', '.join(sorted(prompts_by_name))}"
        ) from exc


def read_lock() -> dict[str, str]:
    """Frozen manifest `name  sha256`. Empty lines and `#` are a comment."""
    if not LOCK_FILE.exists():
        raise PromptError(f"Brak manifestu {LOCK_FILE}")
    entries: dict[str, str] = {}
    for number, line in enumerate(LOCK_FILE.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise PromptError(
                f"{LOCK_FILE.name}:{number}: oczekiwano `nazwa sha256`, jest {line!r}"
            )
        entries[parts[0]] = parts[1]
    return entries


def render_lock() -> str:
    """Manifest content computed from the current files — for comparison and regeneration."""
    lines = [
        "# Zamrożone hasze promptów. Zmiana promptu bez zmiany tej listy zatrzymuje evale.",
        "# Regeneracja: python -m evals.harness.prompts",
    ]
    lines += [f"{p.name}  {p.sha256}" for p in sorted(load_all().values(), key=lambda p: p.name)]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":  # pragma: no cover — developer tool
    LOCK_FILE.write_text(render_lock(), encoding="utf-8")
    print(f"Zapisano {LOCK_FILE}")


__all__ = [
    "LOCK_FILE",
    "PROMPTS_DIR",
    "PromptError",
    "PromptVersion",
    "get",
    "load_all",
    "read_lock",
    "render_lock",
]
