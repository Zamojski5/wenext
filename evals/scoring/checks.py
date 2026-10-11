"""Eval scoring. Assertions on the STRUCTURE of the result — with no LLM as a judge.

Every function returns a list of `CheckResult`. None of them computes semantic similarity,
asks the model for an assessment or returns a number that could be called "quality".
Thanks to that the eval result is repeatable bit for bit and readable in a diff.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence

from community_os_contracts import (
    NEGATIVE_STOP_CLASSES,
    PriorityTier,
    SignalClass,
    ThreadQualityAssessment,
    WindowState,
)

from ..harness.types import (
    CheckResult,
    DiscoveryResult,
    DraftBrief,
    DraftProposal,
    ThreadObservation,
)
from .gate_metrics import MATERIAL_EDIT_THRESHOLD, normalized_edit_distance
from .ranking import signal_class_rank, strictly_precedes_by_class

__all__ = [
    "check_discovery_result",
    "check_draft",
    "check_quality_assessment",
    "check_signal_pair",
]


def _ok(name: str, condition: bool, detail_ok: str, detail_failed: str) -> CheckResult:
    return CheckResult(
        name=name, passed=condition, detail=detail_ok if condition else detail_failed
    )


# ---------------------------------------------------------------------------
# Family: discussion quality classification
# ---------------------------------------------------------------------------


def check_quality_assessment(
    obs: ThreadObservation,
    assessment: ThreadQualityAssessment,
    *,
    expected_tier: PriorityTier,
    expected_unknown_fields: Sequence[str],
    assessment_without_flags: ThreadQualityAssessment | None,
    heuristic_policy_version: str,
) -> list[CheckResult]:
    checks: list[CheckResult] = []

    checks.append(
        _ok(
            "priority_tier zgodny z definicją 22.7",
            assessment.priority_tier is expected_tier,
            f"tier {assessment.priority_tier.value}",
            f"oczekiwano {expected_tier.value}, jest {assessment.priority_tier.value}",
        )
    )

    # The negative is a hard stop, not a negative weight (L4444, L4472, L5550).
    has_negative = bool(obs.negative_signal_classes)
    # REJECT follows from a negative signal and ONLY from it — in both directions.
    checks.append(
        _ok(
            "REJECT wtedy i tylko wtedy, gdy jest sygnał negatywny",
            (assessment.priority_tier is PriorityTier.REJECT) == has_negative,
            "negatyw → REJECT" if has_negative else "brak negatywu → brak REJECT",
            f"negatyw={has_negative}, tier={assessment.priority_tier.value}",
        )
    )
    if has_negative:
        checks.append(
            _ok(
                "negatyw nie jest kompensowany skalą engagementu",
                assessment.priority_tier is PriorityTier.REJECT,
                f"{obs.votes} głosów / {obs.reaction_total} reakcji nie kompensuje "
                f"{', '.join(obs.negative_signal_labels)}",
                "wysoki engagement przegłosował sygnał negatywny",
            )
        )
        checks.append(
            _ok(
                "wątek z negatywem nie nadaje się do wejścia",
                not assessment.is_enterable(),
                "is_enterable() = False",
                "is_enterable() = True mimo sygnału negatywnego",
            )
        )

    # UNKNOWN never as a zero (L4299, L5552).
    for field in expected_unknown_fields:
        value = getattr(assessment, field)
        checks.append(
            _ok(
                f"{field} = UNKNOWN, nie zero",
                (not value.is_known) and value.unknown_reason is not None,
                f"UNKNOWN({value.unknown_reason})",
                f"metryka ukryta przez platformę zapisana jako {value.value!r}",
            )
        )
    # A metric the platform DOES EXPOSE has no right to be UNKNOWN.
    for field in ("depth_profile", "unique_participants", "substantive_reply_ratio"):
        value = getattr(assessment, field)
        checks.append(
            _ok(
                f"{field} odczytane, nie UNKNOWN",
                value.is_known,
                f"{value.value}",
                f"metryka widoczna w źródle zapisana jako UNKNOWN({value.unknown_reason})",
            )
        )

    checks.append(
        _ok(
            "window_state przepisany ze źródła",
            assessment.window_state is obs.window_state,
            assessment.window_state.value,
            f"oczekiwano {obs.window_state.value}, jest {assessment.window_state.value}",
        )
    )

    checks.append(
        _ok(
            "heuristic_policy_version zapisana",
            assessment.heuristic_policy_version == heuristic_policy_version,
            assessment.heuristic_policy_version,
            f"oczekiwano {heuristic_policy_version}, jest {assessment.heuristic_policy_version!r}",
        )
    )

    checks.append(
        _ok(
            "entry_opportunity niepuste",
            bool(assessment.entry_opportunity.strip()),
            assessment.entry_opportunity[:60],
            "puste pole entry_opportunity",
        )
    )

    # fake_engagement_flags are log-only (L4492, L5551).
    if obs.fake_engagement_flags:
        checks.append(
            _ok(
                "flagi fake engagementu trafiają do wyniku",
                set(assessment.fake_engagement_flags) == set(obs.fake_engagement_flags),
                ", ".join(assessment.fake_engagement_flags),
                "flagi zgubione po drodze — log-only znaczy zapisz, nie zignoruj",
            )
        )
        checks.append(
            _ok(
                "flaga fake engagementu nie odrzuca wątku",
                assessment.priority_tier is not PriorityTier.REJECT,
                f"tier {assessment.priority_tier.value} mimo flag",
                "REJECT z powodu flagi — w E1 detekcja jest log-only",
            )
        )
        if assessment_without_flags is not None:
            checks.append(
                _ok(
                    "identyczny tier jak bez flagi (kontrfaktyczny bliźniak)",
                    assessment.priority_tier is assessment_without_flags.priority_tier,
                    f"{assessment.priority_tier.value} == "
                    f"{assessment_without_flags.priority_tier.value}",
                    f"{assessment.priority_tier.value} != "
                    f"{assessment_without_flags.priority_tier.value} — flaga obniżyła tier",
                )
            )

    return checks


# ---------------------------------------------------------------------------
# Family: discovery
# ---------------------------------------------------------------------------


def check_discovery_result(
    result: DiscoveryResult,
    observations: Sequence[ThreadObservation],
    *,
    must_not_shortlist: Sequence[str] = (),
    must_be_last: Sequence[str] = (),
    must_precede: Sequence[tuple[str, str]] = (),
    permutation_result: DiscoveryResult | None = None,
    fakefree_result: DiscoveryResult | None = None,
    heuristic_policy_version: str,
) -> list[CheckResult]:
    checks: list[CheckResult] = []
    obs_by_ref = {o.thread_ref: o for o in observations}
    order = result.order
    position = {ref: i for i, ref in enumerate(order)}

    checks.append(
        _ok(
            "wynik jest permutacją wejścia",
            sorted(order) == sorted(obs_by_ref),
            f"{len(order)} kandydatów",
            f"wejście {len(obs_by_ref)}, wyjście {len(order)} — zgubione albo dodane wątki",
        )
    )
    if sorted(order) != sorted(obs_by_ref):
        return checks

    # The shortlist never contains a thread with a negative signal (L4472).
    negative_refs = {r for r, o in obs_by_ref.items() if o.negative_signal_classes}
    checks.append(
        _ok(
            "żaden wątek z sygnałem negatywnym nie trafia na krótką listę",
            not (negative_refs & set(result.shortlist)),
            f"{len(negative_refs)} wątków z negatywem, wszystkie poza krótką listą",
            f"na krótkiej liście: {sorted(negative_refs & set(result.shortlist))}",
        )
    )

    # The negatives occupy the suffix of the order — they are not mixed in with the positives.
    if negative_refs:
        negative_positions = sorted(position[r] for r in negative_refs)
        expected = list(range(len(order) - len(negative_refs), len(order)))
        checks.append(
            _ok(
                "wątki z negatywem zajmują koniec porządku",
                negative_positions == expected,
                f"pozycje {negative_positions}",
                f"pozycje {negative_positions}, oczekiwano {expected}",
            )
        )

    # A thread with a closed window does not enter the shortlist (L4590, L5541).
    closed_refs = {r for r, o in obs_by_ref.items() if o.window_state is WindowState.CLOSED}
    checks.append(
        _ok(
            "wątek z window_state=CLOSED nie trafia na krótką listę",
            not (closed_refs & set(result.shortlist)),
            f"{len(closed_refs)} zamkniętych, wszystkie poza krótką listą",
            f"na krótkiej liście: {sorted(closed_refs & set(result.shortlist))}",
        )
    )

    wrong_tier_refs = [
        c.thread_ref
        for c in result.ordered
        if c.shortlisted and c.priority_tier not in (PriorityTier.A, PriorityTier.B)
    ]
    checks.append(
        _ok(
            "krótka lista zawiera wyłącznie tier A i B",
            not wrong_tier_refs,
            f"{len(result.shortlist)} pozycji na krótkiej liście",
            f"tier spoza A/B na krótkiej liście: {wrong_tier_refs}",
        )
    )

    # The signal class order from L4471-4479: among candidates without a negative the class
    # rank must not fall as one goes down the list.
    class_ranks = [
        signal_class_rank(obs_by_ref[r].dominant_signal_class)
        for r in order
        if r not in negative_refs
    ]
    non_decreasing = all(a <= b for a, b in itertools.pairwise(class_ranks))
    checks.append(
        _ok(
            "porządek klas sygnału zachowany (L4471-4479)",
            non_decreasing,
            f"rangi klas: {class_ranks}",
            f"ranga klasy rośnie i maleje na przemian: {class_ranks}",
        )
    )

    for ref in must_not_shortlist:
        checks.append(
            _ok(
                f"{ref} poza krótką listą",
                ref not in result.shortlist,
                "poza krótką listą",
                "trafił na krótką listę mimo oczekiwania",
            )
        )
    for ref in must_be_last:
        checks.append(
            _ok(
                f"{ref} na końcu porządku",
                position[ref] == len(order) - 1,
                f"pozycja {position[ref]} z {len(order)}",
                f"pozycja {position[ref]}, oczekiwano {len(order) - 1}",
            )
        )
    for earlier, later in must_precede:
        checks.append(
            _ok(
                f"{earlier} przed {later}",
                position[earlier] < position[later],
                f"{position[earlier]} < {position[later]}",
                f"{position[earlier]} >= {position[later]}",
            )
        )

    # The order does not depend on the input order (L5553).
    if permutation_result is not None:
        checks.append(
            _ok(
                "porządek niezależny od kolejności wejścia",
                permutation_result.order == order,
                "identyczny porządek po permutacji wejścia",
                "permutacja wejścia zmieniła porządek wyjścia",
            )
        )

    # `fake_engagement_flags` are NOT an input to the ranking function (L4492, L5551).
    if fakefree_result is not None:
        without_suffix = tuple(r.split("#")[0] for r in fakefree_result.order)
        checks.append(
            _ok(
                "flagi fake engagementu nie zmieniają porządku",
                without_suffix == order,
                "identyczny porządek po usunięciu flag",
                "usunięcie flagi zmieniło porządek — flaga weszła do rankingu",
            )
        )

    checks.append(
        _ok(
            "heuristic_policy_version zapisana przy rankingu",
            result.heuristic_policy_version == heuristic_policy_version,
            result.heuristic_policy_version,
            f"oczekiwano {heuristic_policy_version}",
        )
    )

    return checks


def check_signal_pair(
    result: DiscoveryResult,
    stronger_ref: str,
    weaker_ref: str,
    stronger: SignalClass,
    weaker: SignalClass,
    *,
    tie: bool,
) -> list[CheckResult]:
    """A pair of candidates differing ONLY in signal class (DoD D17)."""
    position = {ref: i for i, ref in enumerate(result.order)}
    checks = [
        _ok(
            "oba warianty pary są w wyniku",
            stronger_ref in position and weaker_ref in position,
            "oba obecne",
            f"brakuje: {sorted({stronger_ref, weaker_ref} - set(position))}",
        )
    ]
    if stronger_ref not in position or weaker_ref not in position:
        return checks

    if tie:
        checks.append(
            _ok(
                f"{stronger.value} i {weaker.value} mają tę samą rangę klasy (L4474)",
                signal_class_rank(stronger) == signal_class_rank(weaker)
                and not strictly_precedes_by_class(stronger, weaker)
                and not strictly_precedes_by_class(weaker, stronger),
                f"ranga {signal_class_rank(stronger)} dla obu",
                f"{signal_class_rank(stronger)} vs {signal_class_rank(weaker)}",
            )
        )
        return checks

    checks.append(
        _ok(
            f"{stronger.value} przed {weaker.value}",
            position[stronger_ref] < position[weaker_ref],
            f"pozycje {position[stronger_ref]} < {position[weaker_ref]}",
            f"pozycje {position[stronger_ref]} >= {position[weaker_ref]} — "
            "porządek klas sygnału złamany",
        )
    )

    if weaker in NEGATIVE_STOP_CLASSES:
        # A negative class is NOT the weakest entry in the rank table — in the contracts
        # it has rank 0, that is, formally the HIGHEST. The document (L4472) says that the
        # negative "acts like a stop, not like a weight", so the order is decided by
        # a separate branch, not by comparing ranks. This check guards exactly that: the
        # position follows from the stop rule, and a naive rank comparison would give the
        # OPPOSITE result.
        checks.append(
            _ok(
                "negatyw wypchnięty regułą stopu, nie rangą klasy (L4472)",
                signal_class_rank(weaker) <= signal_class_rank(stronger)
                and position[weaker_ref] > position[stronger_ref],
                f"ranga negatywu {signal_class_rank(weaker)} <= "
                f"{signal_class_rank(stronger)}, a mimo to pozycja "
                f"{position[weaker_ref]} > {position[stronger_ref]}",
                "ranga klasy zdecydowała o kolejności — negatyw stał się wagą",
            )
        )
    else:
        checks.append(
            _ok(
                "porządek klas jest ścisły, nie remisowy",
                strictly_precedes_by_class(stronger, weaker),
                f"{signal_class_rank(stronger)} < {signal_class_rank(weaker)}",
                f"{signal_class_rank(stronger)} !< {signal_class_rank(weaker)}",
            )
        )
    return checks


# ---------------------------------------------------------------------------
# Family: reply draft
# ---------------------------------------------------------------------------


def check_draft(
    obs: ThreadObservation,
    brief: DraftBrief,
    draft: DraftProposal,
    *,
    expect_declined: bool,
    expected_decline_reason,
) -> list[CheckResult]:
    checks: list[CheckResult] = []

    checks.append(
        _ok(
            "odmowa albo draft — zgodnie z oczekiwaniem",
            draft.declined == expect_declined,
            "odmowa" if draft.declined else "draft napisany",
            f"oczekiwano declined={expect_declined}, jest {draft.declined}",
        )
    )
    if expect_declined:
        checks.append(
            _ok(
                "powód odmowy zgodny",
                draft.decline_reason == expected_decline_reason,
                str(draft.decline_reason),
                f"oczekiwano {expected_decline_reason}, jest {draft.decline_reason}",
            )
        )
        checks.append(
            _ok(
                "odmowa nie zostawia treści",
                not draft.text.strip(),
                "pusty tekst",
                f"odmowa z treścią: {draft.text[:60]!r}",
            )
        )
        checks.append(
            _ok(
                "odmowa nie stwierdza żadnego claimu",
                not draft.used_claims,
                "brak used_claims",
                f"used_claims mimo odmowy: {draft.used_claims}",
            )
        )
        return checks

    text = draft.text.casefold()

    # L751, L5307: the draft does not use an UNVERIFIED claim as a fact.
    unusable_ids = {c.id for c in brief.unusable_claims}
    used_unusable_ids = sorted(unusable_ids & set(draft.used_claims))
    checks.append(
        _ok(
            "draft nie stwierdza claimu UNVERIFIED/EXPIRED/RETRACTED jako faktu",
            not used_unusable_ids,
            f"used_claims = {list(draft.used_claims)}",
            f"claim niezweryfikowany podany jako fakt: {used_unusable_ids}",
        )
    )
    leaked_markers = sorted(
        c.id for c in brief.unusable_claims if c.marker and c.marker.casefold() in text
    )
    checks.append(
        _ok(
            "marker claimu niezweryfikowanego nie występuje w treści",
            not leaked_markers,
            "brak markerów claimów niezweryfikowanych",
            f"marker claimu w treści mimo braku weryfikacji: {leaked_markers}",
        )
    )

    # prohibited_topics (L724)
    violations = sorted(
        f"{t.id}:{k}" for t in brief.prohibited_topics for k in t.keywords if k.casefold() in text
    )
    checks.append(
        _ok(
            "draft respektuje prohibited_topics",
            not violations,
            f"{len(brief.prohibited_topics)} zakazanych tematów, żadnego naruszenia",
            f"zakazane słowa w treści: {violations}",
        )
    )

    checks.append(
        _ok(
            "draft niepusty",
            bool(draft.text.strip()),
            f"{len(draft.text)} znaków",
            "draft bez treści, a nie oznaczony jako odmowa",
        )
    )

    # The draft is to be a contribution, not a rewrite of the thread. The same metric the
    # approval gate uses (L4607) — one symbol, not a second definition.
    edit_distance = normalized_edit_distance(obs.body, draft.text)
    checks.append(
        _ok(
            "draft nie jest kopią treści wątku",
            edit_distance > MATERIAL_EDIT_THRESHOLD,
            f"znormalizowany dystans edycji od treści wątku = {edit_distance:.3f}",
            f"dystans {edit_distance:.3f} <= {MATERIAL_EDIT_THRESHOLD} — to przepisanie wątku",
        )
    )

    checks.append(
        _ok(
            "draft stwierdza wyłącznie claimy dozwolone",
            set(draft.used_claims) <= {c.id for c in brief.usable_claims},
            f"used_claims = {list(draft.used_claims)}",
            f"claimy spoza briefu: "
            f"{sorted(set(draft.used_claims) - {c.id for c in brief.usable_claims})}",
        )
    )

    return checks
