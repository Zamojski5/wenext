"""The recorded server-notification contract still matches the pinned package (P2).

WHY THIS FILE EXISTS. A session records what the engine tells it: the assistant's messages, every
MCP tool call and its result, the end of a turn. Those shapes are not ours and they are not in
`app-server-methods.json`, which covers only what the CLIENT may send. They come from the pinned
`openai-codex` package, and `contract/app-server-notifications.json` is the copy this repository
parses against.

A copy that nobody compares is a copy that rots. When the package is bumped and a field moves,
the failure without this test is not an error: it is a session that silently stops recording tool
calls, and a conversation on screen with a hole in it that nobody can explain. That is the same
disease as a document asserting something the code does not have (STAN-PRAC §7, third variant),
one layer out.

SO THE DRIFT IS MADE LOUD. If this fails after a version bump, regenerate:

    python -m codex_runtime.generate_notification_contract

and then READ the diff, because a changed field is a changed contract for
`sessions/handlers.py`, not a file to be refreshed without looking.
"""

from __future__ import annotations

import pytest
from codex_runtime.generate_notification_contract import (
    SESSION_ITEM_TYPES,
    SESSION_NOTIFICATIONS,
    collect,
    load_notification_contract,
)


def test_the_recorded_contract_is_what_the_pinned_package_says_today() -> None:
    recorded = load_notification_contract()
    fresh = collect()

    assert recorded["wygenerowano_z"]["pakiet"] == fresh["wygenerowano_z"]["pakiet"]
    assert recorded["wygenerowano_z"]["wersja"] == fresh["wygenerowano_z"]["wersja"], (
        f"kontrakt zapisano dla openai-codex {recorded['wygenerowano_z']['wersja']}, "
        f"a zainstalowana jest {fresh['wygenerowano_z']['wersja']}. Przegeneruj i PRZECZYTAJ "
        "różnicę: zmienione pole to zmieniony kontrakt dla sessions/handlers.py"
    )
    assert recorded["notyfikacje_sesji"] == fresh["notyfikacje_sesji"], (
        "kształty notyfikacji serwera rozjechały się z paczką. Bez tego testu objawem byłaby "
        "sesja, która po cichu przestaje zapisywać wywołania narzędzi"
    )
    assert recorded["elementy_watku"] == fresh["elementy_watku"]


def test_the_contract_covers_every_notification_a_session_parses() -> None:
    """Count first, then compare: a list that silently became empty would pass any loop."""
    recorded = load_notification_contract()
    assert len(SESSION_NOTIFICATIONS) == 6, SESSION_NOTIFICATIONS
    assert set(recorded["notyfikacje_sesji"]) == set(SESSION_NOTIFICATIONS)
    assert len(SESSION_ITEM_TYPES) == 4, SESSION_ITEM_TYPES
    assert set(recorded["elementy_watku"]) == set(SESSION_ITEM_TYPES)


@pytest.mark.parametrize(
    ("notification", "field"),
    [
        ("thread/started", "thread"),
        ("item/completed", "item"),
        ("item/completed", "turnId"),
        ("turn/completed", "turn"),
    ],
)
def test_the_fields_a_session_actually_reads_are_in_the_contract(
    notification: str, field: str
) -> None:
    """Named one by one rather than as a set, because these four are the ones the handler
    dereferences: the thread id it stores and resumes, the item it turns into an event, the turn
    id it matches a completion on, and the turn it reads the outcome from."""
    recorded = load_notification_contract()
    entry = recorded["notyfikacje_sesji"][notification]
    assert field in entry["properties"], (
        f"{notification} nie ma już pola {field!r}; sessions/handlers.py czyta je wprost"
    )


def test_a_tool_call_item_still_carries_what_the_trace_records() -> None:
    """`server`, `tool`, `arguments`, `status` and `result` are what a TOOL_CALL and a
    TOOL_RESULT event are made of. `arguments` is also what the effect digest is computed from,
    so losing it would silently disable the "one write per identical call" guarantee."""
    recorded = load_notification_contract()
    item = recorded["elementy_watku"]["McpToolCallThreadItem"]
    for field in ("server", "tool", "arguments", "status", "result", "error"):
        assert field in item["properties"], f"McpToolCallThreadItem stracił pole {field!r}"
    assert "arguments" in item["required"], (
        "`arguments` przestało być wymagane. Odcisk wywołania liczy się z argumentów, więc "
        "wywołanie bez nich rozbiłoby gwarancję jednego zapisu na identyczne wywołanie"
    )


def test_an_assistant_message_still_carries_its_text() -> None:
    recorded = load_notification_contract()
    item = recorded["elementy_watku"]["AgentMessageThreadItem"]
    assert "text" in item["properties"] and "text" in item["required"]
