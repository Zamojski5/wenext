"""Enums transcribed verbatim from ROADMAP_CODEX_CDP_COMMUNITY_OS.md.

Principle: the values are EXACTLY the ones in the document. Nothing is added "just in case"
and nothing is shortened. Every enum carries the number of the line it comes from.
If the document says one thing in one place and something else in another — both versions are
present here, and the discrepancy is described in NOTES.md, not settled quietly.
"""

from __future__ import annotations

from enum import StrEnum

# ---------------------------------------------------------------------------
# Section 14.8 — separated state machines (L2584-2606)
# ---------------------------------------------------------------------------


class SocialAccountState(StrEnum):
    """L2585-2587. `RUNNING` is never a state of an account (L2608)."""

    PROVISIONED = "PROVISIONED"
    VERIFYING = "VERIFYING"
    READY = "READY"
    PAUSED = "PAUSED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    QUARANTINED = "QUARANTINED"


class BrowserProfileState(StrEnum):
    """L2589-2591. `AVAILABLE` means that the browser may be completely closed (L2608)."""

    CREATING = "CREATING"
    AVAILABLE = "AVAILABLE"
    LEASED = "LEASED"
    QUARANTINED = "QUARANTINED"
    CORRUPT = "CORRUPT"
    RETIRED = "RETIRED"


class BrowserSessionState(StrEnum):
    """L2593-2595."""

    STARTING = "STARTING"
    ACTIVE = "ACTIVE"
    STOPPING = "STOPPING"
    CLOSED = "CLOSED"
    CRASHED = "CRASHED"
    ORPHANED = "ORPHANED"


class HumanTakeoverState(StrEnum):
    """L2597-2599."""

    REQUESTED = "REQUESTED"
    SAFE_STOPPING = "SAFE_STOPPING"
    CONTROL_GRANTED = "CONTROL_GRANTED"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class ExecutionAttemptState(StrEnum):
    """L2601-2605.

    After `SUBMIT_STARTED` there is no automatic retry (L2673, L4560, L5335).
    `UNKNOWN` consumes the budget and creates an alert, not another attempt (L5337).
    """

    PREPARED = "PREPARED"
    DISPATCHED = "DISPATCHED"
    SUBMIT_STARTED = "SUBMIT_STARTED"
    CONFIRMED = "CONFIRMED"
    UNKNOWN = "UNKNOWN"
    FAILED_BEFORE_SIDE_EFFECT = "FAILED_BEFORE_SIDE_EFFECT"
    CAPABILITY_BLOCKED = "CAPABILITY_BLOCKED"
    IDENTITY_BLOCKED = "IDENTITY_BLOCKED"


#: States after reaching which any automatic retry is forbidden (L2673, L5335).
#: After `SUBMIT_STARTED` the side effect may already have happened on the platform side.
STATES_FORBIDDING_AUTOMATIC_RETRY: frozenset[ExecutionAttemptState] = frozenset(
    {
        ExecutionAttemptState.SUBMIT_STARTED,
        ExecutionAttemptState.CONFIRMED,
        ExecutionAttemptState.UNKNOWN,
    }
)


# ---------------------------------------------------------------------------
# Section 14.1 / 14.5 — capsule modes and leases (L2272, L2442-2457)
# ---------------------------------------------------------------------------


class CapsuleMode(StrEnum):
    """L2442. HEADLESS is the default mode and handles all of the automation's work (L2272)."""

    HEADLESS = "HEADLESS"
    INTERACTIVE = "INTERACTIVE"


class InteractionLeaseMode(StrEnum):
    """L2408. Who may send input."""

    AUTOMATION = "AUTOMATION"
    HUMAN = "HUMAN"


class StreamSessionRole(StrEnum):
    """L2420. `viewOnly` on the noVNC client side is NOT a security boundary (L2470)."""

    VIEW_ONLY = "VIEW_ONLY"
    CONTROL = "CONTROL"


class LeaseState(StrEnum):
    """Lease states; section 14.4 lists the `state` field without enumerating its values (L2366).

    Values derived from the rules of the same section: the lease is authoritative in Postgres
    (L2371), losing the heartbeat starts reconciliation, not an immediate second start (L2376),
    an inconclusive reconciliation moves the profile to QUARANTINED (L2379).
    """

    ACTIVE = "ACTIVE"
    RECONCILING = "RECONCILING"
    RELEASED = "RELEASED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class InteractiveReason(StrEnum):
    """The reason for starting INTERACTIVE mode.

    The full enum per section 24.1. The discrepancy between L5161 (four reasons) and
    L4370-4376 (six operations) is settled — see `E1_ALLOWED_INTERACTIVE_REASONS` below
    and NOTES.md.

    The list is CLOSED. Every use of interactive mode for a reason outside the list creates
    an alert and requires a decision, not a silent exception (L5518).
    """

    FIRST_LOGIN = "FIRST_LOGIN"
    REAUTH = "REAUTH"
    MFA = "MFA"
    CHALLENGE = "CHALLENGE"
    HUMAN_TAKEOVER = "HUMAN_TAKEOVER"
    IDENTITY_VERIFICATION = "IDENTITY_VERIFICATION"


#: Reasons allowed in stage E1 (L5161: LOGIN, MFA, CHALLENGE, HUMAN_TAKEOVER).
#: `LOGIN` from L5161 covers FIRST_LOGIN and REAUTH — both are a human logging in at the screen.
#: `IDENTITY_VERIFICATION` is in the enum because 24.1 defines it, but in E1 it requires an
#: explicit decision.
E1_ALLOWED_INTERACTIVE_REASONS: frozenset[InteractiveReason] = frozenset(
    {
        InteractiveReason.FIRST_LOGIN,
        InteractiveReason.REAUTH,
        InteractiveReason.MFA,
        InteractiveReason.CHALLENGE,
        InteractiveReason.HUMAN_TAKEOVER,
    }
)


# ---------------------------------------------------------------------------
# Section 14.7 — Capability Registry (L2523-2580)
# ---------------------------------------------------------------------------


class CapabilityState(StrEnum):
    """L2528-2533. After expiry the state becomes `UNKNOWN` until a re-probe (L2580)."""

    SUPPORTED = "SUPPORTED"
    DEGRADED = "DEGRADED"
    MANUAL_ONLY = "MANUAL_ONLY"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class ExecutionMode(StrEnum):
    """L5232 — capability probing records per account and per ChannelSurface.

    In E1 every writing action has `AUTOMATED_AFTER_APPROVAL`;
    the `AUTOMATED` mode for a writing action does not exist (L4294, L5150).
    """

    AUTOMATED = "AUTOMATED"
    AUTOMATED_AFTER_APPROVAL = "AUTOMATED_AFTER_APPROVAL"
    MANUAL_ONLY = "MANUAL_ONLY"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class SideEffect(StrEnum):
    """The document states `IRREVERSIBLE` explicitly (L2543). The remaining values are needed
    to tell reading actions apart from writing ones — see NOTES.md, entry "side_effect"."""

    NONE = "NONE"
    REVERSIBLE = "REVERSIBLE"
    IRREVERSIBLE = "IRREVERSIBLE"


class RetryClass(StrEnum):
    """The document states `RECONCILE_ONLY` explicitly (L2544, L5326).

    `RECONCILE_ONLY` means: once the submit has started there is no automatic second attempt;
    the only allowed path is reconciliation and a human decision (L4560).
    """

    SAFE_RETRY = "SAFE_RETRY"
    RECONCILE_ONLY = "RECONCILE_ONLY"
    NO_RETRY = "NO_RETRY"


class ResultEvidenceKind(StrEnum):
    """L2545 (`URL`, `CONTENT_HASH`) extended with the evidence required in E1.F5 (L5328):
    URL, content hash, screenshot, `adapter_version`."""

    URL = "URL"
    CONTENT_HASH = "CONTENT_HASH"
    SCREENSHOT = "SCREENSHOT"
    ADAPTER_VERSION = "ADAPTER_VERSION"


class SemanticCapability(StrEnum):
    """The set of capabilities.

    CAUTION — the document uses two vocabularies of names for the same notion:
      * section 14.7 (L2566-2578) — the "adapter" vocabulary,
      * section 22.3 (L2347-2359) — the "stage E1" vocabulary, used in the gate capability
        mapping.
    Both are present here, because both occur in normative fragments. NOTES.md describes the
    discrepancy.
    """

    # --- section 14.7 vocabulary (L2566-2578) ---
    READ_IDENTITY = "READ_IDENTITY"
    READ_ACCOUNT_METRICS = "READ_ACCOUNT_METRICS"
    READ_CONTENT = "READ_CONTENT"
    READ_PUBLICATION_METRICS = "READ_PUBLICATION_METRICS"
    PREPARE_COMMENT = "PREPARE_COMMENT"
    SUBMIT_COMMENT = "SUBMIT_COMMENT"
    PREPARE_POST = "PREPARE_POST"
    SUBMIT_POST = "SUBMIT_POST"
    PREPARE_CONNECTION_REQUEST = "PREPARE_CONNECTION_REQUEST"
    SUBMIT_CONNECTION_REQUEST = "SUBMIT_CONNECTION_REQUEST"
    MANUAL_TAKEOVER = "MANUAL_TAKEOVER"

    # --- section 22.3 vocabulary, E1 capability mapping (L2347-2359) ---
    DISCOVER_SURFACES = "DISCOVER_SURFACES"
    SEARCH_CONTENT = "SEARCH_CONTENT"
    READ_THREAD = "READ_THREAD"
    READ_METRICS = "READ_METRICS"
    PUBLISH_REPLY = "PUBLISH_REPLY"
    REACT_TO_CONTENT = "REACT_TO_CONTENT"
    JOIN_COMMUNITY = "JOIN_COMMUNITY"
    PUBLISH_CONTENT = "PUBLISH_CONTENT"
    REQUEST_HUMAN_ACTION = "REQUEST_HUMAN_ACTION"
    MANAGE_CAMPAIGN = "MANAGE_CAMPAIGN"
    READ_AD_METRICS = "READ_AD_METRICS"


#: The minimal adapter contract (L2551-2561). Kept as data so that the contract test
#: can check that every adapter implements exactly these methods.
ADAPTER_CONTRACT_METHODS: tuple[str, ...] = (
    "verify_identity",
    "probe_capabilities",
    "read_account_metrics",
    "read_content",
    "read_publication_metrics",
    "prepare_action",
    "execute_action",
    "reconcile_action",
    "detect_block_reason",
    "detect_takeover_requirement",
)


# ---------------------------------------------------------------------------
# Section 22.5 — access_mode (L4391-4395)
# ---------------------------------------------------------------------------


class AccessMode(StrEnum):
    """How a given surface can be read at all.

    `UNKNOWN` is treated like `ACCOUNT_REQUIRED` (L4395, L5529) — never as
    "let's try anonymously".
    """

    PUBLIC_API_NO_ACCOUNT = "PUBLIC_API_NO_ACCOUNT"
    PUBLIC_WEB_NO_ACCOUNT = "PUBLIC_WEB_NO_ACCOUNT"
    ACCOUNT_REQUIRED = "ACCOUNT_REQUIRED"
    MEMBERSHIP_REQUIRED = "MEMBERSHIP_REQUIRED"
    UNKNOWN = "UNKNOWN"


#: Modes requiring an active account assignment in order to read anything (L4401, L5526).
#: `UNKNOWN` is here on purpose, because the document orders treating it like ACCOUNT_REQUIRED.
ACCESS_MODES_REQUIRING_ACCOUNT: frozenset[AccessMode] = frozenset(
    {
        AccessMode.ACCOUNT_REQUIRED,
        AccessMode.MEMBERSHIP_REQUIRED,
        AccessMode.UNKNOWN,
    }
)


# ---------------------------------------------------------------------------
# Section 22.7 — discussion quality assessment (L4445, L4464, L4471-4479)
# ---------------------------------------------------------------------------


class WindowState(StrEnum):
    """The life window of a thread (L4445). A `CLOSED` thread creates no new draft (L5541)."""

    RISING = "RISING"
    PEAK = "PEAK"
    DECAYING = "DECAYING"
    CLOSED = "CLOSED"


class PriorityTier(StrEnum):
    """L4464. A fake engagement flag does NOT lower `priority_tier` (L4492, L5551)."""

    A = "A"
    B = "B"
    C = "C"
    REJECT = "REJECT"


class SignalClass(StrEnum):
    """Signal classes from L4471-4479.

    Caution: this is an ordering, NOT a numeric weight. The document forbids summing points
    into a single score that pretends to be the platform's own arithmetic (L4468, L4481).
    """

    NEGATIVE_MODERATION = "NEGATIVE_MODERATION"
    BAN = "BAN"
    LOCK = "LOCK"
    INTENTIONAL_AMPLIFICATION = "INTENTIONAL_AMPLIFICATION"
    SUBSTANTIVE_REPLY = "SUBSTANTIVE_REPLY"
    REPOST = "REPOST"
    REPLY = "REPLY"
    FOLLOW = "FOLLOW"
    SUBSCRIBE = "SUBSCRIBE"
    LIKE = "LIKE"
    UPVOTE = "UPVOTE"


class ChannelArchetype(StrEnum):
    """Channel archetypes occurring in the document (L4582-4586, L5347).

    The E1 gate requires at least TWO DIFFERENT archetypes (L4304, L5347).
    """

    THREADED_COMMUNITY = "THREADED_COMMUNITY"
    PROFESSIONAL_GRAPH = "PROFESSIONAL_GRAPH"
    SHORT_FORM_FEED = "SHORT_FORM_FEED"
    LONG_FORM_PUBLISHING = "LONG_FORM_PUBLISHING"
    CODE_COLLABORATION = "CODE_COLLABORATION"


# ---------------------------------------------------------------------------
# Section 22.8 — presence track (L4496-4505, L4510)
# ---------------------------------------------------------------------------


class PresenceTrackState(StrEnum):
    """Promotion on evidence only, never on elapsed time nor on account age (L4297, L4496).

    `ESTABLISHED` is already beyond the E1 gate (L4289, L4505).
    `CAMPAIGN_ELIGIBLE` is outside the E1 scope (L4496) and is deliberately absent from this enum.
    """

    UNASSESSED = "UNASSESSED"
    BASELINED = "BASELINED"
    OBSERVE_ONLY = "OBSERVE_ONLY"
    VISIBILITY_BUILDING = "VISIBILITY_BUILDING"
    SOFT_PARTICIPATION = "SOFT_PARTICIPATION"
    ESTABLISHED = "ESTABLISHED"
    # Degradation is immediate and automatic (L4510).
    REASSESSMENT_REQUIRED = "REASSESSMENT_REQUIRED"
    BLOCKED = "BLOCKED"


# ---------------------------------------------------------------------------
# Section 13.1 — Codex (L2134-2145, L2152-2157)
# ---------------------------------------------------------------------------


class CodexConnectionState(StrEnum):
    """L2134-2141."""

    DISCONNECTED = "DISCONNECTED"
    AUTH_PENDING = "AUTH_PENDING"
    AUTHENTICATED = "AUTHENTICATED"
    CAPABILITIES_SYNCING = "CAPABILITIES_SYNCING"
    READY = "READY"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    SYNC_RETRY = "SYNC_RETRY"
    RATE_LIMITED = "RATE_LIMITED"
    DISABLED = "DISABLED"


class JobWaitingState(StrEnum):
    """L2144. Jobs in `WAITING_*` states do not hold a runtime lease (L2147, L5288)."""

    WAITING_AUTH = "WAITING_AUTH"
    WAITING_QUOTA = "WAITING_QUOTA"
    WAITING_CAPABILITY = "WAITING_CAPABILITY"
    WAITING_RETRY = "WAITING_RETRY"


class CodexSupervisorMode(StrEnum):
    """L2152-2157. The modes are exclusive — never two App Servers on one `CODEX_HOME` (L2159)."""

    STOPPED = "STOPPED"
    AUTH_CONTROL = "AUTH_CONTROL"
    EXECUTION = "EXECUTION"


class ThreadRole(StrEnum):
    """Four isolated thread roles — an F0 requirement (L5167).

    "Planner, worker, verifier and post-verifier use separate threads;
     there is no global thread for the whole workspace." (L2265)
    """

    PLANNER = "PLANNER"
    WORKER = "WORKER"
    VERIFIER = "VERIFIER"
    POST_VERIFIER = "POST_VERIFIER"


# ---------------------------------------------------------------------------
# Alerts (L4598 + alerts named in other sections)
# ---------------------------------------------------------------------------


class AlertKind(StrEnum):
    """Alert kinds named explicitly in the document."""

    # New alert kinds in E1 (L4598).
    THREAD_WINDOW_CLOSING = "THREAD_WINDOW_CLOSING"
    DRAFT_EXPIRED = "DRAFT_EXPIRED"
    APPROVAL_CAPACITY_EXCEEDED = "APPROVAL_CAPACITY_EXCEEDED"
    NEGATIVE_COMMUNITY_EVENT = "NEGATIVE_COMMUNITY_EVENT"
    FAKE_ENGAGEMENT_SUSPECTED = "FAKE_ENGAGEMENT_SUSPECTED"
    E1_GATE_AT_RISK = "E1_GATE_AT_RISK"
    # Alerts named outside 22.10.
    NEEDS_LOGIN_OR_MFA = "NEEDS_LOGIN_OR_MFA"  # L5241
    UNKNOWN_EXTERNAL_RESULT = "UNKNOWN_EXTERNAL_RESULT"  # L5327, L5337
    RESOURCE_GAP = "RESOURCE_GAP"  # L5285, L5414
    ROUTE_MISMATCH = "ROUTE_MISMATCH"  # L5402, L5641
    THRESHOLD_CHANGED = "THRESHOLD_CHANGED"  # L4619
    AUTHORIZATION_CONTEXT_CHANGED = "AUTHORIZATION_CONTEXT_CHANGED"  # L5627
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"  # L4510
    CAPABILITY_LOST = "CAPABILITY_LOST"  # L4510


# ---------------------------------------------------------------------------
# Observation and judgment — docs/KIERUNEK-PRODUKTU.md §3, §9; docs/OUTCOMES.md §3
# ---------------------------------------------------------------------------


class ObservationPlatform(StrEnum):
    """Platforms the observer can read TODAY, one value per adapter that exists.

    Not a registry and not `SocialAccount.platform` (free text in 0001). A value lands here
    when `services/community_observer/` has an adapter for it, and the database CHECK
    `communities_platform_is_known` holds exactly these values — the parity test in
    `services/db_schema` compares the two, so a platform cannot appear on one side only.
    """

    HACKER_NEWS = "HACKER_NEWS"
    REDDIT = "REDDIT"


class JudgmentDecision(StrEnum):
    """The agent's decision about one thread — `docs/KIERUNEK-PRODUKTU.md` §3.

    Three values and deliberately no fourth. "The most important decision is not WRITE."
    `IGNORE` is a FULL record with reasoning, not the absence of a row: the decision to stay
    silent is the data nobody else has (§9, "negative data").
    """

    ENGAGE = "ENGAGE"
    IGNORE = "IGNORE"
    WAIT = "WAIT"


class JudgmentFeedbackVerdict(StrEnum):
    """What the operator said about one judgment (`docs/OUTCOMES.md` §3 point 5).

    `IRRELEVANT` is separate from `DISAGREE` on purpose: "I would not have said anything here"
    and "this thread has nothing to do with me" teach the judge two different things.
    """

    AGREE = "AGREE"
    DISAGREE = "DISAGREE"
    IRRELEVANT = "IRRELEVANT"


class ReactionKind(StrEnum):
    """What kind of reaction the account makes in one conversation moment (etap jakości rozmów,
    `docs/ROADMAP-JAKOSC-ROZMOW.md` R1). Closed list, shared by the generator in
    `control_plane/observe/reactions.py` and the episode contract in `evals/conversations/`.

    Two of these carry no text and are full results, not the absence of one: `WAIT` (the topic is
    right, the moment is not) and `SILENCE` (nothing to add here). `SHORT_ACK` and `HUMOR` are
    reactions inside an EXISTING conversation with the account; a new entry into somebody else's
    thread still has to carry a concrete contribution (`HELP`, `DIAGNOSE`, `CLARIFY`, `DISAGREE`).
    The judge's `ENGAGE / IGNORE / WAIT` stays the decision whether to be here at all.
    """

    HELP = "HELP"
    DIAGNOSE = "DIAGNOSE"
    CLARIFY = "CLARIFY"
    DISAGREE = "DISAGREE"
    SHORT_ACK = "SHORT_ACK"
    HUMOR = "HUMOR"
    WAIT = "WAIT"
    SILENCE = "SILENCE"
