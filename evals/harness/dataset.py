"""Eval input: the 48 threads of the fixture site (D16) → `ThreadObservation`.

The data comes from a DETERMINISTIC GENERATOR, not from HTTP. The reason: `pytest evals`
must work without a network and without compose coming up, and the `wenext_fixture_site`
generator is pure stdlib — the same seed gives the same result down to the byte (NOTES D16).
Thanks to that the evals and the F3 contract tests look at identical data.

Import by path, not by a package dependency: `wenext-fixture-site` pulls in
fastapi/uvicorn/jinja2, while the evals need the generator alone. The path is
computed from the location of this file, so it does not depend on the working directory.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from community_os_contracts import SignalClass, WindowState

from .types import ThreadObservation

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_SRC = REPO_ROOT / "fixtures" / "site" / "src"

#: D16 seed. The same as the default in `wenext_fixture_site.config`.
FIXTURE_SEED = 20260830


class FixtureUnavailable(RuntimeError):
    """The fixture site (D16) is not available — the evals have nothing to run on."""


def _import_fixture():
    if str(FIXTURE_SRC) not in sys.path:
        sys.path.insert(0, str(FIXTURE_SRC))
    try:
        from wenext_fixture_site import config, generator, seed_data
    except ImportError as exc:  # pragma: no cover — signals the absence of D16
        raise FixtureUnavailable(
            f"Nie da się zaimportować `wenext_fixture_site` z {FIXTURE_SRC}. "
            "Evale D17 mają fixture site (D16) jako twarde wejście — bez niego nie ma "
            "czego oceniać."
        ) from exc
    return config, generator, seed_data


# ---------------------------------------------------------------------------
# Mapping the fixture's negative signals onto the classes from the D21 contracts
# ---------------------------------------------------------------------------
#
# The L4444 table lists FIVE observable negative phenomena: removal by
# moderation, lock, downvote below zero, spam/bot accusation, "ratio".
# `SignalClass` from D21 has THREE classes for them: NEGATIVE_MODERATION, BAN, LOCK.
# The compression is deliberate and recorded here in one place; the original fixture
# label stays in the assessment's `evidence_ref`, so no information is lost.
# The reservation about D21 is in NOTES.md — we do not "fix" someone else's enum quietly.
FIXTURE_NEGATIVE_SIGNAL_TO_CLASS: dict[str, SignalClass] = {
    "NEGATIVE_MODERATION": SignalClass.NEGATIVE_MODERATION,
    "LOCK": SignalClass.LOCK,
    "BAN": SignalClass.BAN,
    "DOWNVOTE_BELOW_ZERO": SignalClass.NEGATIVE_MODERATION,
    "SPAM_ACCUSATION": SignalClass.NEGATIVE_MODERATION,
    "BOT_ACCUSATION": SignalClass.NEGATIVE_MODERATION,
    "RATIO": SignalClass.NEGATIVE_MODERATION,
}


class UnknownNegativeSignal(ValueError):
    """The fixture introduced a negative label the harness does not know — fail-closed."""


def negative_classes(labels: tuple[str, ...]) -> tuple[SignalClass, ...]:
    """Fixture labels → signal classes. An unknown label is an error, not a skip."""
    result: list[SignalClass] = []
    for label in labels:
        klasa = FIXTURE_NEGATIVE_SIGNAL_TO_CLASS.get(label)
        if klasa is None:
            raise UnknownNegativeSignal(
                f"Etykieta sygnału negatywnego {label!r} nie ma mapowania na SignalClass. "
                "Ciche pominięcie zamieniłoby twardy stop w brak sygnału (L4444, L4472)."
            )
        result.append(klasa)
    return tuple(dict.fromkeys(result))


# ---------------------------------------------------------------------------
# Dominant signal class — an OBSERVATION, not an assessment
# ---------------------------------------------------------------------------
#
# The order of the classes lives in the contracts (`SIGNAL_CLASS_ORDER`). Here we settle only
# which class the thread presents AT ALL — on the basis of what is visible from the outside.
# The order of the checks goes from the strongest class to the weakest (L4471-4479).

#: Below this share of substantive replies the thread does not present the
#: SUBSTANTIVE_REPLY class, only a plain REPLY. The value is part of the heuristic policy
#: and is versioned together with it.
SUBSTANTIVE_PRESENCE_RATIO = 0.30


def dominant_signal_class(
    *,
    negatives: tuple[SignalClass, ...],
    amplification_count: int,
    substantive_reply_ratio: float,
    reply_count: int,
    reaction_total: int,
    votes: int,
) -> SignalClass:
    """Which signal class prevails in this thread (L4471-4479)."""
    if negatives:
        # The negative is a conditional branch BEFORE the ranking, not a weight in a sum.
        return negatives[0]
    if amplification_count > 0:
        return SignalClass.INTENTIONAL_AMPLIFICATION
    if reply_count > 0 and substantive_reply_ratio >= SUBSTANTIVE_PRESENCE_RATIO:
        return SignalClass.SUBSTANTIVE_REPLY
    if reply_count > 0:
        return SignalClass.REPLY
    if reaction_total > 0 or votes > 0:
        return SignalClass.LIKE
    return SignalClass.FOLLOW


def _reaction_total(thread) -> int:
    return sum(r.count for r in thread.reactions)


def _observation(thread, epoch) -> ThreadObservation:
    metrics = thread.metrics
    negatives = negative_classes(thread.negative_signals)
    reaction_total = _reaction_total(thread)
    return ThreadObservation(
        thread_ref=thread.id,
        archetype=thread.archetype,
        community=thread.community,
        title=thread.title,
        body=thread.body,
        author=thread.author,
        created_at=thread.created_at,
        # The observation happens in the fixture's epoch, never "now" — otherwise two
        # runs of the harness would produce different records.
        observed_at=epoch,
        depth_levels=metrics.depth_levels,
        reply_count=metrics.reply_count,
        unique_participants=metrics.unique_participants,
        median_reply_chars=metrics.median_reply_chars,
        replies_first_hour=metrics.replies_first_hour,
        substantive_reply_ratio=metrics.substantive_reply_ratio,
        author_reply_count=metrics.author_reply_count,
        author_first_reply_minutes=metrics.author_first_reply_minutes,
        icp_share=metrics.icp_share,
        reach_impressions=metrics.reach_impressions,
        reach_calibration_basis=metrics.reach_calibration_basis,
        hidden_metrics=tuple(metrics.hidden_metrics),
        votes=thread.votes,
        reaction_total=reaction_total,
        amplification_count=len(thread.amplifications),
        amplification_kinds=tuple(a.kind for a in thread.amplifications),
        window_state=WindowState(thread.window_state),
        locked=thread.locked,
        removed_by_moderation=thread.removed_by_moderation,
        negative_signal_labels=tuple(thread.negative_signals),
        negative_signal_classes=negatives,
        fake_engagement_flags=tuple(thread.fake_engagement_flags),
        dominant_signal_class=dominant_signal_class(
            negatives=negatives,
            amplification_count=len(thread.amplifications),
            substantive_reply_ratio=metrics.substantive_reply_ratio,
            reply_count=metrics.reply_count,
            reaction_total=reaction_total,
            votes=thread.votes,
        ),
        spec_key=thread.spec_key,
        illustrates=tuple(thread.illustrates),
        seed_note=thread.seed_note,
    )


@lru_cache(maxsize=2)
def observations(seed: int = FIXTURE_SEED) -> tuple[ThreadObservation, ...]:
    """All 48 fixture threads as observations. The order is stable."""
    config, generator, _ = _import_fixture()
    repo = generator.build_repository(seed)
    # Epoch + one day: the observation is later than every thread, but still fixed.
    epoch = config.FIXTURE_EPOCH + timedelta(days=1)
    return tuple(_observation(t, epoch) for t in repo.threads)


@lru_cache(maxsize=2)
def by_ref(seed: int = FIXTURE_SEED) -> dict[str, ThreadObservation]:
    return {o.thread_ref: o for o in observations(seed)}


@lru_cache(maxsize=2)
def by_spec_key(seed: int = FIXTURE_SEED) -> dict[str, ThreadObservation]:
    return {o.spec_key: o for o in observations(seed)}


def observation(spec_key: str, seed: int = FIXTURE_SEED) -> ThreadObservation:
    """A thread by its key from the D16 seed (e.g. `f-neg-locked-high-engagement`)."""
    try:
        return by_spec_key(seed)[spec_key]
    except KeyError as exc:
        available = ", ".join(sorted(by_spec_key(seed))[:5])
        raise KeyError(
            f"Fixture nie ma wątku o kluczu {spec_key!r}. Dostępne zaczynają się od: {available}…"
        ) from exc


def for_archetype(archetype: str, seed: int = FIXTURE_SEED) -> tuple[ThreadObservation, ...]:
    return tuple(o for o in observations(seed) if o.archetype == archetype)


def fixture_content_hash(seed: int = FIXTURE_SEED) -> str:
    """Hash of the fixture content — evidence that the eval and F3 look at the same data set."""
    import hashlib
    import json

    _, generator, _ = _import_fixture()
    repo = generator.build_repository(seed)
    payload = json.dumps(
        [t.as_dict() for t in repo.threads], ensure_ascii=False, sort_keys=True
    ).encode()
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "FIXTURE_NEGATIVE_SIGNAL_TO_CLASS",
    "FIXTURE_SEED",
    "FixtureUnavailable",
    "UnknownNegativeSignal",
    "by_ref",
    "by_spec_key",
    "dominant_signal_class",
    "fixture_content_hash",
    "for_archetype",
    "negative_classes",
    "observation",
    "observations",
]
