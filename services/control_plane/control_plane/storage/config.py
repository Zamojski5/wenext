"""The frozen retention thresholds of artifacts, and the vocabulary the schema already fixed.

    "encrypted profile storage, artifacts ACL i retention" (L5196)
    "trace i screenshot są wyłączone podczas loginu i MFA" (L3897)
    "trace po zalogowaniu ma krótki TTL, ACL i szyfrowanie" (L3898)

THIS IS THE ONE CONFIGURATION MODULE THE ARTIFACT THRESHOLDS LIVE IN. D-017 demands exactly
that ("wartości mieszkają w JEDNYM module konfiguracji, nie rozsypane po wywołaniach") and
`jobs/backoff.py` — the same module for the queue thresholds — says in its own docstring that
the artifact retention and the source-content limit are NOT there and belong with the artifacts
package. This file is that place; the two numbers below are not repeated anywhere else in this
package, and `test_artifacts_acl.py::test_the_retention_thresholds_are_the_ones_frozen_in_d_017`
compares the table against D-017 so a silent edit is a failing test.

The source-content limit of D-017 (1 MiB, the point above which a payload travels as
`payload_ref` rather than in the row) is DELIBERATELY NOT HERE: it is a threshold of the job
payload, not of an artifact, and nothing in this package reads it. Writing it down here would
create the second home D-017 forbids. Reported to the orchestrator rather than absorbed.

WHY `retention_until` HAS NO DEFAULT ANYWHERE — not here, not in `artifacts.py`, not in the
schema. Migration 0003 spells the reason out: two different retentions (thirty days in general,
twenty-four hours for a trace taken after login) cannot be one column DEFAULT, so the column is
`NOT NULL` with none at all and whoever writes an artifact decides how long it lives. The
function below is how a caller ARRIVES at that value; it is not a fallback the store applies
when the caller forgot. `ArtifactStore.record` never calls it.

WHAT IS NOT MEASURED HERE, stated rather than implied. `retention_until_for` refuses to hand out
a retention for a trace or a screenshot taken during login or MFA, because L3897 says such an
artifact is not produced at all. That refusal is measured
(`test_a_trace_during_login_gets_no_retention_because_it_is_never_written`). What is NOT measured
by this package is that the Browser Runner actually asks this function before it starts a trace —
the runner has no capture path in F1 (that is E1.F2), so there is no call site to measure. This
module can refuse; it cannot yet prove that nobody bypassed it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final


class ArtifactKind(StrEnum):
    """The kinds `artifacts_kind_is_known` accepts: five from migration 0003, one from 0016.

    Typed rather than passed as strings so that a kind the schema does not know is an error in
    Python — with a name — instead of a `23514` from PostgreSQL at the end of a transaction.

    `SOURCE` is the raw material behind a `knowledge_sources` row (P3): the bytes a page or a feed
    actually returned. It lives here rather than in a directory of its own so that it inherits
    what this table already has — retention, ACL, a checksum and the sweeper. `EXPORT` (P7) is
    there for the same reason and one more: the file holds a whole organization, so the retention
    that comes with this table is the difference between a copy that expires and a copy that sits
    on a volume until somebody finds it.
    """

    TRACE = "TRACE"
    SCREENSHOT = "SCREENSHOT"
    HAR = "HAR"
    LOG = "LOG"
    REPORT = "REPORT"
    SOURCE = "SOURCE"
    #: P7: the whole of one organization's own data, as a file the customer can take away. It is
    #: an artifact like the rest so that it inherits a checksum, an ACL, a retention and the
    #: sweeper: a copy of an entire tenant with no expiry is a leak waiting to be found.
    EXPORT = "EXPORT"


class ArtifactAcl(StrEnum):
    """The three values `artifacts_acl_is_known` accepts (migration 0003).

    Three and not more, and the schema comment says why: in E1 there are exactly three reading
    sides — the operator, the Browser Runner, and both — because profile storage is reachable
    only by the runner (L3894) and no third principal exists to grant anything to.
    """

    OPERATOR_ONLY = "OPERATOR_ONLY"
    RUNNER_ONLY = "RUNNER_ONLY"
    OPERATOR_AND_RUNNER = "OPERATOR_AND_RUNNER"


class Reader(StrEnum):
    """Who is asking for the bytes. The other half of the ACL comparison."""

    OPERATOR = "OPERATOR"
    RUNNER = "RUNNER"


class BrowserPhase(StrEnum):
    """The phase of the browser session an artifact would be captured in (L3897-3898).

    `AFTER_LOGIN` is the only phase in which a trace may exist at all, and even then with the
    short TTL of L3898.
    """

    LOGIN = "LOGIN"
    MFA = "MFA"
    AFTER_LOGIN = "AFTER_LOGIN"


# ---------------------------------------------------------------------------
# The two frozen retention thresholds (D-017)
# ---------------------------------------------------------------------------

#: How long an ordinary artifact lives. D-017: "retencja artefaktu | 30 dni".
ARTIFACT_RETENTION: Final[timedelta] = timedelta(days=30)

#: How long a trace captured AFTER login lives. D-017 quotes L3898's "krótki TTL" and fixes it at
#: twenty-four hours. Deliberately absent from the schema as a CHECK: a threshold duplicated in
#: the DDL drifts away from this module without anything saying so (migration 0003 says so too).
TRACE_AFTER_LOGIN_RETENTION: Final[timedelta] = timedelta(hours=24)

#: The phases in which nothing is recorded (L3897).
PHASES_WITH_NO_RECORDING: Final[frozenset[BrowserPhase]] = frozenset(
    {BrowserPhase.LOGIN, BrowserPhase.MFA}
)

#: The kinds L3897 names: "trace i screenshot są wyłączone podczas loginu i MFA". A HAR is not on
#: this list because the document does not put it there, and widening a prohibition on my own is
#: as much an invention as narrowing it.
KINDS_NEVER_RECORDED_DURING_LOGIN: Final[frozenset[ArtifactKind]] = frozenset(
    {ArtifactKind.TRACE, ArtifactKind.SCREENSHOT}
)

#: The whole table as data, so a test compares it with D-017 instead of re-typing the numbers
#: next to the assertions — the shape `jobs/backoff.py::QUEUE_THRESHOLDS` already uses.
RETENTION_THRESHOLDS: Final[dict[str, object]] = {
    "artifact_retention_days": ARTIFACT_RETENTION.days,
    "trace_after_login_retention_hours": int(TRACE_AFTER_LOGIN_RETENTION.total_seconds() // 3600),
    "kinds_never_recorded_during_login": tuple(
        sorted(kind.value for kind in KINDS_NEVER_RECORDED_DURING_LOGIN)
    ),
    "phases_with_no_recording": tuple(sorted(phase.value for phase in PHASES_WITH_NO_RECORDING)),
}


class ArtifactIsNotRecorded(Exception):
    """The artifact asked about is one L3897 says is never produced. Not an error to retry.

    A distinct type rather than a returned `None`, because `None` invites the caller to fall back
    on a default retention — which would write down the very row that must not exist.
    """


def retention_until_for(
    *, kind: ArtifactKind, phase: BrowserPhase, created_at: datetime
) -> datetime:
    """The moment an artifact of this kind, captured in this phase, stops being allowed to exist.

    Raises `ArtifactIsNotRecorded` for a trace or a screenshot during login or MFA (L3897): there
    is no retention for a file that is never written.

    `created_at` is an ARGUMENT and not `datetime.now()`, and that is the same rule the approval
    store follows for the same reason — the caller passes the moment PostgreSQL reported inside
    the transaction that will hold the row, so the value compared by the schema's
    `retention_until > created_at` CHECK and the value computed here come from ONE clock.
    """
    if phase in PHASES_WITH_NO_RECORDING and kind in KINDS_NEVER_RECORDED_DURING_LOGIN:
        raise ArtifactIsNotRecorded(
            f"artefakt rodzaju {kind.value} nie powstaje w fazie {phase.value} (L3897) — "
            "pytanie o jego retencję jest pytaniem o czas życia pliku, którego nie wolno "
            "zapisać. Odmowa, nie wartość domyślna"
        )
    if kind is ArtifactKind.TRACE:
        return created_at + TRACE_AFTER_LOGIN_RETENTION
    return created_at + ARTIFACT_RETENTION
