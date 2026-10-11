"""Content model of the fixture.

The structures are deliberately flat and serializable — the same object goes both into the
Jinja template and into the "internal API" under `/api/public/`. The document requires
preferring the internal API over the DOM for parsing (L5034), so the JSON snapshot must not
be poorer than the HTML.

A `None` in a metric means: the platform DOES NOT SHOW this number. The name of the metric
is then listed in `hidden_metrics`. This is the only way in which the fixture can force
`UNKNOWN` instead of zero on the consumer (L4299, L5552).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# The values must be identical to the enums in `community_os_contracts.enums`.
# The fixture does not import the contracts (the runtime dependency goes one way only), so
# the agreement is guarded by `tests/test_contracts_alignment.py`.
ARCHETYPE_THREADED = "THREADED_COMMUNITY"
ARCHETYPE_GRAPH = "PROFESSIONAL_GRAPH"

ACCESS_ACCOUNT_REQUIRED = "ACCOUNT_REQUIRED"
ACCESS_PUBLIC_API = "PUBLIC_API_NO_ACCOUNT"


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Amplification:
    """Intentional amplification (L4442): someone took on the cost of passing it on."""

    kind: str  # QUOTE | CROSSPOST | NEWSLETTER_LINK | REPOST_WITH_COMMENT | INBOUND_THREAD_LINK
    source: str
    comment: str
    created_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "comment": self.comment,
            "created_at": _iso(self.created_at),
        }


@dataclass(frozen=True)
class Reaction:
    """A typed reaction — PROFESSIONAL_GRAPH distinguishes kinds, the forum does not."""

    kind: str
    count: int

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "count": self.count}


@dataclass(frozen=True)
class Comment:
    id: str
    author: str
    body: str
    created_at: datetime
    depth: int
    votes: int
    by_thread_author: bool
    substantive: bool
    icp: bool
    removed_by_moderation: bool
    replies: tuple[Comment, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "author": None if self.removed_by_moderation else self.author,
            "body": "[usunięte przez moderację]" if self.removed_by_moderation else self.body,
            "created_at": _iso(self.created_at),
            "depth": self.depth,
            "votes": self.votes,
            "by_thread_author": self.by_thread_author,
            "substantive": self.substantive,
            "icp": self.icp,
            "removed_by_moderation": self.removed_by_moderation,
            "replies": [r.as_dict() for r in self.replies],
        }


@dataclass(frozen=True)
class ThreadMetrics:
    """Metrics computed from the tree actually generated, not declared in the seed.

    The exception is `reach_calibration_basis` — in F0 the calibration against the channel
    background is INPUT DATA, not computed (no surface baseline, D04 item 4).
    """

    reply_count: int
    unique_participants: int
    depth_levels: int
    median_reply_chars: int
    replies_first_hour: int
    replies_after_first_hour: int
    substantive_reply_ratio: float
    author_reply_count: int
    author_first_reply_minutes: int | None
    icp_share: float | None
    reach_impressions: int | None
    reach_calibration_basis: str
    reaction_burst_without_replies: bool
    hidden_metrics: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "reply_count": self.reply_count,
            "unique_participants": self.unique_participants,
            "depth_levels": self.depth_levels,
            "median_reply_chars": self.median_reply_chars,
            "replies_first_hour": self.replies_first_hour,
            "replies_after_first_hour": self.replies_after_first_hour,
            "substantive_reply_ratio": self.substantive_reply_ratio,
            "author_reply_count": self.author_reply_count,
            "author_first_reply_minutes": self.author_first_reply_minutes,
            "icp_share": self.icp_share,
            "reach_impressions": self.reach_impressions,
            "reach_calibration_basis": self.reach_calibration_basis,
            "reaction_burst_without_replies": self.reaction_burst_without_replies,
            "hidden_metrics": list(self.hidden_metrics),
        }


@dataclass(frozen=True)
class Thread:
    """A forum thread or a professional-network post — one structure for both archetypes."""

    id: str
    slug: str
    archetype: str
    community: str
    title: str
    body: str
    author: str
    author_handle: str
    created_at: datetime
    votes: int
    locked: bool
    removed_by_moderation: bool
    window_state: str
    negative_signals: tuple[str, ...]
    reactions: tuple[Reaction, ...]
    amplifications: tuple[Amplification, ...]
    comments: tuple[Comment, ...]
    metrics: ThreadMetrics
    #: Seed metadata — why this thread is in the set. Input to the D17 evals.
    spec_key: str
    illustrates: tuple[str, ...]
    seed_note: str
    fake_engagement_flags: tuple[str, ...] = field(default=())

    @property
    def path(self) -> str:
        if self.archetype == ARCHETYPE_THREADED:
            return f"/c/{self.community}/t/{self.slug}"
        return f"/g/p/{self.slug}"

    @property
    def api_path(self) -> str:
        return f"/api/public/threads/{self.id}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "archetype": self.archetype,
            "community": self.community,
            "url": self.path,
            "title": self.title,
            "body": "[usunięte przez moderację]" if self.removed_by_moderation else self.body,
            "author": self.author,
            "author_handle": self.author_handle,
            "created_at": _iso(self.created_at),
            "votes": self.votes,
            "locked": self.locked,
            "removed_by_moderation": self.removed_by_moderation,
            "window_state": self.window_state,
            "negative_signals": list(self.negative_signals),
            "fake_engagement_flags": list(self.fake_engagement_flags),
            "reactions": [r.as_dict() for r in self.reactions],
            "amplifications": [a.as_dict() for a in self.amplifications],
            "metrics": self.metrics.as_dict(),
            "seed": {
                "spec_key": self.spec_key,
                "illustrates": list(self.illustrates),
                "note": self.seed_note,
            },
            "comments": [c.as_dict() for c in self.comments],
        }

    def as_summary(self) -> dict[str, Any]:
        payload = self.as_dict()
        payload.pop("comments")
        return payload


@dataclass(frozen=True)
class Community:
    slug: str
    name: str
    description: str
    members: int
    archetype: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "members": self.members,
            "archetype": self.archetype,
            "url": f"/c/{self.slug}",
        }


@dataclass(frozen=True)
class Repository:
    """The entire content of the fixture generated for one seed."""

    seed: int
    communities: tuple[Community, ...]
    threads: tuple[Thread, ...]

    def by_id(self, thread_id: str) -> Thread | None:
        return next((t for t in self.threads if t.id == thread_id), None)

    def by_slug(self, archetype: str, slug: str) -> Thread | None:
        return next(
            (t for t in self.threads if t.archetype == archetype and t.slug == slug),
            None,
        )

    def for_archetype(self, archetype: str) -> tuple[Thread, ...]:
        return tuple(t for t in self.threads if t.archetype == archetype)

    def for_community(self, slug: str) -> tuple[Thread, ...]:
        return tuple(t for t in self.threads if t.community == slug)
