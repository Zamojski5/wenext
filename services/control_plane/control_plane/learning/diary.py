"""The founder's post log as the seed of the learning loop (13.10).

`DZIENNIK-POSTOW.md` is a table the founder's agent kept by hand from 11.10: date, channel, format,
hook, the start of the text, a link or an id, and what the post got after about 1, 24 and 72 hours.
Most of what it lists is also in the database (the card published it), and for those items the
diary adds the two things the database does not have: the HOOK the agent chose when it wrote the
draft, and the follower count of the account it answered, as it stood in the log. Items the
database does not know (the 12.10 film was posted by hand) are imported as `origin = DIARY`, and
the diary's readings become snapshots tagged `DIARY`.

NOTHING IS INVENTED. A reading is taken only when the cell says its numbers and the age they were
taken at is stated (`ok. 18 h`) or is the column's own ("1 h"); a cell that says its reading was
earlier without saying when ("wcześniejszy odczyt") is skipped, and so is a number belonging to
another post ("Odpowiedź z linkiem pod nim: 22 wyśw."). A seed reading fills a horizon only when
it is a real reading of that horizon (`strict_horizon_of`: the age within a stated tolerance of the
nominal one), and never takes the place of a snapshot the sweep can still take itself.

THE ROWS ARE A SPECIFIC TABLE, not a markup language. The parser reads the eleven columns of that
file by position and says what it skipped; if the file's shape changes, it reports rows it could not
read instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from . import model
from .store import (
    KIND_OWN_POST,
    KIND_REPLY,
    ORIGIN_DIARY,
    PLATFORM_REDDIT,
    PLATFORM_X,
    LearningStore,
    NewPost,
    PostRow,
)

#: The nominal age of the three result columns, in minutes.
COLUMN_AGES = (60, 24 * 60, 72 * 60)

_ROW = re.compile(r"^\|\s*(\d+)\s*\|")
_STATUS_URL = re.compile(r"https://(?:www\.)?x\.com/([A-Za-z0-9_]{1,30})/status/(\d{1,25})")
_X_ID = re.compile(r"\bx:(\d{1,25})\b")
_REDDIT_ID = re.compile(r"\b(t[13]_[a-z0-9]{1,12})\b")
_FOLLOWERS = re.compile(r"\((\d+(?:[.,]\d+)?)\s*([kKmM])\)")
_TARGET = re.compile(r"\bdo\s+@([A-Za-z0-9_]{1,30})")
_AGE = re.compile(r"ok\.\s*(\d+(?:[.,]\d+)?)\s*h\b")
_COUNTERS = {
    "views": re.compile(r"(\d+)\s*wyśw"),
    "likes": re.compile(r"(\d+)\s*lajk"),
    "replies": re.compile(r"(\d+)\s*odp"),
    "reposts": re.compile(r"(\d+)\s*repost"),
}

#: Where the seed's tolerance is tighter than the sweep's own windows: a reading someone ELSE made
#: is only as good as its age, so it counts for a horizon only within 0.75x to 1.5x of it.
SEED_TOLERANCE = (0.75, 1.5)


def strict_horizon_of(age_minutes: int) -> int | None:
    for window in model.WINDOWS:
        low = window.due_minutes * SEED_TOLERANCE[0]
        high = window.due_minutes * SEED_TOLERANCE[1]
        if low <= age_minutes <= high:
            return window.hours
    return None


@dataclass(frozen=True)
class DiaryReading:
    age_minutes: int
    views: int | None = None
    likes: int | None = None
    replies: int | None = None
    reposts: int | None = None
    #: The age is the column's nominal one, not one the cell states.
    approximate: bool = False


@dataclass(frozen=True)
class DiaryRow:
    number: int
    channel: str
    fmt: str
    hook: str | None
    text_start: str
    is_draft: bool
    platform: str | None
    kind: str
    #: OUR item's id on the platform (`x:<id>`, `t1_<id>`) when the row is a published item.
    own_id: str | None = None
    #: The id of the post the row's reply answers, for a draft row (`szkic, x:<id>`).
    target_id: str | None = None
    own_url: str | None = None
    target_handle: str | None = None
    followers: int | None = None
    readings: tuple[DiaryReading, ...] = ()
    skipped: tuple[str, ...] = ()


def _followers(text: str) -> int | None:
    found = _FOLLOWERS.search(text)
    if not found:
        return None
    value = float(found.group(1).replace(",", "."))
    return round(value * (1_000_000 if found.group(2).lower() == "m" else 1_000))


def parse_cell(cell: str, nominal: int) -> tuple[list[DiaryReading], list[str]]:
    """The readings in one result cell, and what was skipped and why."""
    text = cell.strip()
    if not text:
        return [], []
    if "USUNI" in text.upper():
        return [], ["removed by moderation: no numbers"]
    readings: list[DiaryReading] = []
    skipped: list[str] = []
    for segment in re.split(r"\.\s+(?=Pomiar|Odpowiedź z linkiem)", text):
        if segment.startswith("Odpowiedź z linkiem"):
            skipped.append("numbers of another post (a reply under it)")
            continue
        if "wcześniejszy odczyt" in segment:
            skipped.append("earlier reading with no stated age")
            continue
        counters = {name: _first_int(pattern, segment) for name, pattern in _COUNTERS.items()}
        if all(value is None for value in counters.values()):
            continue
        stated = _AGE.search(segment)
        age = round(float(stated.group(1).replace(",", ".")) * 60) if stated else nominal
        readings.append(DiaryReading(age_minutes=age, approximate=stated is None, **counters))
    return readings, skipped


def _first_int(pattern: re.Pattern[str], text: str) -> int | None:
    found = pattern.search(text)
    return int(found.group(1)) if found else None


def parse_row(line: str) -> DiaryRow | None:
    if not _ROW.match(line):
        return None
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if len(cells) < 10:
        return None
    number, _date, channel, fmt, hook_cell, text_start, link = cells[:7]
    result_cells = cells[7:10]
    folded_link = link.lower()
    is_draft = "szkic" in fmt.lower() or folded_link.startswith("szkic")
    lowered = channel.lower()
    if lowered.startswith("reddit"):
        platform, kind = PLATFORM_REDDIT, KIND_REPLY
    elif lowered.startswith("x"):
        platform = PLATFORM_X
        kind = KIND_REPLY if "reply" in lowered else KIND_OWN_POST
    else:
        platform, kind = None, KIND_REPLY
    first_word = re.match(r"[A-Za-z]+", hook_cell)
    hook = first_word.group(0).upper() if first_word else None
    if hook not in model.HOOKS or hook == "INNE":
        hook = None
    own_id = target_id = own_url = None
    status = _STATUS_URL.search(link)
    if status:
        own_id, own_url = f"x:{status.group(2)}", status.group(0)
    elif platform == PLATFORM_REDDIT:
        reddit = _REDDIT_ID.search(link)
        own_id = reddit.group(1) if reddit else None
    else:
        ident = _X_ID.search(link)
        if ident and is_draft:
            target_id = f"x:{ident.group(1)}"
        elif ident:
            own_id = f"x:{ident.group(1)}"
    target = _TARGET.search(text_start)
    readings: list[DiaryReading] = []
    skipped: list[str] = []
    for cell, nominal in zip(result_cells, COLUMN_AGES, strict=True):
        found, why = parse_cell(cell, nominal)
        readings.extend(found)
        skipped.extend(why)
    return DiaryRow(
        number=int(number),
        channel=channel,
        fmt=fmt,
        hook=hook,
        text_start=text_start,
        is_draft=is_draft,
        platform=platform,
        kind=kind,
        own_id=own_id,
        target_id=target_id,
        own_url=own_url,
        target_handle=target.group(1) if target else None,
        followers=_followers(text_start),
        readings=tuple(readings),
        skipped=tuple(skipped),
    )


def parse_diary(text: str) -> list[DiaryRow]:
    """Every row of the diary's table that could be read, in file order."""
    return [row for row in (parse_row(line) for line in text.splitlines()) if row is not None]


def text_prefix(text_start: str) -> str:
    """The start of a post's text as the diary quotes it: no quotes, no trailing ellipsis."""
    cleaned = text_start.strip().strip('"').strip()
    return re.sub(r"(?:\.\.\.|…)$", "", cleaned).strip().strip('"').strip()


# --------------------------------------------------------------------------------------------
# Applying the diary
# --------------------------------------------------------------------------------------------


@dataclass
class DiaryReport:
    rows: int = 0
    labelled: int = 0
    followers_set: int = 0
    imported: int = 0
    snapshots: int = 0
    notes: list[str] = field(default_factory=list)


def _seed_allowed(post: PostRow, hours: int, age: int, now: datetime) -> bool:
    """May this seed reading take the post's snapshot for `hours`?

    Yes when the reading is at or after the horizon's own due age (a proper reading of it), or
    when the sweep can no longer take that horizon (its window has closed). No when the sweep
    still can: a reading made at 18 h must not occupy the 24 h slot of a post that will be
    read at 24 h tomorrow.
    """
    window = model.WINDOW_OF[hours]
    if age >= window.due_minutes:
        return True
    return model.age_minutes(post.published_at, now) > window.expires_minutes


async def apply_diary(
    store: LearningStore,
    rows: list[DiaryRow],
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID | None,
    now: datetime,
    dry_run: bool = False,
) -> DiaryReport:
    """Label the tracked items from the diary, import what the database lacks, add the readings."""
    report = DiaryReport(rows=len(rows))
    for row in rows:
        if row.platform is None:
            report.notes.append(f"row {row.number}: channel {row.channel!r} not recognised")
            continue
        posts: list[PostRow] = []
        if row.own_id:
            found = await store.post_by_external(organization_id, row.platform, row.own_id)
            posts = [found] if found else []
        elif row.target_id and row.kind == KIND_REPLY:
            posts = await store.posts_by_target(organization_id, row.platform, row.target_id)
        elif row.is_draft and row.kind == KIND_OWN_POST:
            posts = await store.posts_by_body_prefix(
                organization_id, row.platform, row.kind, text_prefix(row.text_start)
            )
        if not posts and row.own_id and not row.is_draft and row.own_url:
            if dry_run:
                report.notes.append(f"row {row.number}: would import {row.own_id} (not tracked)")
                continue
            imported = await _import(store, row, organization_id, workspace_id, project_id, now)
            if imported is not None:
                posts = [imported]
                report.imported += 1
        if not posts:
            if not row.is_draft:
                report.notes.append(
                    f"row {row.number}: {row.own_id} is not tracked"
                    + (
                        " yet (a dry run does not register the published items first)"
                        if dry_run
                        else " and cannot be imported"
                    )
                )
            continue
        for post in posts:
            if dry_run:
                continue
            followers = row.followers if post.target_followers is None else None
            if row.hook or followers is not None:
                await store.apply_context(
                    post.id,
                    hook=row.hook,
                    hook_source=model.HOOK_DIARY if row.hook else None,
                    overwrite_hook=True,
                    target_followers=followers,
                    target_author=row.target_handle,
                    facts=(
                        {"followers_source": "diary", "followers_diary_row": row.number}
                        if followers is not None
                        else {}
                    ),
                )
                report.labelled += 1 if row.hook else 0
                report.followers_set += 1 if followers is not None else 0
            if row.is_draft:
                continue  # a draft row carries no numbers of its own
            taken = await store.horizons_taken([post.id])
            for reading in row.readings:
                hours = strict_horizon_of(reading.age_minutes)
                if hours is not None and (
                    hours in taken.get(post.id, set())
                    or not _seed_allowed(post, hours, reading.age_minutes, now)
                ):
                    hours = None
                # A reading that fits no horizon (a 42 h one is not a 24 h one) is kept as evidence
                # with no horizon: it is never part of a comparison.
                if await store.record_snapshot(
                    post,
                    horizon_hours=hours,
                    measured_at=_moment(post, reading),
                    views=reading.views,
                    likes=reading.likes,
                    replies=reading.replies,
                    reposts=reading.reposts,
                    source="DIARY",
                    raw={
                        "diary_row": row.number,
                        "approximate_age": reading.approximate,
                    },
                ):
                    report.snapshots += 1
                    if hours is not None:
                        taken.setdefault(post.id, set()).add(hours)
        for reason in row.skipped:
            report.notes.append(f"row {row.number}: skipped, {reason}")
    return report


def _moment(post: PostRow, reading: DiaryReading) -> datetime:
    return post.published_at + timedelta(minutes=reading.age_minutes)


async def _import(
    store: LearningStore,
    row: DiaryRow,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID | None,
    now: datetime,
) -> PostRow | None:
    """A post the database does not know, from the diary: an own post with its address."""
    from control_plane.pilot.send_checks import x_created_at

    assert row.own_id is not None and row.own_url is not None and row.platform is not None
    if row.platform != PLATFORM_X:
        return None
    published = x_created_at(None, row.own_id.removeprefix("x:"))
    if published is None:
        return None
    text = text_prefix(row.text_start)
    new = NewPost(
        organization_id=organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
        platform=PLATFORM_X,
        kind=row.kind,
        origin=ORIGIN_DIARY,
        external_id=row.own_id,
        permalink=row.own_url,
        account=_handle_of(row.own_url),
        body=text,
        published_at=published,
        hook=row.hook,
        hook_source=model.HOOK_DIARY if row.hook else None,
        mentions_wenext=model.mentions_wenext(text),
        has_link=model.has_link(text) or "link" in row.fmt.lower(),
        facts={"diary_row": row.number, "imported_at": now.isoformat(), "body_is_a_prefix": True},
    )
    await store.register(new)
    return await store.post_by_external(organization_id, PLATFORM_X, row.own_id)


def _handle_of(url: str) -> str | None:
    found = _STATUS_URL.search(url)
    return found.group(1) if found else None


def summary(report: DiaryReport) -> dict[str, Any]:
    return {
        "rows": report.rows,
        "labelled": report.labelled,
        "followers_set": report.followers_set,
        "imported": report.imported,
        "snapshots": report.snapshots,
        "notes": report.notes,
    }
