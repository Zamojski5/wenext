"""`POST /act/post` (0023): własny wątek zakładany przez aktuator kapsuły.

Bez Chrome: `FakeActuator` stoi w miejscu CDP, więc te testy mierzą to, czego endpoint odmawia,
w jakiej kolejności, i jak mapuje trzy wyniki aktuatora. Drugi plik obok
`test_writer_endpoint.py`, bo to drugi rodzaj zapisu i ma się psuć osobno.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from browser_supervisor.reader import (
    ACT_POST_PATH,
    READER_TOKEN_HEADER,
    FetchResult,
    ReaderServer,
)
from browser_supervisor.writer import (
    WRITER_TOKEN_HEADER,
    PostResult,
    WriteBlocked,
    WriteRefused,
    WriteUnknown,
    authorize_submit_url,
)

TOKEN = "sekret-czytnika"
WRITER = "sekret-aktuatora"

GOOD_BODY = {
    "community": "kubernetes",
    "title": "Czego nauczył nas tydzień z operatorem bez testów",
    "text": "Trzy rzeczy, po kolei, bez morałów.",
    "account": "Demo_Maintainer",
}


class FakeFetcher:
    def fetch(self, url: str, *, timeout_s: float) -> FetchResult:
        return FetchResult(status=200, final_url=url, content_type="application/json", body="{}")


class FakeController:
    def __init__(self) -> None:
        self.current: dict[str, Any] = {
            "mode": "INTERACTIVE",
            "display_only": True,
            "human_steer_open": False,
            "alive": True,
        }

    def state(self) -> Mapping[str, Any]:
        return dict(self.current)

    def switch(
        self, mode: str, reason: str | None, display_only: bool = False
    ) -> Mapping[str, Any]:
        return dict(self.current)


class FakeActuator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.comments: list[dict[str, Any]] = []
        self.raise_with: Exception | None = None
        self.result = PostResult(
            fullname="t3_1x22yz",
            post_id="1x22yz",
            permalink="https://www.reddit.com/r/kubernetes/comments/1x22yz/",
            account="Demo_Maintainer",
            observed_at="2026-09-19T21:00:00+00:00",
            title=GOOD_BODY["title"],
        )

    def comment(self, **kwargs: Any) -> Any:
        self.comments.append(kwargs)
        raise AssertionError("własny post nie ma prawa iść ścieżką komentarza")

    def own_post(self, **kwargs: Any) -> PostResult:
        self.calls.append(kwargs)
        if self.raise_with is not None:
            raise self.raise_with
        return self.result


@pytest.fixture
def running() -> Iterator[tuple[ReaderServer, FakeActuator, FakeController]]:
    actuator = FakeActuator()
    controller = FakeController()
    server = ReaderServer(
        bind_host="127.0.0.1",
        port=0,
        token=TOKEN,
        fetcher=FakeFetcher(),
        min_interval_s=0.0,
        timeout_s=5.0,
        controller=controller,
        writer_token=WRITER,
        actuator=actuator,
        act_timeout_s=3.0,
    )
    server.start()
    try:
        yield server, actuator, controller
    finally:
        server.stop()


def _post(
    server: ReaderServer,
    body: Any,
    *,
    token: str | None = WRITER,
    header: str = WRITER_TOKEN_HEADER,
) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers[header] = token
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}{ACT_POST_PATH}",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


# -- adres formularza ----------------------------------------------------------------------------


SUBMIT_URL = "https://old.reddit.com/r/kubernetes/submit?selftext=true"


@pytest.mark.parametrize(
    "community", ["kubernetes", "r/kubernetes", "/r/kubernetes/", " kubernetes "]
)
def test_a_community_name_becomes_one_canonical_submit_url(community: str) -> None:
    assert authorize_submit_url(community) == SUBMIT_URL


@pytest.mark.parametrize(
    "community",
    [
        "",
        "k",
        "ma spacje",
        "kubernetes/comments/1w934kh",
        "https://evil.example/r/kubernetes/",
        "../../etc",
        "kubernetes?x=1",
    ],
)
def test_anything_that_is_not_a_subreddit_name_is_refused_before_chrome(community: str) -> None:
    with pytest.raises(WriteRefused):
        authorize_submit_url(community)


# -- bramki przed aktuatorem -----------------------------------------------------------------------


def test_without_the_writer_token_nothing_reaches_the_actuator(running) -> None:
    server, actuator, _ = running
    status, payload = _post(server, GOOD_BODY, token=None)
    assert status == 401 and payload["error"] == "token_rejected"
    assert actuator.calls == []


def test_the_reader_token_does_not_open_the_actuator(running) -> None:
    """Czytanie i pisanie to dwa różne uprawnienia, więc i dwa różne sekrety."""
    server, actuator, _ = running
    status, _ = _post(server, GOOD_BODY, token=TOKEN)
    assert status == 401
    status, _ = _post(server, GOOD_BODY, token=TOKEN, header=READER_TOKEN_HEADER)
    assert status == 401
    assert actuator.calls == []


def test_a_capsule_that_is_not_display_only_refuses_the_post(running) -> None:
    server, actuator, controller = running
    controller.current["human_steer_open"] = True
    status, payload = _post(server, GOOD_BODY)
    assert status == 409 and payload["error"] == "capsule_not_display_only"
    assert actuator.calls == []


@pytest.mark.parametrize("missing", ["community", "title", "text", "account"])
def test_every_field_is_required(running, missing: str) -> None:
    server, actuator, _ = running
    body = {k: v for k, v in GOOD_BODY.items() if k != missing}
    status, payload = _post(server, body)
    assert status == 400 and payload["error"] == "bad_request"
    assert actuator.calls == []


# -- trzy wyniki -----------------------------------------------------------------------------------


def test_a_post_that_went_through_answers_with_the_thread_that_was_read_back(running) -> None:
    server, actuator, _ = running
    status, payload = _post(server, GOOD_BODY)
    assert status == 200, payload
    assert payload["fullname"] == "t3_1x22yz"
    assert payload["post_id"] == "1x22yz"
    assert payload["permalink"].startswith("https://www.reddit.com/r/kubernetes/comments/1x22yz")
    assert payload["submit_started"] is True
    assert actuator.calls == [
        {
            "community": GOOD_BODY["community"],
            "title": GOOD_BODY["title"],
            "text": GOOD_BODY["text"],
            "account": GOOD_BODY["account"],
            "timeout_s": 3.0,
        }
    ], actuator.calls
    assert actuator.comments == []


def test_a_refusal_says_nothing_was_submitted(running) -> None:
    server, actuator, _ = running
    actuator.raise_with = WriteRefused("brak formularza nowego wątku")
    status, payload = _post(server, GOOD_BODY)
    assert status == 409 and payload["error"] == "refused"
    assert payload["submit_started"] is False


def test_a_block_stops_the_queue_and_carries_the_flag(running) -> None:
    server, actuator, _ = running
    actuator.raise_with = WriteBlocked("you are doing that too much", submit_started=True)
    status, payload = _post(server, GOOD_BODY)
    assert status == 423 and payload["error"] == "blocked"
    assert payload["submit_started"] is True


def test_an_unknown_outcome_is_reported_as_unknown_not_as_a_failure(running) -> None:
    """Po wysłaniu formularza wątek MOŻE istnieć; 504 to jedyna uczciwa odpowiedź, a kolejka
    nie ma z niej prawa wywnioskować, że wolno spróbować drugi raz."""
    server, actuator, _ = running
    actuator.raise_with = WriteUnknown("nie widać wątku po 45 s", submit_started=True)
    status, payload = _post(server, GOOD_BODY)
    assert status == 504 and payload["error"] == "unknown"
    assert payload["submit_started"] is True


def test_a_broken_actuator_never_reports_that_nothing_was_sent(running) -> None:
    server, actuator, _ = running
    actuator.raise_with = RuntimeError("Chrome padł")
    status, payload = _post(server, GOOD_BODY)
    assert status == 502 and payload["error"] == "browser_unavailable"
    assert payload["submit_started"] is None


# -- atrapa strony, na której biegnie `make pilot-actuator-check` ------------------------------


def test_the_stub_submit_page_has_exactly_what_the_actuator_reaches_for() -> None:
    """Atrapa i aktuator muszą mówić o tych samych elementach.

    Sprawdzenie aktuatora biegnie w kontenerze z prawdziwym Chrome, więc tutaj nie da się go
    uruchomić; da się za to złapać ROZJECHANIE SIĘ atrapy z selektorami, przez które aktuator
    patrzy. To jest ta połowa, która psuje się po cichu: strona dalej się renderuje, a kontrola
    zaczyna mierzyć coś innego, niż się wydaje.
    """
    from browser_supervisor.actuator_check import _new_thread_page, _submit_page

    page = _submit_page(account="Demo_Maintainer").decode("utf-8")
    assert 'id="header-bottom-right"' in page and 'class="user"' in page
    assert 'id="newlink"' in page
    assert 'name="title"' in page and 'name="text"' in page
    assert 'name="submit"' in page
    assert 'class="error"' in page and "display:none" in page

    landed = _new_thread_page(account="Demo_Maintainer", post_id="p1z9", title="Tytuł").decode(
        "utf-8"
    )
    assert 'class="thing link"' in landed
    assert 'data-author="Demo_Maintainer"' in landed
    assert 'data-fullname="t3_p1z9"' in landed
    assert 'class="comments"' in landed


def test_the_landing_address_of_the_stub_is_one_the_actuator_recognises() -> None:
    """Adres, pod który przenosi formularz, musi pasować do wzorca, po którym aktuator poznaje
    nowy wątek. Inaczej publikacja udana wyglądałaby jak skutek nieznany."""
    from browser_supervisor.writer import _NEW_POST_PATH

    match = _NEW_POST_PATH.match("/r/testsub/comments/p1z9/")
    assert match is not None and match["post"] == "p1z9"
