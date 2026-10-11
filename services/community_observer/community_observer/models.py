"""What an observation of a community looks like, independently of the platform it came from.

`docs/KIERUNEK-PRODUKTU.md` §10: "Platforma jest tylko surface." A thread from Hacker News and a
thread from a subreddit arrive here in the same shape, so everything downstream — persistence,
the community picture, the judgment — never asks which platform it is looking at. The adapter is
the only place that knows.

Two rules from the roadmap survive the reset because they are about honesty, not about
marketing (`docs/OUTCOMES.md` §1):

* **A metric the platform does not show is `UNKNOWN`, never 0** (22.2 point 7). Reddit hides
  vote counts on fresh posts; Algolia occasionally returns `points: null`. `ThreadMetrics`
  carries `Observed[int]` for exactly that reason, and a caller who wants a number has to say
  what it does when there is none.
* **Every observation carries its own evidence**: the canonical URL on the platform, the moment
  it was read (`observed_at`, timezone-aware), and the SHA-256 of the raw payload it was parsed
  from. A community picture that cites a thread cites this record; a record without evidence
  would be a claim.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from community_os_contracts import AccessMode, ObservationPlatform, Observed
from pydantic import BaseModel, ConfigDict, Field, model_validator

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def raw_digest(payload: Any) -> str:
    """SHA-256 of a payload's canonical JSON. The same payload gives the same digest in any order.

    `sort_keys=True` and the compact separators are what make the digest a property of the DATA
    and not of the wire: Algolia and Reddit both reorder keys between responses.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"{field} musi mieć strefę czasową. Naiwny czas obserwacji jest niejednoznaczny, "
            "a snapshoty po 1/6/24 h liczą się od niego"
        )
    return value


class CommunityRef(BaseModel):
    """One community as the adapter names it: `platform` + `key` (e.g. `ask`, `r/devops`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: ObservationPlatform
    key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    #: Copied from the adapter. `PUBLIC_API_NO_ACCOUNT` is the cheaper path the roadmap prefers
    #: (3.1 point 42); `ACCOUNT_REQUIRED` says the read runs as somebody's account.
    access_mode: AccessMode

    @property
    def ref(self) -> str:
        return f"{self.platform.value}:{self.key}"


class ThreadMetrics(BaseModel):
    """The two counters every platform in scope exposes — or explicitly does not."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    score: Observed[int]
    comment_count: Observed[int]


class ObservedThread(BaseModel):
    """A top-level post as read at `observed_at`. Immutable — a later read is a later record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: ObservationPlatform
    community_key: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    #: The canonical URL of the thread ON THE PLATFORM — the evidence anyone can open.
    url: str = Field(min_length=1)
    #: For link posts: where the post points. `None` for text posts.
    link_url: str | None = None
    title: str = Field(min_length=1)
    #: Plain text. `None` when the post has no body (a link post) or the body was removed.
    body: str | None = None
    #: `None` when the platform reports the author as deleted or hidden.
    author: str | None = None
    created_at: datetime
    observed_at: datetime
    metrics: ThreadMetrics
    raw_sha256: str

    @model_validator(mode="after")
    def _timestamps_are_aware(self) -> ObservedThread:
        _require_aware(self.created_at, "created_at")
        _require_aware(self.observed_at, "observed_at")
        return self

    @model_validator(mode="after")
    def _digest_is_a_full_sha256(self) -> ObservedThread:
        if not _SHA256_HEX.match(self.raw_sha256):
            raise ValueError("raw_sha256 musi być pełnym SHA-256 w hex — to jest dowód odczytu")
        return self

    @property
    def ref(self) -> str:
        """`platform:community:external_id` — the spelling `JudgmentRecord.thread_ref` uses."""
        return f"{self.platform.value}:{self.community_key}:{self.external_id}"


class ObservedComment(BaseModel):
    """One comment in a thread, with its place in the tree."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    external_id: str = Field(min_length=1)
    #: `None` for a top-level comment (its parent is the thread itself).
    parent_external_id: str | None = None
    #: `None` when the platform reports the comment deleted — the ROW stays, because a deleted
    #: comment is still evidence that somebody replied, and moderation is a signal (22.7).
    author: str | None = None
    body: str | None = None
    created_at: datetime
    observed_at: datetime
    depth: int = Field(ge=0)
    #: The comment's points where the platform shows them (Reddit: `score`, unless `score_hidden`),
    #: and an explicit reason where it does not (Hacker News shows none: NOT_EXPOSED_BY_PLATFORM).
    #: Required, not defaulted: an adapter has to say which of the two it is (etap 3 M0, 0005).
    score: Observed[int]

    @model_validator(mode="after")
    def _timestamps_are_aware(self) -> ObservedComment:
        _require_aware(self.created_at, "created_at")
        _require_aware(self.observed_at, "observed_at")
        return self

    @model_validator(mode="after")
    def _top_level_has_depth_zero(self) -> ObservedComment:
        if (self.parent_external_id is None) != (self.depth == 0):
            raise ValueError(
                "komentarz bez rodzica ma depth 0 i odwrotnie; jest "
                f"parent={self.parent_external_id!r}, depth={self.depth}"
            )
        return self


class ThreadSnapshot(BaseModel):
    """A thread and its comments as read in one pass."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    thread: ObservedThread
    comments: tuple[ObservedComment, ...] = ()
    #: `True` when the platform paginated the comment tree and the adapter did not follow every
    #: continuation. Written down rather than silently dropping comments: a picture drawn from
    #: a truncated thread must know it is.
    comments_truncated: bool = False

    @model_validator(mode="after")
    def _comment_ids_are_unique(self) -> ThreadSnapshot:
        ids = [comment.external_id for comment in self.comments]
        if len(ids) != len(set(ids)):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"snapshot ma zduplikowane komentarze: {duplicates[:5]}")
        return self

    @property
    def unique_participants(self) -> Observed[int]:
        """Distinct visible authors among the comments — the thread author excluded.

        `UNKNOWN` when the tree was truncated: counting participants over half a thread and
        calling it the number is the false-precision the roadmap's 22.7 warns about.
        """
        from community_os_contracts import UnknownReason

        if self.comments_truncated:
            return Observed[int].unknown(
                UnknownReason.ADAPTER_COULD_NOT_PARSE, "drzewo komentarzy obcięte przez platformę"
            )
        authors = {c.author for c in self.comments if c.author and c.author != self.thread.author}
        return Observed[int].known(len(authors))
