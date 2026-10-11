"""The conversation eval runner on the recorded port (R1).

Measured: the three demonstration episodes → three generated reactions inside the reviewers'
rubric, every episode `HUMAN_REVIEW_PENDING`, counters with an UNKNOWN bucket each, the run
directory (run.json, one file per episode, report.md); a real episode whose persona has no file
and no database is `PERSONA_UNRESOLVED` and never `GENERATED`; a recording that breaks the
generator's contract is `REJECTED`, a missing one `FAILED`, and neither stops the run; a run with
any UNKNOWN exits 1 unless `--allow-incomplete`; the report groups a thread's episodes
chronologically with a delta of new messages and never contains reference text or a later
message, which `write_run` also enforces before writing; the model input built from any episode on
disk carries no episode id, no message id, no reaction-kind name and no synthetic marker.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from community_os_contracts import ReactionKind
from control_plane.observe.reactions import reaction_input, situation_from_mapping

from evals.conversations.runner import (
    DEFAULT_RECORDED_DIR,
    HUMAN_REVIEW_PENDING,
    STATUS_FAILED,
    STATUS_GENERATED,
    STATUS_PERSONA_UNRESOLVED,
    STATUS_REJECTED,
    UNKNOWN,
    RecordedPort,
    ReportLeak,
    main,
    proposal_reference_overlap,
    reference_leaks,
    render_report,
    render_sections,
    run_episodes,
    select_episodes,
    write_run,
)
from evals.conversations.schema import (
    DEFAULT_EPISODES_DIR,
    Episode,
    Split,
    load_episode_set,
    load_episodes,
)

NOW = datetime(2026, 9, 6, 14, 0, tzinfo=UTC)
#: The three mechanics demonstrations of this session; a0's real episodes live next door.
DEMO_DIR = DEFAULT_EPISODES_DIR / "synthetic"
PROFILE = {
    "knows_deeply": "queues",
    "positions": "measure first",
    "would_never_claim": "k8s",
    "voice_samples": "short",
}


def demo_episodes():
    return select_episodes(
        load_episodes(DEMO_DIR, references_dir=None),
        splits={Split.DEV},
        include_synthetic=True,
        only_synthetic=True,
    )


def run(episodes, port, run_id="test-run", **kwargs):
    return asyncio.run(
        run_episodes(episodes, port=port, runtime="recorded", run_id=run_id, now=NOW, **kwargs)
    )


def message(mid: str, minutes_before: int, *, parent: str | None = None, own: bool = False) -> dict:
    return {
        "message_id": mid,
        "parent_id": parent,
        "author": "acct" if own else f"user_{mid}",
        "body": f"the text of message {mid} in this thread, long enough to be searched for",
        "created_at": (NOW - timedelta(minutes=minutes_before)).isoformat(),
        "own": own,
    }


def real_episode(
    episode_id: str, *, external_id: str = "777", captured_minutes_before: int, transcript, target
) -> Episode:
    return Episode.model_validate(
        {
            "input": {
                "id": episode_id,
                "source": {
                    "platform": "HACKER_NEWS",
                    "external_id": external_id,
                    "url": f"https://news.ycombinator.com/item?id={external_id}",
                },
                "captured_at": (NOW - timedelta(minutes=captured_minutes_before)).isoformat(),
                "community": {"display_name": "HN Ask", "key": "ask", "language": "en"},
                "transcript": transcript,
                "target_message_id": target,
                "persona": {"label": "operator", "profile_version": 2},
                "relation": {"state": "NEW_ENTRY", "evidence": "no own messages"},
                "allowed_reactions": ["HELP", "SILENCE"],
                "split": "DEV",
                "synthetic": False,
            },
            "reference": None,
        }
    )


# -- the demonstration set --------------------------------------------------------------------


def test_three_demo_episodes_produce_three_reviewable_reactions(tmp_path: Path) -> None:
    episodes = demo_episodes()
    assert len(episodes) == 3
    port = RecordedPort.from_dir(DEFAULT_RECORDED_DIR)

    summary = run(episodes, port)

    assert port.calls == [e.input.id for e in episodes], "każdy epizod woła port raz, po kolei"
    counts = summary.counts
    assert counts["episodes"] == 3 and counts["generated"] == 3 and counts["unknown"] == 0
    assert counts["by_status"] == {STATUS_GENERATED: 3}
    assert counts["by_decision"] == {"DIAGNOSE": 1, "SHORT_ACK": 1, "WAIT": 1, UNKNOWN: 0}
    assert counts["kind_acceptable"] == {"yes": 3, "no": 0, UNKNOWN: 0}
    assert counts["with_draft"] == {"yes": 2, "no": 1, UNKNOWN: 0}, "WAIT nie ma tekstu"
    assert counts[HUMAN_REVIEW_PENDING] == 3, "nikt jeszcze nie ocenił żadnej reakcji"
    assert counts["synthetic"] == 3 and counts["real"] == 0 and counts["threads"] == 3
    assert counts["reference_present"] == 3 and counts["reference_reviewed_by_operator"] == 0
    assert counts["models"] == ["recorded"]
    assert summary.complete and summary.prompt_name == "reaction_baseline"
    assert len(summary.prompt_sha256) == 64
    assert all(r.human_review == HUMAN_REVIEW_PENDING for r in summary.records)
    assert sorted(summary.report_order) == sorted({r.thread_key for r in summary.records})
    # The recorded target ids are real ids; the generator mapped them back from neutral ones.
    assert {r.target_message_id for r in summary.records} == {"m3", "m1"}

    run_dir = write_run(summary, {e.input.id: e for e in episodes}, tmp_path)
    assert (run_dir / "run.json").is_file() and (run_dir / "report.md").is_file()
    assert sorted(p.stem for p in (run_dir / "episodes").glob("*.json")) == sorted(
        e.input.id for e in episodes
    )
    written = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert written["human_review"] == HUMAN_REVIEW_PENDING
    assert written["counts"][HUMAN_REVIEW_PENDING] == 3 and written["complete"] is True
    assert written["report_order_threads"] == list(summary.report_order)
    assert written["loader_warnings"] == []
    # The demo recordings echo their references on purpose: reported as overlap, not as a leak.
    assert set(written["proposal_reference_overlap"]) <= {e.input.id for e in episodes}


def test_the_report_shows_situations_and_proposals_but_no_reference_and_no_model() -> None:
    episodes = demo_episodes()
    summary = run(episodes, RecordedPort.from_dir(DEFAULT_RECORDED_DIR), run_id="report-run")
    report = render_report(summary, {e.input.id: e for e in episodes})

    assert report.count("## Sytuacja ") == 3
    assert "odpowiadamy na to" in report and "Rodzaj reakcji" in report
    assert "BRPOP takes the job out of the list" in report, "propozycja jest w raporcie"
    assert report.count(f"Status oceny: {HUMAN_REVIEW_PENDING}") == 3
    for episode in episodes:
        assert episode.reference is not None
        assert episode.reference.rationale not in report, "wyjaśnienie oceniającego ukryte"
        if episode.reference.text:
            assert episode.reference.text not in report, "odpowiedź wzorcowa ukryta"
        for later in episode.reference.later_messages:
            assert later.body not in report, "przyszłość ukryta"
    header, sections = render_sections(summary, {e.input.id: e for e in episodes})
    assert reference_leaks(header, sections, {e.input.id: e for e in episodes}) == []
    overlap = proposal_reference_overlap(sections, {e.input.id: e for e in episodes})
    assert overlap, "nagrania demo celowo brzmią jak referencje: sygnał, nie wyciek"
    assert "recorded" not in report and "fake-" not in report, "bez etykiety modelu/wariantu"
    assert "Ocena (wypełnia operator)" in report


# -- what the model sees ------------------------------------------------------------------------


def test_the_model_input_of_every_episode_on_disk_carries_no_label() -> None:
    """Review 2026-09-06: an episode id such as `…-post-silence` in the prompt is the answer key.
    For every episode on disk the input built for the model has no episode id, no real message
    id, no name of a reaction kind and no synthetic marker."""
    episodes = load_episodes(DEFAULT_EPISODES_DIR)
    assert episodes
    kinds = re.compile(r"\b(" + "|".join(k.value for k in ReactionKind) + r")\b")
    marker = re.compile(r"\bsyn(thetic|-)", re.IGNORECASE)
    for episode in episodes:
        handed = episode.for_generator()
        situation = situation_from_mapping(handed.model_dump(mode="json"))
        payload = reaction_input(situation, profile=PROFILE, now=NOW)
        text = json.dumps(payload["situation"], ensure_ascii=False)
        assert "id" not in payload["situation"], handed.id
        assert handed.id not in text, f"{handed.id}: id epizodu w wejściu modelu"
        for m in handed.transcript:
            if not re.fullmatch(r"m\d+", m.message_id):
                assert m.message_id not in text, f"{handed.id}: prawdziwy id {m.message_id}"
        assert not kinds.search(text), f"{handed.id}: nazwa rodzaju reakcji w wejściu"
        assert not marker.search(text), f"{handed.id}: znacznik syntetyczności w wejściu"
        assert all(
            re.fullmatch(r"m\d+", m["message_id"]) for m in payload["situation"]["transcript"]
        )


# -- UNKNOWN in every counter ------------------------------------------------------------------


def test_a_real_episode_without_a_persona_is_persona_unresolved_never_generated() -> None:
    """The founder's profile is not a file in the repo; offline a real episode has no persona and
    therefore no reaction, even if a recording for it existed."""
    episode = real_episode(
        "hn-777-help", captured_minutes_before=10, transcript=[message("m1", 60)], target="m1"
    )
    recordings = {
        "hn-777-help": {
            "decision": "HELP",
            "draft": "a recorded answer that must never be used without a persona",
            "target_message_id": "m1",
            "contribution": "a contribution long enough for the contract",
            "brief_rationale": "would be a measurement of nothing",
            "sources_used": [],
            "confidence": 0.9,
        }
    }
    port = RecordedPort(recordings)
    summary = run((episode,), port)
    (record,) = summary.records
    assert record.status == STATUS_PERSONA_UNRESOLVED and not record.generated
    assert "operator.v2" in record.error and "--persona-db" in record.error
    assert port.calls == [], "bez persony port nie jest wołany"
    assert summary.counts["unknown"] == 1 and summary.counts["generated"] == 0
    assert summary.counts["unknown_reasons"] == {STATUS_PERSONA_UNRESOLVED: 1}
    assert summary.counts["by_decision"] == {UNKNOWN: 1}
    assert summary.counts["kind_acceptable"] == {"yes": 0, "no": 0, UNKNOWN: 1}
    assert summary.counts[HUMAN_REVIEW_PENDING] == 1 and not summary.complete


def test_no_real_episode_on_disk_is_generated_offline() -> None:
    """Every real episode points at the founder's persona, which offline has no source."""
    real = [e for e in load_episodes(DEFAULT_EPISODES_DIR) if not e.input.synthetic]
    if not real:
        pytest.skip("brak epizodów rzeczywistych na dysku")
    summary = run(tuple(real), RecordedPort.from_dir(DEFAULT_RECORDED_DIR), run_id="real-off")
    assert summary.counts["generated"] == 0
    assert set(summary.counts["by_status"]) == {STATUS_PERSONA_UNRESOLVED}


def test_a_contract_breaking_recording_is_rejected_and_a_missing_one_fails_the_episode() -> None:
    episodes = demo_episodes()
    ids = [e.input.id for e in episodes]
    bad = {
        ids[0]: {
            "decision": "WAIT",
            "draft": "waiting but writing anyway",
            "target_message_id": "m3",
            "contribution": None,
            "brief_rationale": "this recording breaks the contract on purpose",
            "sources_used": [],
            "confidence": 0.5,
        },
        ids[1]: {
            "decision": "HUMOR",
            "draft": "lol queues",
            "target_message_id": "m1",
            "contribution": None,
            "brief_rationale": "a joke as the first words in a stranger's thread",
            "sources_used": [],
            "confidence": 0.9,
        },
        # ids[2] has no recording at all.
    }
    summary = run(episodes, RecordedPort(bad), run_id="bad-run")
    by_id = {r.episode_id: r for r in summary.records}
    assert by_id[ids[0]].status == STATUS_REJECTED and "musi być null" in by_id[ids[0]].error
    assert by_id[ids[1]].status == STATUS_REJECTED and "nowym wejściu" in by_id[ids[1]].error
    assert by_id[ids[2]].status == STATUS_FAILED and "brak nagrania" in by_id[ids[2]].error
    assert summary.counts["by_status"] == {STATUS_FAILED: 1, STATUS_REJECTED: 2}
    assert summary.counts["unknown"] == 3 and summary.counts["generated"] == 0
    assert summary.counts["kind_acceptable"] == {"yes": 0, "no": 0, UNKNOWN: 3}
    assert summary.counts[HUMAN_REVIEW_PENDING] == 3, "odrzucone też czekają na człowieka"
    report = render_report(summary, {e.input.id: e for e in episodes})
    assert report.count("Brak propozycji") == 3


# -- one thread, several moments -------------------------------------------------------------


def test_episodes_of_one_thread_are_reported_chronologically_as_deltas() -> None:
    first = real_episode(
        "hn-777-first",
        captured_minutes_before=50,
        transcript=[message("m1", 90), message("m2", 60, parent="m1")],
        target="m1",
    )
    later = real_episode(
        "hn-777-later",
        captured_minutes_before=5,
        transcript=[
            message("m1", 90),
            message("m2", 60, parent="m1"),
            message("m3", 20, parent="m2"),
        ],
        target="m3",
    )
    summary = run((later, first), RecordedPort({}), run_id="thread-run")
    assert summary.counts["threads"] == 1
    report = render_report(summary, {e.input.id: e for e in (first, later)})

    first_section = report.index("## Sytuacja 1")
    second_section = report.index("## Sytuacja 2")
    assert report.index("`m1` user_m1") < second_section, "starsza sytuacja pierwsza"
    body_m3 = "the text of message m3"
    assert body_m3 not in report[first_section:second_section], "delta: m3 dopiero w sytuacji 2"
    assert body_m3 in report[second_section:]
    assert report[second_section:].count("the text of message m1") == 0, "m1 nie powtarza się"
    assert "Ciąg dalszy wątku z sytuacji 1" in report[second_section:]
    assert "kolejne pokazują tylko nowe wiadomości" in report, "ostrzeżenie dla oceniającego"


def test_a_later_message_shown_as_a_siblings_present_is_not_a_leak(tmp_path: Path) -> None:
    """Rule (b) narrowed (review 2026-09-06): the future of an earlier moment is the present of a
    later moment of the same thread; the report shows it there as "ciąg dalszy", not as a leak."""
    later_body = "a later message of the first moment that the second moment already sees"
    first = real_episode(
        "hn-778-e1",
        external_id="778",
        captured_minutes_before=50,
        transcript=[message("m1", 90)],
        target="m1",
    )
    first = Episode(
        input=first.input,
        reference={
            "reaction": "SILENCE",
            "text": None,
            "rationale": "rationale of the reviewer, hidden as well",
            "author_of_reference": "MODEL",
            "status": "CANDIDATE",
            "later_messages": [{**message("m9", 5, parent="m1"), "body": later_body}],
        },
    )
    second = real_episode(
        "hn-778-e2",
        external_id="778",
        captured_minutes_before=1,
        transcript=[message("m1", 90), {**message("m9", 5, parent="m1"), "body": later_body}],
        target="m9",
    )
    summary = run((second, first), RecordedPort({}), run_id="sibling-run")
    episodes = {e.input.id: e for e in (first, second)}
    header, sections = render_sections(summary, episodes)
    assert reference_leaks(header, sections, episodes) == []
    own = {episode_id: situation for episode_id, situation, _ in sections}
    assert later_body not in own["hn-778-e1"] and later_body in own["hn-778-e2"]
    assert write_run(summary, episodes, tmp_path).is_dir()


def test_write_run_refuses_a_reference_anywhere_and_a_later_message_in_its_own_section(
    tmp_path: Path,
) -> None:
    reference_text = "this exact sentence is the reference answer and must never be printed"
    # (a) another episode's transcript happens to contain episode A's reference text: the
    # reference is a copy of a real comment, a data error, and the report is not written.
    a = real_episode(
        "hn-779-e1",
        external_id="779",
        captured_minutes_before=50,
        transcript=[message("m1", 90)],
        target="m1",
    )
    a = Episode(
        input=a.input,
        reference={
            "reaction": "HELP",
            "text": reference_text,
            "rationale": "rationale of the reviewer",
            "author_of_reference": "MODEL",
            "status": "CANDIDATE",
        },
    )
    b = real_episode(
        "hn-779-e2",
        external_id="779",
        captured_minutes_before=1,
        transcript=[message("m1", 90), {**message("m5", 3, parent="m1"), "body": reference_text}],
        target="m5",
    )
    summary = run((a, b), RecordedPort({}), run_id="ref-leak")
    episodes = {e.input.id: e for e in (a, b)}
    header, sections = render_sections(summary, episodes)
    assert reference_leaks(header, sections, episodes) == ["hn-779-e1"]
    with pytest.raises(ReportLeak, match="hn-779-e1"):
        write_run(summary, episodes, tmp_path)
    assert not (tmp_path / "ref-leak").exists(), "nic nie zapisano"

    # (b) a later message whose text is already in the episode's own transcript: it would be
    # printed in the episode's own section.
    same_body = "the text of message m1 in this thread, long enough to be searched for"
    c = real_episode(
        "hn-780-e1",
        external_id="780",
        captured_minutes_before=50,
        transcript=[message("m1", 90)],
        target="m1",
    )
    c = Episode(
        input=c.input,
        reference={
            "reaction": "SILENCE",
            "text": None,
            "rationale": "rationale of the reviewer",
            "author_of_reference": "MODEL",
            "status": "CANDIDATE",
            "later_messages": [{**message("m9", 5, parent="m1"), "body": same_body}],
        },
    )
    summary = run((c,), RecordedPort({}), run_id="own-leak")
    header, sections = render_sections(summary, {c.input.id: c})
    assert reference_leaks(header, sections, {c.input.id: c}) == ["hn-780-e1"]


def test_a_partially_reproduced_reference_is_a_leak_too() -> None:
    """Sliding windows of 40 characters (like a0's data check): the first two thirds of a
    reference text quoted inside a transcript is still the answer key showing through."""
    reference_text = (
        "the queue drops jobs because BRPOP removes them before processing and a crash in between "
        "loses them silently, so count pops against completions first"
    )
    a = real_episode(
        "hn-781-e1",
        external_id="781",
        captured_minutes_before=50,
        transcript=[message("m1", 90)],
        target="m1",
    )
    a = Episode(
        input=a.input,
        reference={
            "reaction": "HELP",
            "text": reference_text,
            "rationale": "rationale of the reviewer",
            "author_of_reference": "MODEL",
            "status": "CANDIDATE",
        },
    )
    partial = reference_text[: int(len(reference_text) * 0.66)].upper().lower()
    b = real_episode(
        "hn-781-e2",
        external_id="781",
        captured_minutes_before=1,
        transcript=[
            message("m1", 90),
            {**message("m5", 3, parent="m1"), "body": "so:  " + partial},
        ],
        target="m5",
    )
    summary = run((a, b), RecordedPort({}), run_id="partial-leak")
    episodes = {e.input.id: e for e in (a, b)}
    header, sections = render_sections(summary, episodes)
    assert reference_leaks(header, sections, episodes) == ["hn-781-e1"]
    # A short common phrase is not a leak: ordinary prose overlaps in a few words.
    c = real_episode(
        "hn-782-e1",
        external_id="782",
        captured_minutes_before=1,
        transcript=[
            message("m1", 90),
            {**message("m5", 3, parent="m1"), "body": "count pops first"},
        ],
        target="m5",
    )
    header, sections = render_sections(
        run((a, c), RecordedPort({}), run_id="no-leak"), {a.input.id: a, c.input.id: c}
    )
    assert reference_leaks(header, sections, {a.input.id: a, c.input.id: c}) == []


# -- selection, loader warnings and the CLI ------------------------------------------------------


def test_holdout_is_excluded_by_default_and_synthetic_only_when_asked() -> None:
    episodes = load_episodes(DEFAULT_EPISODES_DIR)
    default = select_episodes(
        episodes, splits={Split.DEV}, include_synthetic=False, only_synthetic=False
    )
    assert all(e.input.split is Split.DEV and not e.input.synthetic for e in default)
    with_synthetic = select_episodes(
        episodes, splits={Split.DEV}, include_synthetic=True, only_synthetic=False
    )
    assert len(with_synthetic) >= len(default) + 3, "syntetyki dochodzą tylko na życzenie"
    assert all(e.input.split is Split.DEV for e in with_synthetic)
    holdout_only = select_episodes(
        episodes, splits={Split.HOLDOUT}, include_synthetic=True, only_synthetic=False
    )
    assert all(e.input.split is Split.HOLDOUT for e in holdout_only)
    assert not ({e.input.id for e in default} & {e.input.id for e in holdout_only}), (
        "podziały są rozłączne"
    )


def test_a_subdirectory_run_turns_unmatched_references_into_warnings_not_errors() -> None:
    loaded = load_episode_set(DEMO_DIR)  # default references dir, none of them for the demos
    assert len(loaded.episodes) == 3
    references_on_disk = len(list((DEFAULT_EPISODES_DIR.parent / "references").rglob("*.json")))
    assert len(loaded.warnings) == references_on_disk
    if references_on_disk:
        with pytest.raises(Exception, match="spoza wczytanego zbioru"):
            load_episode_set(DEMO_DIR, strict=True)


def test_the_cli_writes_a_run_directory_and_reports_counts(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "--runtime",
            "recorded",
            "--episodes",
            str(DEMO_DIR),
            "--only-synthetic",
            "--out",
            str(tmp_path),
            "--run-id",
            "cli-run",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "przebieg cli-run: 3 epizodów, GENERATED 3, UNKNOWN 0" in out
    assert f"{HUMAN_REVIEW_PENDING} 3" in out
    assert (tmp_path / "cli-run" / "report.md").is_file()


def test_the_cli_exits_non_zero_on_an_incomplete_run_unless_allowed(tmp_path: Path, capsys) -> None:
    """A real-shaped episode (persona `operator`, no file, no database) is UNKNOWN offline, so the
    run is incomplete: exit 1 with the reason, or 0 with an explicit `--allow-incomplete`."""
    episodes_dir = tmp_path / "episodes"
    episodes_dir.mkdir()
    episode = real_episode(
        "hn-900-moment",
        external_id="900",
        captured_minutes_before=10,
        transcript=[message("m1", 60)],
        target="m1",
    )
    (episodes_dir / "hn-900-moment.json").write_text(episode.model_dump_json(), encoding="utf-8")
    args = [
        "--runtime",
        "recorded",
        "--episodes",
        str(episodes_dir),
        "--split",
        "dev",
        "--out",
        str(tmp_path / "out"),
        "--run-id",
        "inc",
    ]
    assert main(args) == 1
    err = capsys.readouterr().err
    assert "przebieg niekompletny" in err and "--allow-incomplete" in err
    written = json.loads((tmp_path / "out" / "inc" / "run.json").read_text(encoding="utf-8"))
    assert written["complete"] is False and written["counts"]["generated"] == 0
    assert written["counts"]["unknown_reasons"] == {STATUS_PERSONA_UNRESOLVED: 1}
    assert main([*args[:-1], "inc-ok", "--allow-incomplete"]) == 0


def test_the_cli_refuses_an_empty_selection(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert main(["--runtime", "recorded", "--episodes", str(tmp_path / "empty")]) == 2
