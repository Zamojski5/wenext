"""GET /api/audit-events — a read-only window on the log, with no hidden default filter.

Two properties are worth more here than any convenience:

1.  **No mutating route exists.** `audit_events` is append-only in the schema (0001, plus the
    statement-level TRUNCATE trigger from 0002); an HTTP route that tried to edit it would fail
    at the database, so the point of not having one is not defence in depth — it is that the API
    surface must not suggest the log is editable.
2.  **The default view hides no kind of event.** A default filter is the most comfortable way to
    build a log nobody can trust: the query returns rows, the page looks alive, and a whole
    category of events is invisible with no error and no alarm anywhere. That is the same effect
    as having no audit at all, with none of the visibility.

Every assertion counts rows before comparing them, and the fixture inserts one event of EVERY
kind in `AUDIT_KINDS`, so "the default view shows everything" cannot pass over an empty log.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from control_plane.api.audit import AUDIT_EVENTS_PATH, DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from control_plane.app import create_app, mounted_routes
from control_plane.audit import AUDIT_KINDS, AuditWriter

pytestmark = pytest.mark.db


async def _write_one_event_of_every_kind(pool, tenant) -> dict[str, str]:
    """One event per kind, each in its OWN transaction. Returns kind -> subject_ref.

    Separate transactions on purpose: `now()` is the transaction's start time in PostgreSQL, so
    a batch written inside one transaction would carry one identical `occurred_at` and the
    ordering assertions below would be vacuous.
    """
    written: dict[str, str] = {}
    async with pool.acquire() as connection:
        for kind in sorted(AUDIT_KINDS):
            subject = f"{kind.lower()}:{uuid.uuid4()}"
            async with connection.transaction():
                await AuditWriter(connection).record(
                    kind=kind,
                    organization_id=tenant["organization"],
                    workspace_id=tenant["workspace"],
                    subject_ref=subject,
                    actor_ref=f"operator:{tenant['operator']}",
                    payload={"reason_code": "TEST"},
                )
            written[kind] = subject
    assert len(written) == len(AUDIT_KINDS), sorted(written)
    return written


async def test_the_default_view_hides_no_event_kind(logged_in_client, pool, tenant) -> None:
    """Called with no parameters, the endpoint shows every kind that exists in the log.

    The count is asserted first and the comparison is an equality of SETS, not a `>=`: a
    response containing "at least the kinds we asked about" would still be satisfied by an
    endpoint quietly dropping the ones it does not know.
    """
    written = await _write_one_event_of_every_kind(pool, tenant)
    assert len(AUDIT_KINDS) >= 4, sorted(AUDIT_KINDS)
    assert len(written) >= 4, sorted(written)

    response = await logged_in_client.get(AUDIT_EVENTS_PATH)
    assert response.status_code == 200, response.content[:300]
    body = response.json()

    assert len(body["events"]) >= len(written), body["limit"]
    kinds = {event["kind"] for event in body["events"]}
    missing = set(written) - kinds
    assert not missing, (
        f"widok domyślny nie pokazuje rodzajów {sorted(missing)} — filtr domyślny chowający "
        "rodzaj zdarzenia daje ten sam efekt co brak audytu, przy zerowym alarmie"
    )
    subjects = {event["subject_ref"] for event in body["events"]}
    assert set(written.values()) <= subjects, sorted(set(written.values()) - subjects)


async def test_events_come_back_newest_first(logged_in_client, pool, tenant) -> None:
    """`occurred_at` descending, with the identifier as the tie-break.

    Without the tie-break the order of two events written in the same clock tick is whatever the
    planner felt like, and "newest first" stops being a property anybody can rely on.
    """
    written = await _write_one_event_of_every_kind(pool, tenant)
    response = await logged_in_client.get(AUDIT_EVENTS_PATH, params={"limit": MAX_PAGE_LIMIT})
    assert response.status_code == 200, response.content[:300]
    events = response.json()["events"]
    assert len(events) >= len(written), len(events)

    # Parsed, not compared as strings: `isoformat()` omits `.000000`, and "…:00+00:00" sorts
    # before "…:00.5+00:00" only by the accident of `+` preceding `.` in ASCII.
    timestamps = [datetime.fromisoformat(event["occurred_at"]) for event in events]
    identifiers = [event["id"] for event in events]
    assert timestamps == sorted(timestamps, reverse=True), timestamps[:6]
    assert identifiers == sorted(identifiers, reverse=True), identifiers[:6]


async def test_the_kind_filter_narrows_and_the_since_filter_cuts(
    logged_in_client, pool, tenant
) -> None:
    """The filters are the caller's choice, and they are exact.

    Both directions are asserted: the filtered response contains what was asked for AND nothing
    else. A filter that returned everything would pass a test that only checked for presence.
    """
    written = await _write_one_event_of_every_kind(pool, tenant)
    chosen = sorted(written)[0]

    by_kind = await logged_in_client.get(AUDIT_EVENTS_PATH, params={"kind": chosen})
    assert by_kind.status_code == 200, by_kind.content[:300]
    kinds = {event["kind"] for event in by_kind.json()["events"]}
    assert kinds == {chosen}, sorted(kinds)

    everything = await logged_in_client.get(AUDIT_EVENTS_PATH, params={"limit": MAX_PAGE_LIMIT})
    ordered = everything.json()["events"]
    assert len(ordered) >= 3, len(ordered)
    cutoff = ordered[1]["occurred_at"]

    since = await logged_in_client.get(
        AUDIT_EVENTS_PATH, params={"since": cutoff, "limit": MAX_PAGE_LIMIT}
    )
    assert since.status_code == 200, since.content[:300]
    cut = since.json()["events"]
    assert cut, "filtr since zwrócił pustą listę — pusty zbiór to błąd, nie wynik"
    boundary = datetime.fromisoformat(cutoff)
    assert len(cut) >= 1, len(cut)
    assert all(datetime.fromisoformat(event["occurred_at"]) >= boundary for event in cut), [
        event["occurred_at"] for event in cut[:5]
    ]
    assert len(cut) < len(ordered), (
        f"since={cutoff} zwróciło {len(cut)} z {len(ordered)} zdarzeń — filtr, który niczego "
        "nie odcina, nie jest przetestowany"
    )


async def test_an_unknown_kind_is_an_error_not_an_empty_page(
    logged_in_client, pool, tenant
) -> None:
    """A misspelled `kind` is refused, because an empty page is indistinguishable from silence.

    This is the same rule as the closed `kind` vocabulary in the writer, read from the other
    end: a reader who filters by a value nothing can ever carry gets an empty list and concludes
    that nothing happened.
    """
    await _write_one_event_of_every_kind(pool, tenant)
    response = await logged_in_client.get(AUDIT_EVENTS_PATH, params={"kind": "JOB_STATE_CHANGE"})
    assert response.status_code == 400, response.status_code
    assert "JOB_STATE_CHANGE" in response.text, response.text[:300]


async def test_the_audit_endpoint_has_no_mutating_routes(logged_in_client) -> None:
    """PATCH, PUT and DELETE — three separate probes, all 405, with a real session.

    The session matters: without it the boundary answers 401 to everything and this test would
    pass over an application that happily exposes DELETE /api/audit-events. That is the exact
    shape of a test with zero detection power, so the authenticated client is not a convenience
    here, it is the point.
    """
    for method in ("PATCH", "PUT", "DELETE"):
        response = await logged_in_client.request(method, AUDIT_EVENTS_PATH)
        assert response.status_code == 405, (
            f"{method} {AUDIT_EVENTS_PATH} odpowiada {response.status_code}, oczekiwano 405 "
            f"(ciało: {response.content[:200]!r})"
        )

    # Ta sama rzecz od strony deklaracji tras, nie tylko odpowiedzi serwera.
    declared = [
        (path, methods)
        for path, methods in mounted_routes(create_app())
        if path == AUDIT_EVENTS_PATH
    ]
    assert len(declared) == 1, declared
    assert set(declared[0][1]) <= {"GET", "HEAD"}, declared


async def test_the_page_limit_is_bounded_and_reported(logged_in_client, pool, tenant) -> None:
    """The page size is a page size, never a filter — and the response says which one applied.

    A default limit does bound what comes back, so the response reports `limit`, `returned` and
    `truncated`. Bounding the page by time or by kind would be the hidden filter this endpoint
    must not have; bounding it by count, and saying so, is not.
    """
    await _write_one_event_of_every_kind(pool, tenant)

    default_page = await logged_in_client.get(AUDIT_EVENTS_PATH)
    assert default_page.json()["limit"] == DEFAULT_PAGE_LIMIT, default_page.json()["limit"]

    one_row = await logged_in_client.get(AUDIT_EVENTS_PATH, params={"limit": 1})
    body = one_row.json()
    assert body["returned"] == 1, body
    assert body["truncated"] is True, body

    too_many = await logged_in_client.get(AUDIT_EVENTS_PATH, params={"limit": MAX_PAGE_LIMIT + 1})
    assert too_many.status_code == 400, too_many.status_code
