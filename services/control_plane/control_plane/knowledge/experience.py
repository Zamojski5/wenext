"""What happened after we acted, written down so the next session can use it (P5 etap A).

THE MILESTONE IN ONE SENTENCE: a correction or a moderation signal has to change the next
decision. That only works if what happened is DATA, so this module turns the tables the product
already writes (`publication_requests`, `publication_observations`, `pilot_reactions`) into
`EXPERIENCE` rows with the three parts migration 0016 demands: situation, decision, outcome.

FOUR PROPERTIES, EACH BECAUSE OF A WAY THIS COULD LIE:

  1. **silence is a result, not a missing row.** A publication that got nothing after the
     threshold is recorded with `NO_REACTION`. Leaving it out would make "we never measured" and
     "nobody reacted" the same absence, and the second one is the more useful fact of the two;
  2. **nothing is measured too early.** Before the threshold, a publication with no reaction yet
     gets NO row at all, because "nothing happened after twenty minutes" is not a finding;
  3. **a new measurement never overwrites the old one.** The newer experience is written and the
     older one is superseded (`store.correct`), so the history of "it looked fine, then it was
     removed" stays readable. Overwriting would erase exactly the sequence worth learning from;
  4. **no model runs here.** The sweep is a query, a comparison and an insert. P5 says the loop
     must be cheap, and a loop that costs a model call per publication is one nobody leaves on.

WHOSE EXPERIENCE IT IS. The account that published is resolved to a person profile through the
active attachment, and the row carries `person_profile_id`. That is what keeps one person's
experience out of another person's session (the visibility rule lives in `store.py`).

WHAT THIS MODULE DOES NOT CLAIM. It records what was observed, with the moment of observation,
and never why. "Usunięte przez moderację" is what the removal report says; whether the text
deserved it is a judgement nobody here is qualified to write down.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

#: How long a publication must exist before "nothing happened" becomes a finding.
NO_REACTION_AFTER = timedelta(hours=24)

#: What counts as a reaction at all. One point is the author's own upvote on most platforms, so
#: a score of 1 with no replies is silence wearing a number.
QUIET_SCORE = 1

#: The closed vocabulary of outcomes. Closed, because a model reading these has to be able to tell
#: them apart, and a free-text outcome would be a sentence it has to interpret.
#: Widoczność potwierdzona odczytem anonimowym (`pilot/publication.py`). Tylko przy niej wolno
#: zapisać, że NIKT nie zareagował: bez niej nie wiadomo, czy było co oglądać.
VISIBILITY_CONFIRMED = "CONFIRMED"

OUTCOME_REMOVED = "REMOVED"
OUTCOME_NO_REACTION = "NO_REACTION"
OUTCOME_REPLIED = "REPLIED"
OUTCOME_SCORED = "SCORED"
OUTCOMES = (OUTCOME_REMOVED, OUTCOME_NO_REACTION, OUTCOME_REPLIED, OUTCOME_SCORED)

#: Under this key the conflict grouping of P3 would pair two experiences of ONE publication. They
#: are not a conflict, they are a history, so an experience carries its request id under its own
#: key and stays out of `about`.
REQUEST_KEY = "request_id"

#: How much of our own text travels into the payload. Enough to recognise the answer, short
#: enough that memory does not become a copy of everything ever published.
EXCERPT_CHARS = 280


@dataclass(frozen=True)
class Measured:
    """One publication with everything the sweep needs, already joined."""

    request_id: UUID
    workspace_id: UUID
    published_at: datetime | None
    target_url: str
    public_visibility: str
    removal_source: str | None
    decision: str
    move: str | None
    draft: str | None
    community_key: str | None
    platform: str | None
    thread_title: str | None
    thread_external_id: str | None
    thread_created_at: datetime | None
    account: str | None
    person_profile_id: UUID | None
    project_id: UUID | None
    observed_at: datetime | None
    still_present: bool | None
    score: int | None
    reply_count: int | None


_SELECT = """
SELECT r.id AS request_id, r.workspace_id, r.published_at, r.target_url,
       r.public_visibility, r.removal_source,
       coalesce(re.decision, r.kind) AS decision, re.move,
       coalesce(re.draft, ar.payload->>'text_and_media') AS draft,
       r.project_id AS request_project_id,
       coalesce(c.community_key, CASE WHEN r.kind = 'OWN_POST'
           THEN ar.payload->>'target_external_id' END) AS community_key,
       coalesce(c.platform, ar.payload->>'platform') AS platform,
       coalesce(t.title, CASE WHEN r.kind = 'OWN_POST'
           THEN split_part(ar.payload->>'text_and_media', E'\n\n', 1) END) AS thread_title,
       coalesce(t.external_id, CASE WHEN r.kind = 'OWN_POST'
           THEN regexp_replace(ev.external_id, '^t3_', '') END) AS thread_external_id,
       t.created_at_source AS thread_created_at,
       ev.account,
       ppa.person_profile_id,
       paa.project_id,
       obs.observed_at, obs.still_present, obs.score, obs.reply_count
  FROM publication_requests r
  -- LEFT, not INNER, and the reason is a measurement rather than caution: since migration 0017
  -- a request comes EITHER from a pilot run (run_id + reaction_id) OR from a project delegation
  -- (project_id + delegation_id), and a CHECK enforces exactly one of the two. An inner join on
  -- the reaction would silently skip every project publication, which is precisely the kind of
  -- miss that looks like "the agent never published anything" rather than like a bug.
  LEFT JOIN pilot_reactions re
    ON re.organization_id = r.organization_id
   AND re.workspace_id = r.workspace_id
   AND re.id = r.reaction_id
  LEFT JOIN action_proposal_revisions ar
    ON ar.organization_id = r.organization_id
   AND ar.workspace_id = r.workspace_id
   AND ar.id = r.proposal_revision_id
  LEFT JOIN observed_threads t
    ON t.organization_id = r.organization_id
   AND t.workspace_id = r.workspace_id
   AND (t.id = re.thread_id OR (r.reaction_id IS NULL AND r.kind = 'REPLY'
        AND t.external_id = regexp_replace(ar.payload->>'target_external_id', '^t3_', '')))
  LEFT JOIN communities c
    ON c.organization_id = t.organization_id
   AND c.id = t.community_id
  LEFT JOIN execution_evidence ev
    ON ev.organization_id = r.organization_id
   AND ev.workspace_id = r.workspace_id
   AND ev.attempt_id = r.attempt_id
  LEFT JOIN social_accounts sa
    ON sa.organization_id = r.organization_id
   AND ((ar.id IS NOT NULL AND sa.id::text = ar.payload->>'account')
        OR (ar.id IS NULL AND sa.external_account_id = ev.account AND sa.platform = c.platform))
  LEFT JOIN person_profile_accounts ppa
    ON ppa.organization_id = sa.organization_id
   AND ppa.workspace_id = r.workspace_id
   AND ppa.social_account_id = sa.id
   AND ppa.detached_at IS NULL
  LEFT JOIN project_account_assignments paa
    ON paa.organization_id = sa.organization_id
   AND paa.workspace_id = r.workspace_id
   AND paa.social_account_id = sa.id
   AND paa.unassigned_at IS NULL
   -- When the request itself names a project, that is the project this experience belongs to;
   -- the assignment is only the fallback for a pilot-run publication, which names none.
   AND (r.project_id IS NULL OR paa.project_id = r.project_id)
  LEFT JOIN LATERAL (
      SELECT o.observed_at, o.still_present, o.score, o.reply_count
        FROM publication_observations o
       WHERE o.request_id = r.id
         AND o.viewer = 'AUTHOR'
       ORDER BY o.observed_at DESC
       LIMIT 1
  ) obs ON true
 WHERE r.organization_id = $1
   AND {where}
 ORDER BY r.published_at DESC NULLS LAST
 {limit}
"""

#: The sweep looks at everything that really went out; `measure_one` looks at one request by id.
#: Two callers, one join, because a second copy of this join would drift from the first one.
_WHERE_PUBLISHED = "r.status = 'PUBLISHED'"
_WHERE_ONE = "r.id = $2"


def _row(row: asyncpg.Record) -> Measured:
    return Measured(
        request_id=row["request_id"],
        workspace_id=row["workspace_id"],
        published_at=row["published_at"],
        target_url=row["target_url"],
        public_visibility=row["public_visibility"],
        removal_source=row["removal_source"],
        # A project publication has no pilot reaction, so the decision it records is the one the
        # session made. Named rather than left empty: "PROJECT" is what happened.
        decision=row["decision"] or "PROJECT",
        move=row["move"],
        draft=row["draft"],
        community_key=row["community_key"],
        platform=row["platform"],
        thread_title=row["thread_title"],
        thread_external_id=row["thread_external_id"],
        thread_created_at=row["thread_created_at"],
        account=row["account"],
        person_profile_id=row["person_profile_id"],
        # The request's own project wins over the account's assignment: a publication made under
        # a delegation belongs to that project even when the account also works elsewhere.
        project_id=row["request_project_id"] or row["project_id"],
        observed_at=row["observed_at"],
        still_present=row["still_present"],
        score=row["score"],
        reply_count=row["reply_count"],
    )


async def measure(pool: asyncpg.Pool, *, organization_id: UUID, limit: int = 200) -> list[Measured]:
    """Every published request with its newest observation, its situation and its account."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _SELECT.format(where=_WHERE_PUBLISHED, limit="LIMIT $2"), organization_id, limit
        )
    return [_row(row) for row in rows]


async def measure_one(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    request_id: UUID,
    connection: asyncpg.Connection | None = None,
) -> Measured | None:
    """One publication, joined the same way as the sweep. `None` when there is no such request.

    The same SQL with a different WHERE, not a second query: the person's correction and the
    sweep have to see the same account, the same community and the same length, or the preference
    would be recorded against a situation nobody measured.
    """
    query = _SELECT.format(where=_WHERE_ONE, limit="LIMIT 1")
    if connection is not None:
        row = await connection.fetchrow(query, organization_id, request_id)
    else:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, organization_id, request_id)
    return _row(row) if row is not None else None


# ---------------------------------------------------------------------------
# The person's short correction, as a preference the next session can use
# ---------------------------------------------------------------------------

#: The four buttons, and what each one is a preference ABOUT. The `about` key is what makes two
#: contradicting corrections show up as a pair instead of the newer one quietly winning (P3).
VERDICTS: dict[str, dict[str, str]] = {
    "TOO_LONG": {
        "label": "za długie",
        "about": "długość wypowiedzi",
    },
    "NOT_LIKE_THIS": {
        "label": "nie w ten sposób",
        "about": "sposób odpowiedzi",
    },
    "GOOD": {
        "label": "dobrze",
        "about": "sposób odpowiedzi",
    },
    "DONT_REPLY_HERE": {
        "label": "nie odpowiadaj tu",
        "about": "udział w społeczności",
    },
}


def preference_of(
    row: Measured, verdict: str, *, note: str = "", experience_id: UUID | None = None
) -> tuple[str, dict[str, Any]]:
    """`(statement, payload)` for the person's correction of ONE publication.

    The statement is written so that it still means something read alone a month later: it names
    the community and the concrete thing being corrected, because "za długie" without a length
    and a place is an instruction nobody can apply.

    `GOOD` and `NOT_LIKE_THIS` share an `about`, deliberately: they are two answers to the same
    question, so a later "dobrze" after an earlier "nie w ten sposób" comes back as a conflict
    for the person to settle rather than as the newer one silently winning.
    """
    spec = VERDICTS[verdict]
    where = row.community_key or "tej społeczności"
    length = len(row.draft or "")
    sentences = {
        "TOO_LONG": f"Za długie: w {where} pisz krócej niż {length} znaków.",
        "NOT_LIKE_THIS": f"Nie w ten sposób: to podejście w {where} się nie sprawdziło.",
        "GOOD": f"Dobrze: tak jak w tej publikacji w {where}.",
        "DONT_REPLY_HERE": f"Nie odpowiadaj w {where}.",
    }
    statement = sentences[verdict]
    if note:
        statement = f"{statement} {note.strip()}"
    about = spec["about"]
    if verdict in ("NOT_LIKE_THIS", "GOOD", "DONT_REPLY_HERE") and row.community_key:
        about = f"{about}: {row.community_key}"
    payload = {
        "about": about,
        "verdict": verdict,
        "note": note.strip(),
        REQUEST_KEY: str(row.request_id),
        "experience_id": str(experience_id) if experience_id else None,
        "community": row.community_key,
        "account": row.account,
        "length_chars": length,
    }
    return statement, payload


def outcome_of(
    row: Measured, *, now: datetime | None = None, after: timedelta = NO_REACTION_AFTER
) -> dict[str, Any] | None:
    """What came of this publication, or `None` when there is nothing honest to say yet.

    `None` is the case that keeps this honest: before the threshold, with nothing observed and
    nothing removed, there is no finding to write. A row saying "no reaction" twenty minutes in
    would be recorded as knowledge and read later as one.

    `UNVERIFIED` TO NIE JEST DOŚWIADCZENIE, i to jest poprawka z 21.09 (P8 etap B, decyzja
    koordynatora). Do tego dnia publikacja, której nigdy nie udało się odczytać anonimowo,
    dostawała po dobie ustalenie „po N godzinach nikt nie zareagował". To zdanie mówi o CUDZYM
    zachowaniu, a zmierzony jest tylko nasz brak wiedzy: nie wiadomo nawet, czy ktokolwiek to
    widział. Następna sesja czytała to jako lekcję o społeczności i o sobie. Teraz brak reakcji
    zapisujemy wyłącznie tam, gdzie widoczność jest POTWIERDZONA; reszta zostaje bez ustalenia,
    bo „nie wiemy" jest prawdą, a „nikt nie zareagował" byłoby nieprawdą. Na żywym systemie ma
    to konkretną cenę: 38 publikacji z `UNVERIFIED` nie dostanie doświadczenia i to jest wynik,
    nie luka.

    Zaobserwowana REAKCJA jest osobnym dowodem widoczności i dlatego odpowiedzi i punkty liczą
    się niezależnie od `public_visibility`: skoro ktoś odpowiedział, to zobaczył.
    """
    moment = now or datetime.now(UTC)
    age = (
        (moment - row.published_at).total_seconds() / 3600.0
        if row.published_at is not None
        else None
    )
    observed_age = (
        (row.observed_at - row.published_at).total_seconds() / 3600.0
        if row.observed_at is not None and row.published_at is not None
        else None
    )
    measured_at = row.observed_at or row.published_at

    if row.public_visibility == "REMOVED":
        return {
            "kind": OUTCOME_REMOVED,
            "sentence": "zniknęła z platformy",
            "removal_source": row.removal_source,
            "score": row.score,
            "replies": row.reply_count,
            "measured_at": (measured_at or moment).isoformat(),
            "hours_after_publication": round(age, 2) if age is not None else None,
        }

    replies = row.reply_count or 0
    score = row.score
    if replies > 0:
        kind, sentence = OUTCOME_REPLIED, f"odpowiedziało {replies}"
    elif score is not None and score > QUIET_SCORE:
        kind, sentence = OUTCOME_SCORED, f"punkty: {score}, bez odpowiedzi"
    elif (
        age is not None
        and age >= after.total_seconds() / 3600.0
        and row.public_visibility == VISIBILITY_CONFIRMED
        and observed_age is not None
        and observed_age >= after.total_seconds() / 3600.0
    ):
        kind, sentence = (
            OUTCOME_NO_REACTION,
            f"po {int(observed_age)} godzinach nikt nie zareagował",
        )
    else:
        # Too early. Deliberately no row: see the docstring.
        return None

    return {
        "kind": kind,
        "sentence": sentence,
        "score": score,
        "replies": replies,
        "visibility": row.public_visibility,
        "measured_at": (measured_at or moment).isoformat(),
        "hours_after_publication": round(observed_age, 2) if observed_age is not None else None,
    }


def payload_of(row: Measured, outcome: dict[str, Any], *, sources: list[str] | None = None) -> dict:
    """The three parts migration 0016 requires, plus what makes them findable later.

    `situation`, `decision` and `outcome` are separate objects rather than one flat dictionary
    because the question a session asks is "what happened when we did THIS THERE" — and that
    question is only answerable when the three are not mixed together.
    """
    age_hours = None
    if row.published_at is not None and row.thread_created_at is not None:
        age_hours = round((row.published_at - row.thread_created_at).total_seconds() / 3600.0, 2)
    draft = row.draft or ""
    return {
        "situation": {
            "community": row.community_key,
            "platform": row.platform,
            "thread": row.thread_title,
            "thread_external_id": row.thread_external_id,
            "post_age_hours": age_hours,
            "move": row.move,
            "decision_kind": row.decision,
        },
        "decision": {
            "excerpt": draft[:EXCERPT_CHARS],
            "length_chars": len(draft),
            "account": row.account,
            "person_profile_id": str(row.person_profile_id) if row.person_profile_id else None,
            "sources": sources or [],
            "url": row.target_url,
        },
        "outcome": outcome,
        REQUEST_KEY: str(row.request_id),
        # Said in the data, not only in a docstring: this row was produced by a query over what
        # was observed, with no model involved.
        "measured_by": "job knowledge.experience.sweep",
    }


def statement_of(row: Measured, outcome: dict[str, Any]) -> str:
    """One readable sentence. It is what a person sees on the screen and what search matches."""
    where = row.community_key or "nieznana społeczność"
    move = f", ruch {row.move}" if row.move else ""
    length = len(row.draft or "")
    return (
        f"{where}: {row.decision}{move}, {length} znaków. "
        f"Wynik: {outcome['sentence']} (pomiar {outcome['measured_at'][:16]})."
    )


def changed(previous: dict[str, Any] | None, outcome: dict[str, Any]) -> bool:
    """Whether the new measurement says anything different from the recorded one.

    Compares the FOUR fields a reader would act on, not the whole payload: `measured_at` moves on
    every sweep, and writing a new experience every hour because the clock moved would bury the
    two or three that mean something.
    """
    if previous is None:
        return True
    old = previous.get("outcome") or {}
    return (
        old.get("kind") != outcome.get("kind")
        or old.get("score") != outcome.get("score")
        or old.get("replies") != outcome.get("replies")
        or old.get("visibility") != outcome.get("visibility")
    )
