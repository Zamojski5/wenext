"""The conversation eval runner: episodes in, one reaction each through the product's port, and a
run directory a human can review (R1, `docs/ROADMAP-JAKOSC-ROZMOW.md`).

Two runtimes behind the same `ModelTurnPort`:

* `RecordedPort` (offline, the default): answers from `recorded/<episode-id>.json`. It proves the
  mechanics (loading, the generator's contract, counters, artifacts) and nothing about a model. A
  recording is written by hand or copied from a live run; every file says which. The runner tells
  the port which episode is next (`expect`); the model input itself carries no episode id.
* `CodexTurnRunner` (live, `--runtime codex`): the product's own runner on the founder's Codex
  login. Only this one measures a model, and only a human's rating measures quality.

Every episode ends in exactly one status. `GENERATED` is the only one that carries a reaction;
everything else is UNKNOWN for every counter, spelled out rather than dropped:

* `REJECTED`: the model answered and the answer broke the generator's contract (counted, with the
  rule it broke);
* `MODEL_UNAVAILABLE`: no model (not logged in, usage limit, no SDK);
* `PERSONA_UNRESOLVED`: the persona reference has neither a file under `personas/` nor a database
  behind `--persona-db`. The founder's profile is never a file in the repository (decision of
  2026-09-06), so offline runs over real episodes end here by design, and the count says so;
* `FAILED`: anything else (no recording, a broken turn).

A run whose episodes are not all `GENERATED` exits non-zero unless `--allow-incomplete` says the
caller knows. Every episode is `HUMAN_REVIEW_PENDING` until the operator rates the produced reaction;
the literal appears in `run.json` and in `report.md`, and nothing here fills it in.

What one run writes under `artifacts/conversation-quality/<run-id>/`: `run.json` (environment,
prompt name + sha256, HEAD, loader warnings, counters with an UNKNOWN bucket each, every record),
`episodes/<id>.json`, and `report.md`: the review document. The report groups episodes by thread,
in the thread's chronological order (`captured_at`), shows the first episode's transcript whole
and every later one as the delta of new messages ("ciąg dalszy wątku"), and shuffles the THREADS
with a seed recorded in `run.json`. It never shows the reference or the model's name, and
`write_run` refuses to write a report in which any episode's reference text or rationale appears
anywhere, or a later message of an episode appears in that episode's own section.

The generator never sees `allowed_reactions` (the reviewer's rubric; compared after generation as
`kind_acceptable`), never sees the reference (`Episode.for_generator()` is the only handover and
`situation_from_mapping` reads only the input fields) and never sees an episode or message id
(`reactions.reaction_input` neutralises them).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from community_os_contracts import ReactionKind, ThreadRole
from control_plane.observe import prompts
from control_plane.observe.reactions import (
    PROMPT_NAME,
    ReactionRejected,
    ReactionResult,
    generate_reaction,
    situation_from_mapping,
)
from control_plane.observe.turns import (
    CodexTurnRunner,
    ModelTurnPort,
    ModelUnavailable,
    TurnFailed,
    TurnOutcome,
)

from .schema import (
    DEFAULT_EPISODES_DIR,
    DEFAULT_PERSONAS_DIR,
    DEFAULT_REFERENCES_DIR,
    Episode,
    EpisodeFileError,
    EpisodeMessage,
    Persona,
    PersonaRef,
    Split,
    load_episode_set,
    load_persona,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECORDED_DIR = Path(__file__).resolve().parent / "recorded"
DEFAULT_OUT_DIR = REPO_ROOT / "artifacts" / "conversation-quality"

HUMAN_REVIEW_PENDING = "HUMAN_REVIEW_PENDING"

STATUS_GENERATED = "GENERATED"
STATUS_REJECTED = "REJECTED"
STATUS_MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
STATUS_PERSONA_UNRESOLVED = "PERSONA_UNRESOLVED"
STATUS_FAILED = "FAILED"
#: Everything that is not a generated reaction. Counted as UNKNOWN in every counter.
UNKNOWN_STATUSES: frozenset[str] = frozenset(
    {STATUS_REJECTED, STATUS_MODEL_UNAVAILABLE, STATUS_PERSONA_UNRESOLVED, STATUS_FAILED}
)
UNKNOWN = "UNKNOWN"

#: The api's DSN variable (`control_plane.auth.DSN_VARIABLE`). SQLAlchemy-style schemes are
#: normalised for asyncpg, which knows only `postgresql://`.
DSN_VARIABLE = "DATABASE_URL"

#: How the report names each kind for a Polish-reading reviewer. The enum stays in the JSON.
KIND_LABELS: dict[ReactionKind, str] = {
    ReactionKind.HELP: "pomoc",
    ReactionKind.DIAGNOSE: "diagnoza",
    ReactionKind.CLARIFY: "doprecyzowanie",
    ReactionKind.DISAGREE: "niezgoda",
    ReactionKind.SHORT_ACK: "krótkie potwierdzenie",
    ReactionKind.HUMOR: "humor",
    ReactionKind.WAIT: "czekać",
    ReactionKind.SILENCE: "milczenie",
}

REPORT_TRANSCRIPT_CHARS = 900
#: Reference pieces shorter than this are not searched for in the report: a two-word rationale
#: would match ordinary prose. Longer pieces are searched for in windows of `LEAK_WINDOW_CHARS`.
LEAK_MIN_CHARS = 20


class RecordingMissing(TurnFailed):
    """No recording for this episode. A failure of the run, never a silent empty answer."""


class ReportLeak(RuntimeError):
    """The rendered report contains reference material. Nothing is written."""


class RecordedPort:
    """`ModelTurnPort` that answers from recordings. The runner names the next episode through
    `expect()`; the mapping never travels inside the model input."""

    kind = "recorded"

    def __init__(self, recordings: Mapping[str, Mapping[str, Any]]) -> None:
        self._recordings = dict(recordings)
        self._expected: str | None = None
        self.calls: list[str] = []

    @classmethod
    def from_dir(cls, root: Path) -> RecordedPort:
        recordings: dict[str, dict[str, Any]] = {}
        if root.is_dir():
            for path in sorted(root.glob("*.json")):
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise EpisodeFileError(f"{path}: nagranie musi być obiektem JSON")
                recordings[path.stem] = {k: v for k, v in raw.items() if not k.startswith("_")}
        return cls(recordings)

    def expect(self, episode_id: str) -> None:
        self._expected = episode_id

    async def run(
        self, *, role: ThreadRole, instructions: str, user_input: str, output_schema: dict
    ) -> TurnOutcome:
        episode_id = self._expected
        if episode_id is None:
            raise TurnFailed("RecordedPort: runner nie nazwał epizodu przed wywołaniem (expect)")
        self.calls.append(episode_id)
        recording = self._recordings.get(episode_id)
        if recording is None:
            raise RecordingMissing(
                f"brak nagrania dla epizodu {episode_id!r} w recorded/ (runtime offline nie "
                "zgaduje odpowiedzi)"
            )
        return TurnOutcome(structured_output=dict(recording), model="recorded", effort="none")


@dataclass
class EpisodeRun:
    """What one episode produced. `reference_*` fields are metadata only, never the text."""

    episode_id: str
    thread_key: str
    captured_at: str
    split: str
    synthetic: bool
    relation: str
    persona: str
    status: str
    human_review: str = HUMAN_REVIEW_PENDING
    decision: str | None = None
    kind_acceptable: bool | None = None
    draft: str | None = None
    target_message_id: str | None = None
    contribution: str | None = None
    brief_rationale: str | None = None
    confidence: float | None = None
    sources_used: list[str] = field(default_factory=list)
    long_dash_count: int = 0
    model: str | None = None
    effort: str | None = None
    prompt_name: str | None = None
    prompt_sha256: str | None = None
    input_sha256: str | None = None
    error: str | None = None
    reference_present: bool = False
    reference_status: str | None = None
    generated_at: str | None = None

    @property
    def generated(self) -> bool:
        return self.status == STATUS_GENERATED


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    runtime: str
    prompt_name: str
    prompt_sha256: str
    generated_at: str
    head: str | None
    counts: dict[str, Any]
    records: tuple[EpisodeRun, ...]
    #: Thread keys in report order (seeded shuffle); episodes inside a thread go by `captured_at`.
    report_order: tuple[str, ...]
    loader_warnings: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return all(r.generated for r in self.records)

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "runtime": self.runtime,
            "prompt_name": self.prompt_name,
            "prompt_sha256": self.prompt_sha256,
            "generated_at": self.generated_at,
            "head": self.head,
            "complete": self.complete,
            "human_review": HUMAN_REVIEW_PENDING,
            "loader_warnings": list(self.loader_warnings),
            "counts": self.counts,
            "report_order_threads": list(self.report_order),
            "episodes": [asdict(r) for r in self.records],
        }


async def load_persona_from_db(ref: PersonaRef, *, dsn: str) -> Persona:
    """The founder's real profile version from `user_profile_versions` (one operator, one
    workspace: D-016). Live runs inside the `api` container use this; the profile text never
    lands in the repository."""
    import asyncpg

    normalised = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(normalised)
    try:
        row = await connection.fetchrow(
            """
            SELECT version, knows_deeply, positions, would_never_claim, voice_samples
            FROM user_profile_versions
            WHERE version = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            ref.profile_version,
        )
    finally:
        await connection.close()
    if row is None:
        raise EpisodeFileError(
            f"brak wersji profilu {ref.profile_version} w user_profile_versions dla persony "
            f"{ref.label!r}"
        )
    return Persona(
        label=ref.label,
        profile_version=int(row["version"]),
        knows_deeply=row["knows_deeply"],
        positions=row["positions"],
        would_never_claim=row["would_never_claim"],
        voice_samples=row["voice_samples"] or "",
        synthetic=False,
    )


async def resolve_persona(
    ref: PersonaRef, *, personas_dir: Path, db_dsn: str | None = None
) -> Persona | None:
    """A persona file first, the database when `db_dsn` is given, otherwise `None`: the episode
    is then `PERSONA_UNRESOLVED`. No placeholder: a reaction generated against an invented profile
    would be a measurement of nothing, and the recorded runtime does not need the profile to prove
    the mechanics on the demonstration episodes, whose persona is a file."""
    try:
        return load_persona(ref, personas_dir)
    except EpisodeFileError:
        if db_dsn:
            return await load_persona_from_db(ref, dsn=db_dsn)
        return None


def select_episodes(
    episodes: Iterable[Episode],
    *,
    splits: set[Split],
    include_synthetic: bool,
    only_synthetic: bool,
) -> tuple[Episode, ...]:
    chosen = []
    for episode in episodes:
        if episode.input.split not in splits:
            continue
        if episode.input.synthetic and not (include_synthetic or only_synthetic):
            continue
        if only_synthetic and not episode.input.synthetic:
            continue
        chosen.append(episode)
    return tuple(chosen)


def _thread_key(episode: Episode) -> str:
    platform, external_id = episode.thread_key
    return f"{platform}:{external_id}"


async def run_one(
    episode: Episode, *, port: ModelTurnPort, persona: Persona | None, now: datetime
) -> EpisodeRun:
    handed = episode.for_generator()
    raw_input = handed.model_dump(mode="json")
    record = EpisodeRun(
        episode_id=handed.id,
        thread_key=_thread_key(episode),
        captured_at=handed.captured_at.isoformat(),
        split=handed.split.value,
        synthetic=handed.synthetic,
        relation=handed.relation.state.value,
        persona=f"{handed.persona.label}.v{handed.persona.profile_version}",
        status=STATUS_FAILED,
        input_sha256=hashlib.sha256(
            json.dumps(raw_input, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        reference_present=episode.reference is not None,
        reference_status=(episode.reference.status.value if episode.reference else None),
    )
    if persona is None:
        record.status = STATUS_PERSONA_UNRESOLVED
        record.error = (
            f"persona {record.persona} bez pliku w personas/ i bez bazy (--persona-db); profil "
            "założyciela nie jest plikiem w repo, więc offline ten epizod nie ma reakcji"
        )
        return record
    expect = getattr(port, "expect", None)
    if callable(expect):
        expect(handed.id)
    try:
        situation = situation_from_mapping(raw_input)
        result: ReactionResult = await generate_reaction(
            port, situation=situation, profile=persona.as_mapping(), now=now
        )
    except ReactionRejected as exc:
        record.status = STATUS_REJECTED
        record.error = str(exc)
        return record
    except ModelUnavailable as exc:
        record.status = STATUS_MODEL_UNAVAILABLE
        record.error = str(exc)
        return record
    except TurnFailed as exc:
        record.status = STATUS_FAILED
        record.error = str(exc)
        return record
    reaction = result.reaction
    record.status = STATUS_GENERATED
    record.decision = reaction.decision.value
    record.kind_acceptable = reaction.decision in handed.allowed_reactions
    record.draft = reaction.draft
    record.target_message_id = reaction.target_message_id
    record.contribution = reaction.contribution
    record.brief_rationale = reaction.brief_rationale
    record.confidence = reaction.confidence
    record.sources_used = list(reaction.sources_used)
    record.long_dash_count = reaction.long_dash_count
    record.model = result.model
    record.effort = result.effort
    record.prompt_name = result.prompt_name
    record.prompt_sha256 = result.prompt_sha256
    record.generated_at = result.generated_at.isoformat()
    return record


def _with_unknown(counter: Counter[str], unknown: int) -> dict[str, int]:
    out = dict(sorted(counter.items()))
    out[UNKNOWN] = unknown
    return out


def summarize(
    records: Sequence[EpisodeRun],
    *,
    run_id: str,
    runtime: str,
    now: datetime,
    loader_warnings: Sequence[str] = (),
) -> RunSummary:
    prompt = prompts.get(PROMPT_NAME)
    generated = [r for r in records if r.generated]
    unknown = [r for r in records if not r.generated]
    counts: dict[str, Any] = {
        "episodes": len(records),
        "generated": len(generated),
        "unknown": len(unknown),
        "unknown_reasons": dict(sorted(Counter(r.status for r in unknown).items())),
        "by_status": dict(sorted(Counter(r.status for r in records).items())),
        "by_split": dict(sorted(Counter(r.split for r in records).items())),
        "by_relation": dict(sorted(Counter(r.relation for r in records).items())),
        "synthetic": sum(1 for r in records if r.synthetic),
        "real": sum(1 for r in records if not r.synthetic),
        "by_decision": _with_unknown(Counter(r.decision or "?" for r in generated), len(unknown)),
        "kind_acceptable": {
            "yes": sum(1 for r in generated if r.kind_acceptable is True),
            "no": sum(1 for r in generated if r.kind_acceptable is False),
            UNKNOWN: len(unknown),
        },
        "with_draft": {
            "yes": sum(1 for r in generated if r.draft is not None),
            "no": sum(1 for r in generated if r.draft is None),
            UNKNOWN: len(unknown),
        },
        "long_dashes_rewritten": sum(r.long_dash_count for r in generated),
        HUMAN_REVIEW_PENDING: sum(1 for r in records if r.human_review == HUMAN_REVIEW_PENDING),
        "reference_present": sum(1 for r in records if r.reference_present),
        "reference_reviewed_by_operator": sum(
            1 for r in records if r.reference_status == "REVIEWED_BY_OPERATOR"
        ),
        "threads": len({r.thread_key for r in records}),
        "models": sorted({r.model for r in generated if r.model}),
    }
    threads = sorted({r.thread_key for r in records})
    random.Random(run_id).shuffle(threads)
    return RunSummary(
        run_id=run_id,
        runtime=runtime,
        prompt_name=prompt.name,
        prompt_sha256=prompt.sha256,
        generated_at=now.isoformat(),
        head=_git_head(),
        counts=counts,
        records=tuple(records),
        report_order=tuple(threads),
        loader_warnings=tuple(loader_warnings),
    )


def _git_head() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _render_message(message: EpisodeMessage, *, target_id: str) -> list[str]:
    who = message.author if message.author is not None else "[usunięty]"
    marker = " **[konto]**" if message.own else ""
    arrow = " ← **odpowiadamy na to**" if message.message_id == target_id else ""
    lines = [f"- `{message.message_id}` {who}{marker}{arrow}:"]
    for row in _clip(message.body, REPORT_TRANSCRIPT_CHARS).splitlines() or [""]:
        lines.append(f"  > {row}")
    return lines


def _rating_form(record: EpisodeRun) -> list[str]:
    return [
        f"Status oceny: {record.human_review}",
        "",
        "Ocena (wypełnia operator):",
        "- Czy ten rodzaj reakcji pasuje do sytuacji? Tak / Nie / Nie umiem ocenić",
        "- Czy ja bym to napisał lub tak przemilczał? "
        "Tak / Po drobnej zmianie / Po przepisaniu / Nie",
        "- Czy treść wnosi to, czego potrzebuje rozmowa? Tak / Nie / Nie dotyczy tekstu",
        "- Czy przypisano mi nieprawdziwy fakt lub stanowisko? "
        "Tak, jakie / Nie / Wymaga sprawdzenia",
        "- Co trzeba zmienić?",
        "",
    ]


def render_sections(
    summary: RunSummary, episodes: Mapping[str, Episode]
) -> tuple[str, list[tuple[str, str, str]]]:
    """The report's header and one section per episode as `(episode_id, situation, proposal)`, in
    report order: threads in seeded random order, episodes of a thread chronologically, later
    episodes as the delta of new messages. The situation part is what the leak check reads; the
    proposal part is the model's output and may legitimately resemble the reference. No model
    label, no variant label, no reference."""
    by_thread: dict[str, list[EpisodeRun]] = {}
    for record in summary.records:
        by_thread.setdefault(record.thread_key, []).append(record)
    header = "\n".join(
        [
            "# Reakcje do przejrzenia",
            "",
            f"Przebieg `{summary.run_id}`, {summary.counts['episodes']} sytuacji w "
            f"{summary.counts['threads']} wątkach, wątki w kolejności losowej.",
            "Nie ma tu etykiety modelu ani wariantu: oceniasz sytuację i propozycję, nic więcej.",
            "Odpowiedzi wzorcowe nie są pokazane. Każda sytuacja ma status oceny "
            f"{HUMAN_REVIEW_PENDING}, dopóki jej nie ocenisz",
            "(pięć pytań z materiałów oceny, formularz pod propozycją).",
            "",
            "**Uwaga:** kiedy jeden wątek ma kilka sytuacji, kolejne pokazują tylko "
            "nowe wiadomości",
            '("ciąg dalszy wątku"). Oceniając późniejszą sytuację znasz już dalszy ciąg wątku,',
            "którego generator przy wcześniejszej sytuacji nie znał. Oceniaj każdą sytuację według",
            "tego, co było widoczne w jej momencie.",
            "",
        ]
    )
    sections: list[tuple[str, str, str]] = []
    number = 0
    for thread_key in summary.report_order:
        group = sorted(by_thread[thread_key], key=lambda r: (r.captured_at, r.episode_id))
        shown: set[str] = set()
        for position, record in enumerate(group, start=1):
            number += 1
            handed = episodes[record.episode_id].input
            lines = [f"## Sytuacja {number}", ""]
            tag = " (przykład syntetyczny, do sprawdzenia mechaniki)" if handed.synthetic else ""
            lines.append(
                f"Community: {handed.community.display_name} ({handed.community.language}){tag}. "
                f"Relacja: {handed.relation.state.value} ({handed.relation.evidence}). "
                f"Moment: {handed.captured_at.isoformat()}. Źródło: {handed.source.url}"
            )
            lines.append("")
            if position > 1:
                lines.append(
                    f"Ciąg dalszy wątku z sytuacji {number - 1}: poniżej tylko wiadomości nowe "
                    "względem poprzedniej sytuacji; wcześniejsze są wyżej."
                )
                lines.append("")
                if handed.target_message_id in shown:
                    lines.append(
                        f"Odpowiadamy na `{handed.target_message_id}` (pokazana w poprzedniej "
                        "sytuacji)."
                    )
                    lines.append("")
            for message in handed.transcript:
                if message.message_id not in shown:
                    lines.extend(_render_message(message, target_id=handed.target_message_id))
            shown.update(m.message_id for m in handed.transcript)
            lines.append("")
            situation_text = "\n".join(lines)
            lines = ["**Propozycja**", ""]
            if not record.generated:
                lines.append(f"Brak propozycji: {record.status}. Powód: {record.error}")
            else:
                kind = ReactionKind(record.decision) if record.decision else None
                label = KIND_LABELS.get(kind, record.decision or "?") if kind else "?"
                lines.append(f"Rodzaj reakcji: **{label}**")
                lines.append("")
                if record.draft:
                    for row in record.draft.splitlines() or [""]:
                        lines.append(f"> {row}")
                else:
                    lines.append("> (bez tekstu)")
                lines.append("")
                lines.append(
                    f"Uzasadnienie (do audytu, nie do publikacji): {record.brief_rationale}"
                )
            lines.append("")
            lines.extend(_rating_form(record))
            sections.append((record.episode_id, situation_text, "\n".join(lines)))
    return header, sections


def _join(header: str, sections: Sequence[tuple[str, str, str]]) -> str:
    return (
        header
        + "\n"
        + "\n".join(f"{situation}\n{proposal}" for _, situation, proposal in sections)
        + "\n"
    )


def render_report(summary: RunSummary, episodes: Mapping[str, Episode]) -> str:
    """The review document: header plus every section of `render_sections`."""
    header, sections = render_sections(summary, episodes)
    return _join(header, sections)


LEAK_WINDOW_CHARS = 40


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _windows(text: str) -> list[str]:
    """Every `LEAK_WINDOW_CHARS`-long window of the normalised text; a shorter text that still
    clears `LEAK_MIN_CHARS` is one window; anything shorter is not searched for (it would match
    ordinary prose)."""
    normalised = _normalise(text)
    if len(normalised) < LEAK_MIN_CHARS:
        return []
    if len(normalised) <= LEAK_WINDOW_CHARS:
        return [normalised]
    return [
        normalised[i : i + LEAK_WINDOW_CHARS]
        for i in range(0, len(normalised) - LEAK_WINDOW_CHARS + 1)
    ]


def _appears(piece: str, haystack: str) -> bool:
    return any(window in haystack for window in _windows(piece))


def reference_leaks(
    header: str, sections: Sequence[tuple[str, str, str]], episodes: Mapping[str, Episode]
) -> list[str]:
    """Which episodes' hidden material the SITUATION part of the report would show. Two hard rules
    (review 2026-09-06), both measured with sliding windows of `LEAK_WINDOW_CHARS` characters over
    whitespace-normalised text, so a partially reproduced reference is a leak too:

    (a) the REFERENCE of any episode (exemplary text, rationale) appears nowhere in the header or
        in any situation (transcripts, relation, source);
    (b) a later message of episode A appears in the situation OF A. In a later sibling's section it
        is that sibling's present, shown as "ciąg dalszy" on purpose, and not a leak.

    The PROPOSAL part is the model's output: it may resemble the reference (that is what a good
    reaction does), so it is reported separately as `proposal_reference_overlap`, never as a leak.
    """
    whole = _normalise(header + "\n" + "\n".join(situation for _, situation, _ in sections))
    own = {episode_id: _normalise(situation) for episode_id, situation, _ in sections}
    leaks: list[str] = []
    for episode_id, episode in episodes.items():
        reference = episode.reference
        if reference is None:
            continue
        if any(_appears(piece, whole) for piece in (reference.rationale, reference.text or "")):
            leaks.append(episode_id)
            continue
        section = own.get(episode_id, "")
        if any(_appears(m.body, section) for m in reference.later_messages):
            leaks.append(episode_id)
    return sorted(leaks)


def proposal_reference_overlap(
    sections: Sequence[tuple[str, str, str]], episodes: Mapping[str, Episode]
) -> list[str]:
    """Episodes whose PROPOSAL shares a `LEAK_WINDOW_CHARS` window with their own reference text.
    Not a leak (the model never saw the reference) but a signal worth a look: a verbatim overlap
    means either an unusually good match or a reference copied from something the model also
    quotes."""
    overlaps: list[str] = []
    for episode_id, _, proposal in sections:
        reference = episodes[episode_id].reference
        if reference is None or not reference.text:
            continue
        if _appears(reference.text, _normalise(proposal)):
            overlaps.append(episode_id)
    return sorted(overlaps)


def write_run(summary: RunSummary, episodes: Mapping[str, Episode], out_dir: Path) -> Path:
    header, sections = render_sections(summary, episodes)
    leaks = reference_leaks(header, sections, episodes)
    if leaks:
        raise ReportLeak(
            f"raport zawiera materiał referencyjny epizodów {leaks}; nic nie zapisano. Reguły: "
            "tekst referencji nigdzie w części sytuacyjnej raportu; późniejsza wiadomość epizodu "
            "nie w jego własnej sekcji. Zwykła przyczyna: referencja jest kopią cudzego "
            "późniejszego komentarza"
        )
    report = _join(header, sections)
    payload = summary.to_json()
    payload["proposal_reference_overlap"] = proposal_reference_overlap(sections, episodes)
    run_dir = out_dir / summary.run_id
    (run_dir / "episodes").mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for record in summary.records:
        (run_dir / "episodes" / f"{record.episode_id}.json").write_text(
            json.dumps(asdict(record), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    return run_dir


async def run_episodes(
    episodes: Sequence[Episode],
    *,
    port: ModelTurnPort,
    runtime: str,
    run_id: str,
    personas_dir: Path = DEFAULT_PERSONAS_DIR,
    now: datetime | None = None,
    db_dsn: str | None = None,
    loader_warnings: Sequence[str] = (),
) -> RunSummary:
    moment = now or datetime.now(UTC)
    records: list[EpisodeRun] = []
    for episode in episodes:
        persona = await resolve_persona(
            episode.input.persona, personas_dir=personas_dir, db_dsn=db_dsn
        )
        records.append(await run_one(episode, port=port, persona=persona, now=moment))
    return summarize(
        records, run_id=run_id, runtime=runtime, now=moment, loader_warnings=loader_warnings
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="wenext conversation evals (R1)")
    parser.add_argument("--runtime", choices=["recorded", "codex"], default="recorded")
    parser.add_argument("--episodes", type=Path, default=DEFAULT_EPISODES_DIR)
    parser.add_argument(
        "--references",
        type=Path,
        default=DEFAULT_REFERENCES_DIR,
        help="katalog referencji trzymanych osobno; referencje do epizodów spoza --episodes "
        "są ostrzeżeniem w run.json, błędem przy --strict",
    )
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--recorded", type=Path, default=DEFAULT_RECORDED_DIR)
    parser.add_argument("--personas", type=Path, default=DEFAULT_PERSONAS_DIR)
    parser.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    parser.add_argument("--include-synthetic", action="store_true")
    parser.add_argument("--only-synthetic", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--codex-home", type=Path, default=None)
    parser.add_argument(
        "--persona-db",
        action="store_true",
        help=f"rozwiąż persony bez pliku z bazy (zmienna {DSN_VARIABLE}); do przebiegów w api",
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="kod wyjścia 0 także wtedy, gdy nie każdy epizod ma wygenerowaną reakcję",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    splits = {Split.DEV, Split.HOLDOUT} if args.split == "all" else {Split(args.split.upper())}
    loaded = load_episode_set(args.episodes, args.references, strict=args.strict)
    episodes = select_episodes(
        loaded.episodes,
        splits=splits,
        include_synthetic=args.include_synthetic,
        only_synthetic=args.only_synthetic,
    )
    if not episodes:
        print("brak epizodów do przebiegu (sprawdź --split / --include-synthetic)", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    run_id = args.run_id or f"{now.strftime('%Y%m%dT%H%M%SZ')}-{args.runtime}"
    if args.runtime == "codex":
        if args.codex_home is None:
            print("--runtime codex wymaga --codex-home (katalog z auth.json)", file=sys.stderr)
            return 2
        port: ModelTurnPort = CodexTurnRunner(codex_home=args.codex_home)
    else:
        port = RecordedPort.from_dir(args.recorded)
    db_dsn = None
    if args.persona_db:
        import os

        db_dsn = os.environ.get(DSN_VARIABLE)
        if not db_dsn:
            print(f"--persona-db wymaga zmiennej {DSN_VARIABLE}", file=sys.stderr)
            return 2
    summary = asyncio.run(
        run_episodes(
            episodes,
            port=port,
            runtime=args.runtime,
            run_id=run_id,
            personas_dir=args.personas,
            now=now,
            db_dsn=db_dsn,
            loader_warnings=loaded.warnings,
        )
    )
    try:
        run_dir = write_run(summary, {e.input.id: e for e in episodes}, args.out)
    except ReportLeak as exc:
        print(f"raport niezapisany: {exc}", file=sys.stderr)
        return 3
    counts = summary.counts
    print(
        f"przebieg {run_id}: {counts['episodes']} epizodów, GENERATED {counts['generated']}, "
        f"UNKNOWN {counts['unknown']} {counts['unknown_reasons']}, "
        f"rodzaj zgodny z rubryką {counts['kind_acceptable']}, "
        f"{HUMAN_REVIEW_PENDING} {counts[HUMAN_REVIEW_PENDING]}, "
        f"ostrzeżenia loadera {len(loaded.warnings)}, katalog {run_dir}"
    )
    if not summary.complete and not args.allow_incomplete:
        print(
            f"przebieg niekompletny: {counts['unknown']} z {counts['episodes']} epizodów bez "
            "reakcji (patrz unknown_reasons). Kod wyjścia 1; --allow-incomplete, jeśli to "
            "zamierzone",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
