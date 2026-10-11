"""The port every platform adapter implements — and the errors it is allowed to raise.

The port has two operations and no third: list what is new, read one thread in full. That is
all Outcome A needs (`docs/OUTCOMES.md` §3), and a port that grows a `post()` before Outcome C
would be the scope creep the direction warns about (§13: "Nie buduj mechanizmu na przyszłość").

THE ONE RULE ABOUT ERRORS: a source that cannot read says so with an exception, never with an
empty tuple. `[]` from a broken adapter is indistinguishable from "nothing new", and the
control plane would happily record a quiet day. This is 22.2 point 7 ("UNKNOWN is not zero")
applied to a list instead of a number, and `CredentialsMissing` exists for exactly the case
where the temptation is strongest — a Reddit adapter with no credentials configured.

The four error classes below are the WHOLE vocabulary, and the control plane's job handler
decides per class what the queue does: `SourceUnavailable` is worth a later attempt,
`CredentialsMissing` is a missing capability (park, do not retry), `ThreadNotFound` and
`UnknownCommunity` are facts about the request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from community_os_contracts import AccessMode, ObservationPlatform

from .models import CommunityRef, ObservedThread, ThreadSnapshot

#: The methods of the port, as data, so a contract test can check an adapter without knowing it.
COMMUNITY_SOURCE_METHODS: tuple[str, ...] = ("describe", "list_recent_threads", "fetch_thread")

#: How many threads one listing may ask for. Algolia caps a page at 1000; Reddit at 100. A
#: listing larger than this is a backfill, and a backfill is a different job than "what is new".
MAX_LISTING = 100


class SourceError(Exception):
    """Base of everything an adapter raises. The control plane catches subclasses, not this."""


class SourceUnavailable(SourceError):
    """The platform did not answer usefully: network error, 5xx, 429, or a body that is not
    the JSON it should be. Transient by definition — the job may be attempted again later."""


class CredentialsMissing(SourceError):
    """The adapter needs an account and none is configured. NOT transient: waiting does not help,
    a human has to act. The handler parks the job in `WAITING_CAPABILITY` (L2147)."""


class ThreadNotFound(SourceError):
    """The platform says there is no such thread (deleted, or never existed)."""


class UnknownCommunity(SourceError):
    """The adapter does not know this community key (`ask`, `r/devops`, …)."""


@runtime_checkable
class CommunitySource(Protocol):
    """Read access to one platform. Two operations, both read-only, both async."""

    platform: ObservationPlatform
    access_mode: AccessMode

    def describe(self, community_key: str) -> CommunityRef:
        """Validate the key and name the community. Raises `UnknownCommunity`."""
        ...

    async def list_recent_threads(
        self, community_key: str, *, since: datetime | None, limit: int
    ) -> tuple[ObservedThread, ...]:
        """Threads created after `since` (all recent ones when `None`), newest first.

        Comments are NOT included — that is what `fetch_thread` is for, and listing a hundred
        threads must not cost a hundred tree reads.
        """
        ...

    async def fetch_thread(self, community_key: str, external_id: str) -> ThreadSnapshot:
        """The thread and its whole comment tree as of now. Raises `ThreadNotFound`."""
        ...
