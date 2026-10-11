"""Outcome A — observe one real community, understand it, judge each thread (`docs/OUTCOMES.md`).

    scheduling.py  job types, payload refs, idempotency keys, cadence — how work enters the queue
    store.py       the eight tables of migration 0004
    context.py     what the model sees; how what it says back is checked
    prompts.py     versioned prompts with frozen digests
    turns.py       one model turn with structured output (Codex on the founder's account)
    handlers.py    the four job handlers
    wiring.py      building all of it from the environment

`__all__` is checked by `tests/test_repo_consistency.py::test_every_all_entry_is_actually_bound`.
"""

from .context import (
    JUDGE_OUTPUT_SCHEMA,
    PICTURE_OUTPUT_SCHEMA,
    JudgmentRejected,
    PictureRejected,
    ThreadContext,
    ValidatedPicture,
    judge_input,
    judgment_from_output,
    picture_input,
    validate_picture,
)
from .handlers import ObserveHandlers
from .scheduling import (
    JOB_JUDGE,
    JOB_PICTURE,
    JOB_POLL,
    JOB_SNAPSHOT,
    OBSERVE_JOB_TYPES,
    ROUTING_KEY,
    SNAPSHOT_DELAYS,
    parse_ref,
    picture_is_due,
    schedule_judgment,
    schedule_picture,
    schedule_poll,
    schedule_snapshots,
)
from .store import (
    CommunityRow,
    JudgmentRow,
    ObservationStore,
    PictureRow,
    ProfileRow,
    ThreadRow,
)
from .turns import (
    DEFAULT_ROUTES,
    CodexTurnRunner,
    FakeTurnRunner,
    ModelTurnPort,
    ModelUnavailable,
    TurnFailed,
    TurnOutcome,
)
from .wiring import build_handlers, build_sources, build_turns

__all__ = [
    "DEFAULT_ROUTES",
    "JOB_JUDGE",
    "JOB_PICTURE",
    "JOB_POLL",
    "JOB_SNAPSHOT",
    "JUDGE_OUTPUT_SCHEMA",
    "OBSERVE_JOB_TYPES",
    "PICTURE_OUTPUT_SCHEMA",
    "ROUTING_KEY",
    "SNAPSHOT_DELAYS",
    "CodexTurnRunner",
    "CommunityRow",
    "FakeTurnRunner",
    "JudgmentRejected",
    "JudgmentRow",
    "ModelTurnPort",
    "ModelUnavailable",
    "ObservationStore",
    "ObserveHandlers",
    "PictureRejected",
    "PictureRow",
    "ProfileRow",
    "ThreadContext",
    "ThreadRow",
    "TurnFailed",
    "TurnOutcome",
    "ValidatedPicture",
    "build_handlers",
    "build_sources",
    "build_turns",
    "judge_input",
    "judgment_from_output",
    "parse_ref",
    "picture_input",
    "picture_is_due",
    "schedule_judgment",
    "schedule_picture",
    "schedule_poll",
    "schedule_snapshots",
    "validate_picture",
]
