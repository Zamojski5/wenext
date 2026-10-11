"""The morning report of the pilot in Markdown, laid out as `docs/RAPORT-PILOTA-UKLAD.md` says.

Rules the layout insists on and this renderer keeps: an unmeasured value is printed as "brak
odczytu" with its reason, never as zero; a skipped cycle is not a failure; the generator's opinion
(rationale, confidence) is labelled as the generator's and the reviewer's fields stay empty; no model
name next to a proposal (it lives in the technical section); no long dashes in our own text; the
judge's verdicts (D-029) are labelled as the system's and never fill the reviewer's fields.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from community_os_contracts import ReactionKind

from control_plane.observe.context import ThreadContext

from .store import PilotCycle, PilotReaction, PilotRun

KIND_PL: dict[str, str] = {
    ReactionKind.HELP.value: "pomoc",
    ReactionKind.DIAGNOSE.value: "diagnoza",
    ReactionKind.CLARIFY.value: "dopytanie",
    ReactionKind.DISAGREE.value: "niezgoda",
    ReactionKind.SHORT_ACK.value: "krótkie potwierdzenie",
    ReactionKind.HUMOR.value: "żart",
    ReactionKind.WAIT.value: "poczekać",
    ReactionKind.SILENCE.value: "milczenie",
}

#: The move a draft is built around (prompt v3), in the founder's language.
MOVE_PL: dict[str, str] = {
    "CLAIM_WITH_LIMIT": "teza z granicą",
    "COUNTERPOINT": "kontrpogląd",
    "FIRST_HAND": "z pierwszej ręki",
    "IMAGE": "obraz",
    "NUMBER": "konkretna liczba",
}

OK_OUTCOMES = frozenset({"GENERATED"})
SKIP_OUTCOMES = frozenset({"NO_CANDIDATE", "PACING", "DUPLICATE"})
BUDGET_OUTCOMES = frozenset({"BUDGET_EXHAUSTED"})
FAILED_OUTCOMES = frozenset({"FAILED", "MODEL_UNAVAILABLE", "PLATFORM_UNAVAILABLE", "REJECTED"})
MAX_PROPOSALS = 10
CONTEXT_MESSAGES = 3
CONTEXT_CHARS = 200
#: Two drafts sharing a run of this many words are reported as a repeated phrase.
REPEAT_NGRAM = 6


def _one_line(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _t(moment: datetime | None) -> str:
    return moment.isoformat(timespec="minutes") if moment else "brak odczytu"


def pick_proposals(
    reactions: Sequence[PilotReaction], limit: int = MAX_PROPOSALS
) -> list[PilotReaction]:
    """Up to `limit`, as different as possible: by `created_at`, first those bringing a new
    decision kind or a new thread, then the rest."""
    ordered = sorted(reactions, key=lambda r: r.created_at)
    seen_kinds: set[str] = set()
    seen_threads: set = set()
    first: list[PilotReaction] = []
    rest: list[PilotReaction] = []
    for r in ordered:
        if r.decision not in seen_kinds or r.thread_id not in seen_threads:
            first.append(r)
        else:
            rest.append(r)
        seen_kinds.add(r.decision)
        seen_threads.add(r.thread_id)
    return (first + rest)[:limit]


def repeated_phrases(reactions: Sequence[PilotReaction]) -> list[str]:
    """Word 6-grams shared by two different drafts (lowercased, whitespace-normalised)."""
    grams: dict[str, set[int]] = {}
    for index, r in enumerate(reactions):
        if not r.draft:
            continue
        words = " ".join(r.draft.split()).lower().split(" ")
        for i in range(0, max(0, len(words) - REPEAT_NGRAM + 1)):
            gram = " ".join(words[i : i + REPEAT_NGRAM])
            grams.setdefault(gram, set()).add(index)
    return sorted(g for g, owners in grams.items() if len(owners) >= 2)


def gaps(
    run: PilotRun, cycles: Sequence[PilotCycle], *, now: datetime
) -> list[tuple[str, str, str]]:
    """(from, to, reason) from the manifest's host_events plus cycles far later than planned."""
    out: list[tuple[str, str, str]] = []
    for event in run.host_events:
        out.append(
            (str(event.get("at", "?")), "", f"{event.get('kind', '?')}: {event.get('detail', '')}")
        )
    for cycle in cycles:
        late = cycle.started_at - cycle.planned_at
        if late > timedelta(minutes=45):
            out.append(
                (
                    _t(cycle.planned_at),
                    _t(cycle.started_at),
                    f"cykl {cycle.slot} ruszył {int(late.total_seconds() // 60)} min po planie",
                )
            )
    return out


def _context_block(reaction: PilotReaction, context: ThreadContext | None) -> list[str]:
    if context is None:
        return ["_Kontekstu nie zapisano._"]
    lines = [
        f"> **{context.author or '[usunięty]'}**, {_t(context.created_at)}: "
        f"{_one_line(context.title + ('. ' + context.body if context.body else ''), CONTEXT_CHARS)}"
    ]
    before = [c for c in context.comments if c.created_at <= reaction.read_at]
    for c in before[-CONTEXT_MESSAGES:]:
        lines.append(
            f"> **{c.author or '[usunięty]'}**, {_t(c.created_at)}: "
            f"{_one_line(c.body or '[usunięty]', CONTEXT_CHARS)}"
        )
    return lines


def _proposal_card(number: int, r: PilotReaction, context: ThreadContext | None) -> list[str]:
    lines = [f"### {number}. {r.thread_title}", ""]
    lines.append(f"- Wątek: {r.thread_url}")
    lines.append(f"- Moment odczytu: {_t(r.read_at)}")
    author = (
        context.author
        if context is not None and r.target_message_external_id == context.external_id
        else None
    )
    if author is None and context is not None:
        for c in context.comments:
            if c.external_id == r.target_message_external_id:
                author = c.author
    lines.append(
        f"- Odpowiadamy na wiadomość `{r.target_message_external_id}` od {author or 'brak odczytu'}"
    )
    if not r.context_complete:
        lines.append(
            f"- Odczyt niepełny: przycięty do limitu wiadomości albo drzewo komentarzy "
            f"obcięte przez platformę (widziano {r.comments_seen} komentarzy)"
        )
    lines += [
        "",
        "**Co było widać**",
        "",
        *_context_block(r, context),
        "",
        "**Propozycja systemu**",
        "",
    ]
    kind = KIND_PL.get(r.decision, r.decision)
    if r.decision == ReactionKind.SILENCE.value:
        lines.append(f"Rodzaj: **{kind}**, bez treści.")
    elif r.decision == ReactionKind.WAIT.value:
        lines.append(f"Rodzaj: **{kind}** do {_t(r.next_check_at)}.")
        lines.append("")
        lines.append(f"Powód: {r.wait_reason or 'brak odczytu'}")
    else:
        lines.append(f"Rodzaj: **{kind}**")
        if r.move and r.move != "NONE":
            lines.append(f"Ruch: {MOVE_PL.get(r.move, r.move)}")
        lines.append("")
        for row in (r.draft or "").splitlines() or [""]:
            lines.append(f"> {row}")
    lines.append("")
    if r.contribution:
        lines.append(f"Co wnosi, zdaniem generatora: {_one_line(r.contribution, 300)}")
    if r.brief_rationale and r.decision != ReactionKind.WAIT.value:
        lines.append(f"Uzasadnienie generatora: {_one_line(r.brief_rationale, 400)}")
    lines.append(f"Pewność podana przez generator: {r.confidence:.2f}")
    lines += [
        "",
        "**Twoja ocena**",
        "",
        "| Pytanie | Twoja odpowiedź |",
        "| --- | --- |",
        "| Czy ten rodzaj reakcji pasuje do sytuacji? (tak / nie / nie umiem ocenić) | |",
        "| Czy ja bym to napisał lub tak przemilczał? "
        "(tak / drobna zmiana / przepisanie / nie) | |",
        "| Czy treść wnosi to, czego potrzebuje rozmowa? (tak / nie / właściwe jest milczenie) | |",
        "| Czy przypisano mi nieprawdziwy fakt lub stanowisko? "
        "(nie / tak, jakie / do sprawdzenia) | |",
        "| Co trzeba zmienić? | |",
        "",
    ]
    return lines


def render_report(
    run: PilotRun,
    cycles: Sequence[PilotCycle],
    reactions: Sequence[PilotReaction],
    *,
    now: datetime,
    contexts: Mapping | None = None,
    community_name: str | None = None,
    publications: Sequence[Mapping] | None = None,
    judgments: Sequence[Mapping] | None = None,
) -> str:
    contexts = contexts or {}
    publications = list(publications or [])
    if run.stopped_at:
        ended = _t(run.stopped_at)
    elif now >= run.ends_at:
        ended = "okno zamknięte, brak zapisu zatrzymania"
    else:
        ended = "trwa"
    lines = [
        f"# Pilot {run.id}, raport poranny",
        "",
        f"Okno: od {_t(run.starts_at)} do {_t(run.ends_at)}, strefa w zapisie. "
        f"Zakończony: {ended}.",
        f"Społeczność: {community_name or 'brak odczytu'}. Odpytywanie co "
        f"{run.poll_interval_seconds} sekund.",
        "",
        f"Budżet wywołań modelu: {run.model_calls_used} z {run.model_calls_budget}.",
        "",
        "Propozycje poniżej napisał system. Twoje pola są puste i zostają puste do Twojej decyzji.",
        "Brak reakcji jest pełnoprawnym wynikiem, nie awarią: milczenie i "
        "czekanie liczą się tak samo",
        "jak szkic.",
        "",
    ]
    chosen = pick_proposals(reactions)
    lines.append(f"## Propozycje do oceny, {len(chosen)} z {len(reactions)} zapisanych")
    lines.append("")
    if not chosen:
        lines += [
            "Żadnej propozycji nie zapisano w tym oknie. To nie to samo, co brak "
            "okazji: sprawdź liczniki",
            "i przerwy poniżej.",
            "",
        ]
    for number, r in enumerate(chosen, start=1):
        lines += _proposal_card(number, r, contexts.get(r.thread_id))

    outcomes = Counter(c.outcome for c in cycles if c.finished_at is not None)
    ok = sum(n for o, n in outcomes.items() if o in OK_OUTCOMES)
    skipped = {o: outcomes.get(o, 0) for o in ("NO_CANDIDATE", "PACING", "DUPLICATE")}
    budget = sum(n for o, n in outcomes.items() if o in BUDGET_OUTCOMES)
    failed = sum(n for o, n in outcomes.items() if o in FAILED_OUTCOMES)
    drafts = sum(1 for r in reactions if r.draft)
    waits = sum(1 for r in reactions if r.decision == ReactionKind.WAIT.value)
    silences = sum(1 for r in reactions if r.decision == ReactionKind.SILENCE.value)
    repeats = repeated_phrases(reactions)
    errors = [c for c in cycles if c.outcome in FAILED_OUTCOMES]
    lines += [
        "## Liczniki",
        "",
        "| Co | Ile |",
        "| --- | --- |",
        f"| cykle udane | {ok} |",
        f"| cykle bez okazji | {sum(skipped.values())} |",
        f"| cykle zatrzymane limitem | {budget} |",
        f"| cykle nieudane | {failed} |",
        f"| unikalne rozmowy | {len({r.thread_id for r in reactions})} |",
        f"| szkice | {drafts} |",
        f"| decyzje poczekać | {waits} |",
        f"| decyzje milczenie | {silences} |",
        f"| wykryte powtórzenia fraz | {len(repeats)} |",
        f"| błędy | {len(errors)} |",
        "| tokeny i koszt | brak odczytu (runtime nie podaje zużycia) |",
        "",
        f"Cykle bez okazji z podziałem: brak kandydata {skipped['NO_CANDIDATE']}, "
        f"wstrzymane tempem "
        f"{skipped['PACING']}, duplikat sytuacji {skipped['DUPLICATE']}.",
        "",
    ]
    if repeats:
        lines.append("Powtórzone frazy (6 słów wspólnych dla dwu szkiców):")
        lines += [f"- {g}" for g in repeats[:10]]
        lines.append("")
    if errors:
        lines.append("Błędy cykli:")
        lines += [
            f"- slot {c.slot}, {_t(c.started_at)}: {c.outcome}: {c.detail or 'brak odczytu'}"
            for c in errors
        ]
        lines.append("")
    lines += ["## Przerwy w pracy", ""]
    breaks = gaps(run, cycles, now=now)
    if not cycles and not run.host_events:
        lines.append("Przerwy: brak odczytu.")
    elif not breaks:
        lines.append("Przerw nie zapisano.")
    else:
        lines += [f"- od {a}{(' do ' + b) if b else ''}: {reason}" for a, b, reason in breaks]
    lines += ["", "## Opublikowane", ""]
    if not publications:
        lines.append("Nic nie zostało opublikowane z tego runu.")
    else:
        lines.append("| Komentarz | Opublikowano | Nadal widoczny | Punkty | Odpowiedzi |")
        lines.append("| --- | --- | --- | --- | --- |")
        for p in publications:
            present = p.get("still_present")
            visible = "brak odczytu" if present is None else ("tak" if present else "NIE, zniknął")
            score = p.get("score")
            lines.append(
                f"| {p.get('permalink', 'brak odczytu')} | {_t(p.get('published_at'))} | {visible} "
                f"| {'brak odczytu' if score is None else score} | {len(p.get('replies') or [])} |"
            )
        lines.append("")
        lines.append(
            "Punkty i widoczność z ostatniego odczytu kontrolnego (co godzinę), nie z chwili "
            "publikacji. Głosów własnych nie oddajemy."
        )
    lines += ["", "## Odpowiedzi ludzi", ""]
    replies = [(p, r) for p in publications for r in (p.get("replies") or [])]
    if not publications:
        lines.append("Brak odczytu: nic nie opublikowano.")
    elif not replies:
        lines.append("Pod naszymi komentarzami nikt jeszcze nie odpowiedział.")
    else:
        for p, r in replies:
            lines.append(
                f"- pod {p.get('permalink')}: **{r.get('author') or '[usunięty]'}**, "
                f"{_t(r.get('created_at_source'))}: {_one_line(r.get('body') or '[usunięty]', 300)}"
            )
    lines += ["", "## Ocena systemu (sędzia szkiców, D-029)", ""]
    judged = list(judgments or [])
    if not judged:
        lines.append(
            "Sędzia nie oceniał jeszcze żadnego szkicu z tego runu (brak podpisanej polityki albo "
            "brak kandydatów)."
        )
    else:
        titles = {r.id: r.thread_title or r.thread_external_id for r in reactions}
        lines.append("| Wątek | Werdykt | Uzasadnienie |")
        lines.append("| --- | --- | --- |")
        for j in judged:
            lines.append(
                f"| {_one_line(titles.get(j.get('reaction_id'), '?'), 80)} | {j.get('verdict')} | "
                f"{_one_line(j.get('rationale') or '', 300)} |"
            )
        lines.append("")
        lines.append(
            "PASS trafia do kolejki publikacji; REJECT i POLICY_REJECT zostają do Twojej decyzji."
        )
    lines += ["", "## Wyniki po 24 h", ""]
    matured = [
        p
        for p in publications
        if p.get("observed_at") is not None
        and p.get("published_at") is not None
        and p["observed_at"] - p["published_at"] >= timedelta(hours=20)
    ]
    if not matured:
        lines.append("Żaden komentarz nie ma jeszcze doby z odczytem kontrolnym.")
    else:
        lines.append("| Komentarz | Po publikacji | Punkty | Odpowiedzi | Widoczny |")
        lines.append("| --- | --- | --- | --- | --- |")
        for p in matured:
            hours = int((p["observed_at"] - p["published_at"]).total_seconds() // 3600)
            lines.append(
                f"| {p.get('permalink')} | {hours} h | "
                f"{'brak odczytu' if p.get('score') is None else p.get('score')} | "
                f"{len(p.get('replies') or [])} | {'tak' if p.get('still_present') else 'NIE'} |"
            )
    lines += [
        "",
        "## Czego ten raport nie mówi",
        "",
        "- Czy propozycje są dobre. Sędzia mówi tylko, czy wolno je opublikować; czy były dobre, "
        "mówią wyniki po 24 h i odpowiedzi ludzi.",
        "- Jak zareagowaliby ludzie na szkice, których nie opublikowano.",
        "- Nic o kontach poza wybraną społecznością.",
        "",
        "## Zapis techniczny",
        "",
        f"Wersja profilu: {run.profile_version_id}. Prompt: {run.prompt_name}, hash "
        f"{run.prompt_sha256[:12]}.",
        f"Model: {', '.join(sorted({r.model for r in reactions})) or 'brak odczytu'}. Wstrzymane "
        f"rodzaje zadań na czas pilota: {', '.join(run.paused_job_types) or 'żadne'}.",
        f"Konfiguracja do przywrócenia: {run.restore}.",
        "",
    ]
    return "\n".join(lines)
