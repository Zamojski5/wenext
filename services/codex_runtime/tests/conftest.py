"""Fixtures for the Codex runtime tests.

Everything except `@pytest.mark.live` works WITHOUT a ChatGPT account. This is possible because
`codex app-server` starts without a login — checked by running it: the handshake,
`config/read`, `model/list`, `thread/start` and `account/login/start(chatgptDeviceCode)`
answer correctly on a logged-out instance. Authorization is required only by
`account/rateLimits/read` and by actually executing a turn.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from codex_runtime import (
    AppServerClient,
    CodexAccountAdapter,
    ModelCatalogSnapshot,
    ModelInfo,
    find_codex_bin,
    prepare_codex_home,
)


@pytest.fixture(scope="session")
def codex_bin() -> Path:
    try:
        return find_codex_bin()
    except FileNotFoundError as exc:
        pytest.skip(f"brak binarki codex: {exc}")


@pytest.fixture
def codex_home(tmp_path: Path) -> Path:
    return prepare_codex_home(tmp_path / "codex-home")


@pytest.fixture
def client(codex_bin: Path, codex_home: Path) -> Iterator[AppServerClient]:
    """A live `codex app-server` on an isolated CODEX_HOME, after the handshake, not logged in."""
    client = AppServerClient(codex_bin=codex_bin, codex_home=codex_home)
    client.start()
    client.initialize()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def adapter(client: AppServerClient) -> CodexAccountAdapter:
    return CodexAccountAdapter(client=client)


def advance_to_authenticated(adapter: CodexAccountAdapter) -> CodexAccountAdapter:
    """Drives the adapter along the legal path DISCONNECTED → AUTH_PENDING → AUTHENTICATED.

    Without an account the login cannot be completed, but `model/list` works on a logged-out
    instance — so the pagination test needs a state in which synchronizing the catalog is
    allowed at all. We get there ALONG THE TABLE of transitions, and not by forcing the
    field: if somebody changed the table, this helper will stop working and that is the
    right behaviour.
    """
    from community_os_contracts import CodexConnectionState as ConnectionState

    adapter.transition_to(ConnectionState.AUTH_PENDING)
    adapter.transition_to(ConnectionState.AUTHENTICATED)
    return adapter


def snapshot_with(*models: ModelInfo, complete: bool = True) -> ModelCatalogSnapshot:
    """A catalog snapshot built by hand — for routing tests without the network."""
    return ModelCatalogSnapshot(
        models=models,
        complete=complete,
        include_hidden=False,
        page_count=1,
        content_hash="sha256:test",
        fetched_at=datetime.now(UTC),
    )


def model(model_id: str, efforts: tuple[str, ...] = ("low", "medium", "high")) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        model=model_id,
        display_name=model_id,
        hidden=False,
        is_default=False,
        default_reasoning_effort="medium",
        supported_reasoning_efforts=efforts,
    )
