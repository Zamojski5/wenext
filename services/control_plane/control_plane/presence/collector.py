"""What happened out there that is worth waking the agent for (P4).

THE COLLECTOR IS THE REASON THE PRODUCT CAN BE CLOSED AND STILL WORK, and it is also the reason
the bill does not run away. Every wake-up costs a model turn, so the question it answers is not
"has anything changed" but "has anything changed THAT THIS PROJECT HAS NOT ALREADY SEEN".

THREE KINDS OF EVENT, AND WHY EXACTLY THESE:

  * `NEW_THREAD`: somebody posted in a community the delegation names. This is the one that makes
    the agent useful while nobody is looking, and D-030 is why it matters that it is noticed
    quickly: a reply is worth something while the post is still on `new`.
  * `REPLY`: somebody answered one of OUR published comments. A conversation we started and then
    ignored is worse than one we never started.
  * `MODERATION`: something we published stopped being publicly visible. P0 made the system able
    to tell that apart from success; P4 makes the agent hear about it.

WHAT MAKES A KEY. `<kind>:<platform id>`, and nothing else. Not the text, not the timestamp: the
same post read twice, or read by two collectors at once, has to produce the same key, or the
UNIQUE constraint in `wakeup_events` cannot do its job. `known_event_keys` here is planning, not
deduplication, and this module must never be read as if it were the other way round: two ticks
overlapping will both build the same key, both call `record_event`, and exactly one will win.

WHAT IT DOES NOT DO: decide whether the agent acts. A wake-up with three events may still end in
"nothing worth doing", and that is a legitimate answer which `handlers.py` records as the
wake-up's `skipped_reason`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncpg

from .store import DelegationRow

#: How many events one tick carries at most. A community that got two hundred new posts overnight
#: is not two hundred reasons to think; it is one reason to think, with the newest posts in hand.
#: The rest are NOT recorded, so they can still become events on a later tick rather than being
#: silently marked seen.
MAX_EVENTS_PER_TICK = 12

#: Ile wierszy wolno PRZECZYTAĆ, zanim zadziała deduplikacja. Osobna liczba od limitu tiku i
#: większa od niego, bo zapytanie z `LIMIT 12` obcinało WCZEŚNIEJ, niż odsiewały klucze: przy
#: trzynastu nowych wątkach drugi tik dostawał te same dwanaście, uznawał je za znane i kończył
#: ciszą, a trzynasty nie docierał do modelu nigdy. Audyt z 19.09 zmierzył to wprost.
#:
#: Ośmiokrotność, bo to ma wystarczyć na kilka tików zaległości i nadal być zapytaniem, które
#: kończy się w milisekundach na indeksie.
SCAN_LIMIT = MAX_EVENTS_PER_TICK * 8

KIND_NEW_THREAD = "NEW_THREAD"
KIND_REPLY = "REPLY"
KIND_MODERATION = "MODERATION"


@dataclass(frozen=True)
class Event:
    """One thing that happened, in the form `wakeup_events` stores."""

    kind: str
    event_key: str
    payload: dict[str, Any] = field(default_factory=dict)


class EventCollector:
    """Reads the observation tables for a project and says what is new."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def collect(
        self, delegation: DelegationRow, *, since: datetime, seen: set[str] | None = None
    ) -> tuple[Event, ...]:
        """Events for this delegation's project, newest first, already filtered by `seen`.

        `since` bounds the READ, `seen` bounds the RESULT, and they are not the same guard. The
        window keeps the queries cheap; the keys keep a post that was observed inside the window
        twice from producing two wake-ups. A tick with a wide window and an empty `seen` is
        correct, just expensive, which is the direction a mistake here should fall.
        """
        known = seen if seen is not None else set()
        events: list[Event] = []
        async with self._pool.acquire() as conn:
            events.extend(await self._outcomes(conn, delegation, since=since))
            events.extend(await self._new_threads(conn, delegation, since=since))
            events.extend(await self._replies(conn, delegation, since=since))
            events.extend(await self._x_replies(conn, delegation, since=since))
            events.extend(await self._moderation(conn, delegation, since=since))
        # NIE PRZYCINAMY TUTAJ do limitu tiku. Kandydaci idą dalej w całości, a limit nakłada
        # `handlers.py` DOPIERO PO odsianiu tych, które już doszły do modelu. Przycięcie w tym
        # miejscu było drugim pół-błędem z audytu 19.09: przy trzynastu nowych wątkach lista
        # wracała obcięta do dwunastu ZNANYCH, handler odsiewał wszystkie i kończył ciszą, choć
        # trzynasty czekał nietknięty. Kolejność „najpierw odsiej, potem przytnij" jest jedyną,
        # w której nadmiar zostaje na następny tik zamiast znikać.
        return tuple(event for event in events if event.event_key not in known)

    async def _new_threads(
        self, conn: asyncpg.Connection, delegation: DelegationRow, *, since: datetime
    ) -> list[Event]:
        if not delegation.scope.communities:
            return []
        rows = await conn.fetch(
            """
            SELECT t.external_id, t.title, t.url, c.community_key, t.created_at_source
              FROM observed_threads t
              JOIN communities c ON c.id = t.community_id
             WHERE t.organization_id = $1 AND t.workspace_id = $2
               AND c.community_key = ANY($3::text[])
               AND t.first_observed_at >= $4
             -- Porządek jest STABILNY: przy tej samej sekundzie obserwacji sam czas nie
             -- rozstrzyga, a niestabilna kolejność znaczy, że dwa tiki widzą inne dwanaście.
             ORDER BY t.created_at_source DESC, t.external_id
             LIMIT $5
            """,
            delegation.organization_id,
            delegation.workspace_id,
            list(delegation.scope.communities),
            since,
            SCAN_LIMIT,
        )
        return [
            Event(
                KIND_NEW_THREAD,
                f"{KIND_NEW_THREAD}:t3_{row['external_id']}",
                {
                    "spolecznosc": row["community_key"],
                    "tytul": row["title"],
                    "adres": row["url"],
                    "powstal": row["created_at_source"].isoformat(),
                },
            )
            for row in rows
        ]

    async def _replies(
        self, conn: asyncpg.Connection, delegation: DelegationRow, *, since: datetime
    ) -> list[Event]:
        """Comments whose parent is one of OUR published comments in this project.

        The join runs through `execution_evidence`, which is where the platform's id of what we
        actually posted is written, and through `publication_requests` to reach the project. A
        reply to a PILOT comment is not this project's event, and the `project_id` filter is what
        keeps the two apart now that both live in one queue.
        """
        rows = await conn.fetch(
            """
            SELECT oc.external_id, oc.author, oc.body, oc.created_at_source,
                   e.external_id AS ours
              FROM observed_comments oc
              JOIN execution_evidence e
                -- The evidence holds Reddit's FULLNAME of our comment (`t1_abc`), while
                -- `observed_comments` holds bare ids on both sides of the parent link. Stripping
                -- the prefix here is what makes this join find anything at all.
                ON oc.parent_external_id = regexp_replace(e.external_id, '^t[13]_', '')
              JOIN publication_requests q ON q.attempt_id = e.attempt_id
             WHERE q.organization_id = $1 AND q.project_id = $2
               AND oc.organization_id = $1
               AND oc.observed_at >= $3
             ORDER BY oc.created_at_source DESC, oc.external_id
             LIMIT $4
            """,
            delegation.organization_id,
            delegation.project_id,
            since,
            SCAN_LIMIT,
        )
        return [
            Event(
                KIND_REPLY,
                f"{KIND_REPLY}:t1_{row['external_id']}",
                {
                    "autor": row["author"],
                    "pod_naszym": row["ours"],
                    "fragment": (row["body"] or "")[:280],
                    "powstal": row["created_at_source"].isoformat(),
                },
            )
            for row in rows
        ]

    async def _x_replies(
        self, conn: asyncpg.Connection, delegation: DelegationRow, *, since: datetime
    ) -> list[Event]:
        """Replies under OUR posts on X, read from the publisher's author-side observation.

        Measured 27.09: a reply came under the project's X post and nothing woke the agent,
        because REPLY events were built from `observed_comments`, which only Reddit fills. The
        X reader already records who answered and what (`raw->'odpowiedzi'` of each AUTHOR
        observation, `pilot/publisher.py::_observe_x`); this turns each new one into an event.
        Our own account's answers in the conversation are not replies to us. The key is the
        reply's X id, so the same reply seen in five hourly reads wakes the agent once.
        """
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (r ->> 'id')
                   r ->> 'id' AS reply_id, r ->> 'autor' AS author, r ->> 'tekst' AS body,
                   e.permalink AS ours, o.observed_at
              FROM publication_observations o
              JOIN publication_requests q ON q.id = o.request_id
              JOIN execution_evidence e ON e.attempt_id = q.attempt_id
             CROSS JOIN LATERAL jsonb_array_elements(
                   CASE WHEN jsonb_typeof(o.raw -> 'odpowiedzi') = 'array'
                        THEN o.raw -> 'odpowiedzi' ELSE '[]'::jsonb END) AS r
             WHERE q.organization_id = $1 AND q.project_id = $2
               AND q.target_external_id LIKE 'x:%'
               AND o.viewer = 'AUTHOR'
               AND o.observed_at >= $3
               AND r ->> 'id' ~ '^[0-9]{1,25}$'
               AND lower(coalesce(r ->> 'autor', '')) <> lower(coalesce(e.account, ''))
             ORDER BY r ->> 'id', o.observed_at
             LIMIT $4
            """,
            delegation.organization_id,
            delegation.project_id,
            since,
            SCAN_LIMIT,
        )
        return [
            Event(
                KIND_REPLY,
                f"{KIND_REPLY}:x:{row['reply_id']}",
                {
                    "platforma": "X",
                    "autor": f"@{row['author']}" if row["author"] else None,
                    "fragment": (row["body"] or "")[:280],
                    "pod_naszym_postem": row["ours"],
                    "odpowiedz": (
                        f"https://x.com/{row['author']}/status/{row['reply_id']}"
                        if row["author"]
                        else None
                    ),
                },
            )
            for row in rows
        ]

    async def _moderation(
        self, conn: asyncpg.Connection, delegation: DelegationRow, *, since: datetime
    ) -> list[Event]:
        """Publications of this project that are no longer visible to anyone else.

        `public_visibility = REMOVED` is P0's answer, and it is deliberately the only one used
        here: UNVERIFIED means we could not check, and waking the agent up to tell it that we do
        not know would spend a model turn on nothing.
        """
        rows = await conn.fetch(
            """
            SELECT id, target_url, removal_source, removal_reported_at
              FROM publication_requests
             WHERE organization_id = $1 AND project_id = $2
               AND public_visibility = 'REMOVED'
               AND coalesce(removal_reported_at, public_visibility_checked_at) >= $3
             ORDER BY coalesce(removal_reported_at, public_visibility_checked_at) DESC, id
             LIMIT $4
            """,
            delegation.organization_id,
            delegation.project_id,
            since,
            SCAN_LIMIT,
        )
        return [
            Event(
                KIND_MODERATION,
                f"{KIND_MODERATION}:{row['id']}",
                {
                    "wniosek": str(row["id"]),
                    "adres": row["target_url"],
                    "zrodlo": row["removal_source"],
                },
            )
            for row in rows
        ]

    async def _outcomes(
        self, conn: asyncpg.Connection, delegation: DelegationRow, *, since: datetime
    ) -> list[Event]:
        """One wake-up per meaningful outcome kind, not per vote or repeated sweep.

        The cheap observation pipeline remains independent of the model. Replies themselves
        still arrive through REPLY; this also exposes aggregate feedback on our own posts.
        """
        from control_plane.knowledge.experience import measure_one, outcome_of

        rows = await conn.fetch(
            "SELECT DISTINCT q.id FROM publication_requests q "
            "JOIN publication_observations o ON o.request_id=q.id "
            "WHERE q.organization_id=$1 AND q.workspace_id=$2 AND q.project_id=$3 "
            "AND q.status='PUBLISHED' AND o.observed_at >= $4 "
            "ORDER BY q.id LIMIT $5",
            delegation.organization_id,
            delegation.workspace_id,
            delegation.project_id,
            since,
            SCAN_LIMIT,
        )
        events = []
        for row in rows:
            measured = await measure_one(
                self._pool,
                organization_id=delegation.organization_id,
                request_id=row["id"],
                connection=conn,
            )
            if measured is None:
                continue
            outcome = outcome_of(measured)
            if outcome is None or outcome["kind"] == "REMOVED":
                continue
            events.append(
                Event(
                    "OUTCOME",
                    f"OUTCOME:{row['id']}:{outcome['kind']}",
                    {
                        "publication": str(row["id"]),
                        "community": measured.community_key,
                        "url": measured.target_url,
                        "outcome": outcome,
                        "meaning": (
                            "Wynik publikacji do oceny, nie polecenie napisania kolejnego posta."
                        ),
                    },
                )
            )
        return events
