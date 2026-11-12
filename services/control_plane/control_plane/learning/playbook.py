"""What the agent reads before it drafts: the lessons, the best past replies, the tone (13.10).

ONE READ, NO MODEL, NO WRITE. `playbook.read` hands the agent three things and keeps them apart,
because they are three different kinds of statement:

  * `lekcje`: what the numbers showed, each with the groups it compared, their medians and their
    `n` (`model.py`). MEASURED, observational, from our own small history.
  * `zasady`: rules somebody stated (the founder's), kept in memory beside the lessons but marked
    as rules. Not measured, and never presented as if they were.
  * `glos`: the founder's rules for the tone of a reply (`voice.VOICE_RULES`), always there.

AND TWO LISTS OF EXAMPLES: the best replies and the best own posts so far, with their text and
their numbers. A model that sees only a rule writes the rule; one that sees what actually got read,
next to how old the post was and how big its author, writes with the evidence in front of it.

NO LESSON IS A FINDING TOO. With nothing measured yet, the answer says so in a sentence and shows
how much data there is (`pokrycie`) and what the model needs before it speaks (two groups with
`n >= 3` at one horizon). The agent is told to write from the general rules, not to guess a lesson.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from . import model
from .store import KIND_OWN_POST, KIND_REPLY, LearningStore
from .voice import VOICE_RULES

DEFAULT_TOP = 5
MAX_TOP = 10

CAVEAT = (
    "Observational and small: patterns in our own history, not causes. Use them to choose what to "
    "try next, never as a promise, and say the n when you rely on one."
)

HOW_TO_USE = (
    "Follow the lessons where they fit the post in front of you (how old it is, how big its "
    "author is, own post or reply). A lesson is a tendency from `n` items; a clearly better "
    "angle in the post itself outranks it, and the founder's rules and voice outrank every "
    "lesson: a lesson never licenses a pitch, a joke followed by the product, or an explained "
    "joke. Check the range next to a median before you trust it. With no lessons, write from "
    "the general rules and the voice. Never quote a number that is not in this answer."
)


def lesson_json(item: Any) -> dict[str, Any]:
    """One measured lesson row as the agent reads it. Numbers come from the payload, as stored."""
    payload = item.payload
    winner = payload.get("winner") or {}
    loser = payload.get("loser") or {}

    def side(group: dict[str, Any]) -> dict[str, Any]:
        return {
            "grupa": group.get("bucket"),
            "n": group.get("n"),
            "n_wyswietlen": group.get("n_views"),
            "mediana_wyswietlen": group.get("median_views"),
            "n_polubien": group.get("n_likes"),
            "mediana_polubien": group.get("median_likes"),
            "zakres_wyswietlen": group.get("range_views"),
            "zakres_polubien": group.get("range_likes"),
        }

    return {
        "id": str(item.id),
        "klucz": payload.get("lesson_key"),
        "platforma": payload.get("platform"),
        "temat": payload.get("dimension"),
        "horyzont_godzin": payload.get("horizon_hours"),
        "metryka": payload.get("metric"),
        "wniosek": item.statement,
        "lepsza": side(winner),
        "gorsza": side(loser),
        "pozostale_grupy": [side(one) for one in payload.get("others") or []],
        "zmierzono": payload.get("measured_at"),
    }


def rule_json(item: Any) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "klucz": item.payload.get("lesson_key"),
        "zasada": item.statement,
        "zrodlo": item.payload.get("source"),
        "uwaga": "To reguła, nie pomiar: nikt tego nie zmierzył.",
    }


def example_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "tekst": row["body"],
        "adres": row["permalink"],
        "platforma": row["platform"],
        "opublikowano": row["published_at"].isoformat(),
        "cel_autor": row["target_author"],
        "cel_obserwujacy": row["target_followers"],
        "wiek_celu_min": row["target_age_minutes"],
        "pozycja_odpowiedzi": row["position_replies"],
        "haczyk": row["hook"],
        "wzmianka_wenext": row["mentions_wenext"],
        "link": row["has_link"],
        "horyzont_godzin": row["horizon_hours"],
        "wiek_pomiaru_min": row["age_minutes"],
        "wyswietlenia": row["views"],
        "polubienia": row["likes"],
        "odpowiedzi": row["replies"],
        "reposty": row["reposts"],
    }


async def read_playbook(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID | None,
    platform: str | None = None,
    limit: int = DEFAULT_TOP,
) -> dict[str, Any]:
    """The playbook of one project: its lessons and rules, its best examples, its coverage."""
    from control_plane.knowledge.store import KnowledgeStore, Scope

    top = max(1, min(int(limit or DEFAULT_TOP), MAX_TOP))
    store = LearningStore(pool)
    scope = Scope(organization_id=organization_id, workspace_id=workspace_id, project_id=project_id)
    rows = await KnowledgeStore(pool).list(scope, kind="LESSON", limit=100)
    lessons: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    for row in rows:
        if row.payload.get("rule") is True:
            rules.append(rule_json(row))
        elif platform is None or row.payload.get("platform") == platform:
            lessons.append(lesson_json(row))
    # The most mature horizon first, then by key, so the order does not move between reads.
    lessons.sort(key=lambda one: (-(one["horyzont_godzin"] or 0), str(one["klucz"])))

    examples: dict[str, list[dict[str, Any]]] = {}
    for name, kind in (("najlepsze_odpowiedzi", KIND_REPLY), ("najlepsze_posty", KIND_OWN_POST)):
        examples[name] = [
            example_json(row)
            for row in await store.top_items(
                organization_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind=kind,
                platform=platform,
                limit=top,
            )
        ]
    coverage = await store.coverage(organization_id, workspace_id=workspace_id)
    result: dict[str, Any] = {
        "lekcje": lessons,
        "zasady": rules,
        "glos": VOICE_RULES,
        **examples,
        "pokrycie": coverage.to_json(),
        "jak_uzyc": HOW_TO_USE,
        "uwaga": CAVEAT,
    }
    if not lessons:
        result["brak_lekcji"] = (
            "Nic jeszcze nie jest zmierzone na tyle, żeby coś twierdzić: lekcja wymaga dwóch grup "
            f"po co najmniej {model.MIN_N} pozycje przy tym samym horyzoncie (około 1, 6, 24 albo "
            "72 godziny po publikacji) i wyraźnej różnicy. Pisz według zasad i głosu, nie "
            "zgaduj lekcji."
        )
    return result
