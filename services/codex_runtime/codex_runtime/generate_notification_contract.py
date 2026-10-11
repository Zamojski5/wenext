"""Regeneration of the SERVER notification contract from the pinned `openai-codex` package.

    python -m codex_runtime.generate_notification_contract

WHY A SECOND CONTRACT FILE. `contract/app-server-methods.json` was generated from the pinned
`codex` binary and covers what the CLIENT may send: requests and the one notification
(`initialized`). It says nothing about what the SERVER sends back during a turn, and that is
precisely what a session has to record: the assistant's messages, every MCP tool call and its
result, and the end of the turn.

Those shapes are not guesswork either. The pinned `openai-codex` package ships
`openai_codex.generated.notification_registry.NOTIFICATION_MODELS`, a generated map from a
notification method to its Pydantic model, produced from the same protocol schema as the
binary. This script copies the part of it a session needs into
`contract/app-server-notifications.json`, so that:

  * the fake app-server in the tests answers with shapes READ from the package rather than
    invented by whoever wrote the test, and
  * a version bump that changes or drops one of those notifications fails a test
    (`tests/test_notification_contract.py`) instead of silently changing what a session records.

WHAT IS COPIED, AND WHY NOT EVERYTHING. The registry has over fifty notifications, most of them
about surfaces this product does not use (realtime audio, file changes, plugins, remote control).
Copying all of them would make the file change on every unrelated bump, and a contract that is
noisy is a contract nobody reads. `SESSION_NOTIFICATIONS` is the list a session actually parses;
adding one here is a deliberate act.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .protocol import CONTRACT_DIR

#: The notifications a session parses. Everything else the server sends is recorded verbatim as
#: an unrecognised event rather than dropped, but only these have a shape we depend on.
SESSION_NOTIFICATIONS: tuple[str, ...] = (
    # The thread that was just created or resumed. Carries the id a session stores and later
    # resumes, so this one is load-bearing for continuity across turns.
    "thread/started",
    # One item of the conversation appearing and finishing: an assistant message, an MCP tool
    # call, reasoning. `item/completed` is where a tool call's result and status arrive.
    "item/started",
    "item/completed",
    # Progress of a long tool call. Recorded so a person watching sees movement rather than a
    # frozen screen.
    "item/mcpToolCall/progress",
    # The end of a turn, with its outcome.
    "turn/completed",
    # The thread's own state: this is how a server-side failure or interruption shows up when no
    # turn outcome arrives at all.
    "thread/status/changed",
)

#: The thread item types a session maps onto its own event kinds. The names come from the same
#: package; a rename upstream has to be visible here rather than turning into silently skipped
#: tool calls.
SESSION_ITEM_TYPES: tuple[str, ...] = (
    "AgentMessageThreadItem",
    "McpToolCallThreadItem",
    "ReasoningThreadItem",
    "UserMessageThreadItem",
)

CONTRACT_FILE = "app-server-notifications.json"


def _package_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("openai-codex")
    except PackageNotFoundError:  # pragma: no cover - only in a checkout without the SDK
        return "nieznana"


def collect() -> dict[str, Any]:
    """The contract as data, straight from the installed package."""
    from openai_codex.generated.notification_registry import NOTIFICATION_MODELS

    missing = [name for name in SESSION_NOTIFICATIONS if name not in NOTIFICATION_MODELS]
    if missing:
        raise RuntimeError(
            f"przypięta paczka openai-codex nie zna notyfikacji {missing}. To nie jest usterka "
            "tego skryptu: kontrakt protokołu się zmienił i sesja parsuje coś, czego serwer już "
            "nie wysyła"
        )

    notifications: dict[str, Any] = {}
    item_types: dict[str, Any] = {}
    for name in SESSION_NOTIFICATIONS:
        model = NOTIFICATION_MODELS[name]
        schema = model.model_json_schema()
        notifications[name] = {
            "model": model.__name__,
            "properties": sorted(schema.get("properties", {})),
            "required": sorted(schema.get("required", [])),
        }
        for item_name in SESSION_ITEM_TYPES:
            definition = schema.get("$defs", {}).get(item_name)
            if definition is not None and item_name not in item_types:
                item_types[item_name] = {
                    "properties": sorted(definition.get("properties", {})),
                    "required": sorted(definition.get("required", [])),
                }

    absent = [name for name in SESSION_ITEM_TYPES if name not in item_types]
    if absent:
        raise RuntimeError(
            f"nie znaleziono definicji elementów wątku {absent} w schematach notyfikacji. "
            "Sesja mapuje je na swoje rodzaje zdarzeń, więc ich brak jest zmianą kontraktu"
        )

    return {
        "_opis": (
            "Kontrakt notyfikacji SERWERA app-server, wygenerowany z PRZYPIĘTEJ paczki "
            "openai-codex, nie przepisany z dokumentacji. Uzupełnia app-server-methods.json, "
            "który opisuje wyłącznie żądania klienta."
        ),
        "_jak_odtworzyc": "python -m codex_runtime.generate_notification_contract",
        "wygenerowano_z": {
            "pakiet": "openai-codex",
            "wersja": _package_version(),
            "moduł": "openai_codex.generated.notification_registry",
        },
        "wygenerowano_o": datetime.now(UTC).isoformat(timespec="seconds"),
        "notyfikacje_sesji": notifications,
        "elementy_watku": item_types,
    }


def generate(target_dir: Path | None = None) -> Path:
    target = (target_dir or CONTRACT_DIR) / CONTRACT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(collect(), ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return target


def load_notification_contract(target_dir: Path | None = None) -> dict[str, Any]:
    path = (target_dir or CONTRACT_DIR) / CONTRACT_FILE
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    written = generate()
    print(f"zapisano {written}")
