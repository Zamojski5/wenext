"""The look the publisher takes again at the moment of a scheduled reply, as arithmetic and words (v11).

No database here (`tests/test_scheduled_checks_db.py` runs the publisher through it). Held:

  * THE AGE LIMIT: only a request with a `not_before` is ever judged by it, only when the target's age
    is known, X at 6 hours and Reddit at 48 by default, each configurable through the environment, and
    a value that is not a positive number falls back to the default instead of switching the check off;
  * HOW OLD A TARGET IS: the Reddit post's `created_utc`, the X page's timestamp, else the snowflake in
    an X status id;
  * THE REASONS: the publisher's sentences (Polish prose) have stable codes for the card, and every
    sentence this module claims to recognise is really in the publisher's source, so a rewording there
    cannot silently turn a code into `None`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from control_plane.pilot import send_checks as sc
from control_plane.pilot.publication import PublicationRequest

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
PILOT = Path(sc.__file__).resolve().parent


def _request(not_before: datetime | None) -> PublicationRequest:
    return PublicationRequest(
        id=uuid4(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
        run_id=None,
        reaction_id=None,
        project_id=uuid4(),
        delegation_id=uuid4(),
        kind="REPLY",
        proposal_revision_id=uuid4(),
        approval_decision_id=uuid4(),
        intent_id=None,
        attempt_id=None,
        position=1,
        status="QUEUED",
        consent_source="x",
        draft_sha256="a" * 64,
        target_external_id="t3_abc",
        target_url="https://www.reddit.com/r/x/comments/abc/t/",
        queued_at=NOW - timedelta(hours=3),
        not_before=not_before,
        last_checked_at=None,
        detail=None,
        published_at=None,
        public_visibility="UNVERIFIED",
        public_visibility_checked_at=None,
        removal_reported_at=None,
        removal_source=None,
    )


def _stale(on_x: bool, age: timedelta, *, scheduled: bool = True, environ=None):
    return sc.stale_detail(
        _request(NOW - timedelta(minutes=1) if scheduled else None),
        on_x=on_x,
        created_at=NOW - age,
        now=NOW,
        environ=environ or {},
    )


def test_defaults_are_eight_hours_on_x_and_forty_eight_on_reddit() -> None:
    assert sc.max_age(True, {}) == timedelta(hours=8) == sc.MAX_AGE_X
    assert sc.max_age(False, {}) == timedelta(hours=48) == sc.MAX_AGE_REDDIT


def test_a_scheduled_reply_is_stale_just_past_the_limit_and_not_at_it() -> None:
    assert _stale(True, timedelta(hours=8)) is None
    assert _stale(True, timedelta(hours=8, minutes=1)) is not None
    assert _stale(False, timedelta(hours=48)) is None
    assert _stale(False, timedelta(hours=48, minutes=1)) is not None
    # X and Reddit differ: nine hours is cold on X and young on Reddit
    assert (
        _stale(True, timedelta(hours=9)) is not None and _stale(False, timedelta(hours=9)) is None
    )


def test_the_sentence_says_how_old_the_limit_the_platform_and_that_the_text_is_unchanged() -> None:
    text = _stale(True, timedelta(hours=9, minutes=20))
    assert text is not None
    assert "ma 9 h" in text and "najwyżej 8 h (X)" in text and "tekst nie został zmieniony" in text
    assert "(Reddit)" in (_stale(False, timedelta(hours=70)) or "")
    assert not LONG_DASHES.search(text)
    assert sc.reason_code(text) == sc.REASON_STALE


def test_a_request_that_was_not_scheduled_is_never_judged_by_age() -> None:
    assert _stale(True, timedelta(days=30), scheduled=False) is None
    assert _stale(False, timedelta(days=30), scheduled=False) is None


def test_an_age_that_is_not_known_is_not_called_stale_on_a_guess() -> None:
    assert sc.stale_detail(_request(NOW), on_x=True, created_at=None, now=NOW, environ={}) is None


def test_the_limits_are_configurable_and_a_bad_value_falls_back() -> None:
    env = {sc.ENV_MAX_AGE_X_HOURS: "2", sc.ENV_MAX_AGE_REDDIT_HOURS: "100"}
    assert sc.max_age(True, env) == timedelta(hours=2)
    assert sc.max_age(False, env) == timedelta(hours=100)
    assert _stale(True, timedelta(hours=3), environ=env) is not None
    assert _stale(False, timedelta(hours=60), environ=env) is None
    for bad in ("", "0", "-5", "abc", "nan", "inf", "1e999"):
        assert sc.max_age(True, {sc.ENV_MAX_AGE_X_HOURS: bad}) == sc.MAX_AGE_X, bad
        assert sc.max_age(False, {sc.ENV_MAX_AGE_REDDIT_HOURS: bad}) == sc.MAX_AGE_REDDIT, bad


def test_how_old_a_reddit_post_is_comes_from_created_utc() -> None:
    stamp = (NOW - timedelta(hours=5)).timestamp()
    assert sc.reddit_created_at({"created_utc": stamp}) == NOW - timedelta(hours=5)
    assert sc.reddit_created_at({"created_utc": "x"}) is None
    assert sc.reddit_created_at({}) is None


def test_how_old_an_x_post_is_comes_from_the_page_then_from_the_snowflake() -> None:
    page = (NOW - timedelta(hours=3)).isoformat()
    assert sc.x_created_at(page, "123") == NOW - timedelta(hours=3)
    assert sc.x_created_at(page.replace("+00:00", "Z"), "123") == NOW - timedelta(hours=3)
    # a status id is a snowflake: the milliseconds since X's epoch sit in its high bits
    moment = NOW - timedelta(hours=7)
    status_id = str((int(moment.timestamp() * 1000) - sc._X_EPOCH_MS) << 22)
    got = sc.x_created_at(None, status_id)
    assert got is not None and abs((got - moment).total_seconds()) < 1
    assert sc.x_created_at("not a date", status_id) == got, "a bad page date falls back"
    assert sc.x_created_at(None, "x:abc") is None


# ---------------------------------------------------------------------------
# The reasons
# ---------------------------------------------------------------------------

#: Every sentence the publisher writes about a request that did not go out, as it writes it.
SENTENCES = {
    "wątek zablokowany": sc.REASON_LOCKED,
    "wątek zarchiwizowany": sc.REASON_LOCKED,
    "komentarz docelowy t1_k1 zniknął": sc.REASON_GONE,
    "wątek zniknął z Reddita": sc.REASON_GONE,
    "brak wątku albo community": sc.REASON_GONE,
    "post X 2103587050346250341 jest niedostępny (deleted)": sc.REASON_GONE,
    "na tym wątku jest już komentarz naszego konta; nowa reakcja = nowa zgoda": sc.REASON_REPLIED,
    "pod tym postem X jest już nasza odpowiedź; nowa reakcja = nowa zgoda": sc.REASON_REPLIED,
    "ta sama treść już stoi jako nasz post na X; nowy post = nowa zgoda": sc.REASON_REPLIED,
    "brak sesji X konta @a: ekran logowania": sc.REASON_SESSION,
    "brak sesji X przy odczycie posta: nie ma": sc.REASON_SESSION,
    "sesja czytnika niedostępna: x": sc.REASON_SESSION,
    "nie udało się sprawdzić sesji X konta @a (to nie jest wylogowanie): t; wniosek czeka": (
        sc.REASON_SESSION_UNKNOWN
    ),
    "sesja w kapsule to ktoś, zgoda dotyczy konta a: kolejka zatrzymana": sc.REASON_OTHER_ACCOUNT,
    "przeglądarka agenta jest zalogowana na X jako @b, zgoda dotyczy @a": sc.REASON_OTHER_ACCOUNT,
    "tempo: 1/1 w ostatniej godzinie, 3/6 w ostatniej dobie": sc.REASON_PACE,
    "kapsuła nie odpowiada na /state": sc.REASON_CAPSULE,
    "brak aktuatora (WENEXT_BROWSER_WRITER_TOKEN nieskonfigurowane)": sc.REASON_CAPSULE,
}


@pytest.mark.parametrize(("sentence", "code"), sorted(SENTENCES.items()))
def test_the_publishers_sentences_have_stable_codes(sentence: str, code: str) -> None:
    assert sc.reason_code(sentence) == code


def test_a_sentence_nobody_taught_this_module_has_no_code_and_none_is_empty() -> None:
    assert sc.reason_code("odczyt posta X nie powiódł się: coś") is None
    assert sc.reason_code("") is None and sc.reason_code(None) is None


def test_every_recognised_sentence_is_really_in_the_publisher_so_a_rewording_cannot_orphan_a_code() -> (
    None
):
    source = ((PILOT / "publisher.py").read_text() + (PILOT / "send_checks.py").read_text()).lower()
    for needle, _code in sc._PATTERNS:
        assert needle in source, f"„{needle}” nie stoi w publikatorze: kod nie ma czego rozpoznać"
    for sentence in SENTENCES:
        assert not LONG_DASHES.search(sentence)
