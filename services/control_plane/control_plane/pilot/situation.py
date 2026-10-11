"""From a fresh thread read to what the generator sees, plus the hash that says "this exact
version of the conversation has already been answered".

Two things the pilot brief insists on and this module makes true:

* **The target and its parent chain survive clipping.** `MAX_MESSAGES` of `observe/reactions.py`
  caps the transcript; when a thread is bigger, the pilot keeps the target message, every
  ancestor of it (so the model sees what the target answers), and fills the rest with the newest
  messages. `context_complete` says whether anything was dropped or the adapter reported a
  truncated comment tree, so a clipped read never looks like a full one.
* **The situation hash is over the conversation, not over the clock.** Thread id, target id and
  the sorted list of `(message_id, sha256(body))` of what the model would see. The same hash a
  second time is a duplicate (no model call); a new comment in the branch changes it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from community_observer import ObservedComment, ObservedThread

from control_plane.observe.reactions import (
    MAX_MESSAGES,
    RELATION_NEW_ENTRY,
    ReactionSituation,
    SituationMessage,
)


def messages_from_thread(
    thread: ObservedThread, comments: Sequence[ObservedComment]
) -> tuple[SituationMessage, ...]:
    """The post as the root message (its `external_id`), then the comments as observed.
    A comment whose parent is not in the read hangs under the post, as a0's episode builder does:
    a parent id pointing at nothing would fail the situation's validation."""
    known = {thread.external_id} | {c.external_id for c in comments}
    root = SituationMessage(
        message_id=thread.external_id,
        parent_id=None,
        author=thread.author,
        body="\n\n".join(part for part in (thread.title, thread.body or "") if part),
        created_at=thread.created_at,
        own=False,
    )
    out = [root]
    for comment in comments:
        parent = comment.parent_external_id
        if parent is None or parent not in known:
            parent = thread.external_id
        out.append(
            SituationMessage(
                message_id=comment.external_id,
                parent_id=parent,
                author=comment.author,
                body=comment.body or "[deleted]",
                created_at=comment.created_at,
                own=False,
            )
        )
    return tuple(out)


def clip_keeping_target(
    messages: Sequence[SituationMessage], *, target_id: str, limit: int = MAX_MESSAGES
) -> tuple[tuple[SituationMessage, ...], bool]:
    """At most `limit` messages: the target, its ancestors, then the newest of the rest.
    Returns `(kept, complete)`; `complete` is False when anything was dropped."""
    by_id = {m.message_id: m for m in messages}
    if target_id not in by_id:
        raise ValueError(f"cel {target_id!r} nie jest w odczycie wątku")
    if len(messages) <= limit:
        return tuple(sorted(messages, key=lambda m: (m.created_at, m.message_id))), True
    keep: dict[str, SituationMessage] = {}
    cursor: str | None = target_id
    while cursor is not None and cursor in by_id and cursor not in keep:
        keep[cursor] = by_id[cursor]
        cursor = by_id[cursor].parent_id
    for message in sorted(messages, key=lambda m: (m.created_at, m.message_id), reverse=True):
        if len(keep) >= limit:
            break
        keep.setdefault(message.message_id, message)
    # Every kept message needs its parent in the set or it becomes a child of the root.
    root_id = next(m.message_id for m in messages if m.parent_id is None)
    if root_id not in keep:
        oldest_extra = max(
            (m for m in keep.values() if m.message_id not in _ancestors(by_id, target_id)),
            key=lambda m: (m.created_at, m.message_id),
            default=None,
        )
        if oldest_extra is not None and len(keep) >= limit:
            del keep[oldest_extra.message_id]
        keep[root_id] = by_id[root_id]
    repaired = []
    for message in sorted(keep.values(), key=lambda m: (m.created_at, m.message_id)):
        parent = message.parent_id
        if parent is not None and parent not in keep:
            message = SituationMessage(
                message_id=message.message_id,
                parent_id=root_id,
                author=message.author,
                body=message.body,
                created_at=message.created_at,
                own=message.own,
            )
        repaired.append(message)
    return tuple(repaired), False


def _ancestors(by_id: dict[str, SituationMessage], start: str) -> set[str]:
    out: set[str] = set()
    cursor: str | None = start
    while cursor is not None and cursor in by_id and cursor not in out:
        out.add(cursor)
        cursor = by_id[cursor].parent_id
    return out


def situation_hash(
    *, thread_external_id: str, target_id: str, messages: Sequence[SituationMessage]
) -> str:
    """sha256 over the conversation version the model sees; identical input → identical hash."""
    payload = {
        "thread": thread_external_id,
        "target": target_id,
        "messages": sorted(
            (m.message_id, hashlib.sha256(m.body.encode("utf-8")).hexdigest()) for m in messages
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def build_situation(
    *,
    situation_id: str,
    platform: str,
    community_display_name: str,
    language: str,
    thread: ObservedThread,
    comments: Sequence[ObservedComment],
    target_id: str,
    read_at: datetime,
    comments_truncated: bool,
    recent_own_drafts: Sequence[str] = (),
    community_exemplars: Sequence[str] = (),
    community_voice_card: Mapping[str, Any] | None = None,
    community_move_outcomes: Sequence[Mapping[str, Any]] = (),
) -> tuple[ReactionSituation, str, bool]:
    """`(situation, situation_sha256, context_complete)` for one fresh read. The account has no
    own messages on the platform yet (nothing has been published), so every pilot situation is
    a NEW_ENTRY."""
    messages = messages_from_thread(thread, comments)
    kept, complete = clip_keeping_target(messages, target_id=target_id)
    situation = ReactionSituation(
        situation_id=situation_id,
        platform=platform,
        community_display_name=community_display_name,
        language=language,
        captured_at=read_at,
        transcript=kept,
        target_message_id=target_id,
        relation=RELATION_NEW_ENTRY,
        relation_evidence="konto nie zabrało głosu w tym wątku (pilot: bez publikacji)",
        recent_own_drafts=tuple(recent_own_drafts),
        community_exemplars=tuple(community_exemplars),
        community_voice_card=community_voice_card,
        community_move_outcomes=tuple(community_move_outcomes),
    )
    # The digest covers the conversation only (thread, target, messages): what the account knows
    # about the community changes what is written, not which conversation version this is.
    digest = situation_hash(
        thread_external_id=thread.external_id, target_id=target_id, messages=kept
    )
    return situation, digest, complete and not comments_truncated
