"""Prompt versioning for the observe module — the mechanism of `evals/harness/prompts.py`, applied
to production prompts.

Every `community_pictures` and `thread_judgments` row names the prompt it came from by name
AND by the SHA-256 of the full prompt file. Without the digest the flywheel of
`docs/KIERUNEK-PRODUKTU.md` §8 has data and no way to tell which change of wording made the
judgments better or worse — the same reason eval records carry it (roadmap L5308).

`PROMPTS.lock` next to the files freezes the digests. Editing a prompt without regenerating
the manifest fails `tests/test_observe_prompts.py`; regenerating it is a visible diff. Not
imported from `evals/`: `evals/` is a test package with the repo root on `sys.path`, and the
control plane runs in an image where `evals/` does not exist.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from community_os_contracts import ThreadRole

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
LOCK_FILE = PROMPTS_DIR / "PROMPTS.lock"

_HEADER = re.compile(r"^---\n(?P<meta>.*?)\n---\n", re.DOTALL)

#: The prompts this module owns. A file in the directory that is not on this list is an error,
#: and so is a name on this list without a file — both fail the test, in both directions.
PROMPT_NAMES: tuple[str, ...] = (
    "community_picture",
    "judge_thread",
    "community_voice",
    "draft_judge",
    "reaction_baseline",
)


class PromptError(RuntimeError):
    pass


@dataclass(frozen=True)
class PromptVersion:
    name: str
    version: str
    role: ThreadRole
    path: Path
    text: str
    sha256: str

    @property
    def short(self) -> str:
        return f"{self.name}@{self.version}+{self.sha256[:12]}"


def _parse(path: Path) -> PromptVersion:
    text = path.read_text(encoding="utf-8")
    match = _HEADER.match(text)
    if match is None:
        raise PromptError(
            f"Prompt {path.name} nie ma nagłówka `--- ... ---` z polami name/version/role"
        )
    meta: dict[str, str] = {}
    for line in match.group("meta").splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    missing = {"name", "version", "role"} - meta.keys()
    if missing:
        raise PromptError(f"Prompt {path.name} nie ma pól: {', '.join(sorted(missing))}")
    try:
        role = ThreadRole(meta["role"])
    except ValueError as exc:
        raise PromptError(
            f"Prompt {path.name}: role={meta['role']!r} nie jest wartością ThreadRole"
        ) from exc
    return PromptVersion(
        name=meta["name"],
        version=meta["version"],
        role=role,
        path=path,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


@lru_cache(maxsize=1)
def load_all() -> dict[str, PromptVersion]:
    prompts: dict[str, PromptVersion] = {}
    for path in sorted(PROMPTS_DIR.glob("*.md")):
        prompt = _parse(path)
        if prompt.name in prompts:
            raise PromptError(f"Dwa pliki deklarują prompt {prompt.name!r}")
        prompts[prompt.name] = prompt
    if set(prompts) != set(PROMPT_NAMES):
        raise PromptError(
            f"Prompty na dysku {sorted(prompts)} nie zgadzają się z PROMPT_NAMES "
            f"{sorted(PROMPT_NAMES)}"
        )
    return prompts


def get(name: str) -> PromptVersion:
    try:
        return load_all()[name]
    except KeyError as exc:
        raise PromptError(f"Nie ma promptu {name!r}. Dostępne: {', '.join(PROMPT_NAMES)}") from exc


def read_lock() -> dict[str, str]:
    if not LOCK_FILE.exists():
        raise PromptError(f"Brak manifestu {LOCK_FILE}")
    entries: dict[str, str] = {}
    for number, raw in enumerate(LOCK_FILE.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
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
    lines = [
        "# Zamrożone hasze promptów modułu observe. Regeneracja:",
        "#   python -m control_plane.observe.prompts",
        "# Zmiana promptu bez odświeżenia tego pliku zatrzymuje suitę",
        "# (tests/test_observe_prompts.py).",
    ]
    lines += [f"{p.name} {p.sha256}" for p in sorted(load_all().values(), key=lambda p: p.name)]
    return "\n".join(lines) + "\n"


def main() -> int:
    LOCK_FILE.write_text(render_lock(), encoding="utf-8")
    print(f"zapisano {LOCK_FILE}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
