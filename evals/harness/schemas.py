"""Output schema for Codex structured outputs (L5265: they are a requirement, not an option).

The schemas are written by hand rather than generated from pydantic, for one reason:
**`UNKNOWN` must be a first-class value in the schema**, and not `null`. A schema
generated automatically from `Observed[float]` would admit `{"value": null}` and the model
could send a zero where the source exposed nothing (L4299, L5552).

Here a metric is a `oneOf`: either `{"value": <number>}`, or
`{"unknown_reason": <reason>, "unknown_detail": <text>}` — and nothing in between.
`test_schemas.py` makes sure the schema does NOT let through `null` or a bare `0`
where the source did not expose a number.
"""

from __future__ import annotations

from community_os_contracts import PriorityTier, SignalClass, UnknownReason, WindowState

from .types import DeclineReason


def _metric(kind: str) -> dict:
    """An observable metric: a number OR an explicit UNKNOWN with a reason. Never `null`."""
    return {
        "oneOf": [
            {
                "type": "object",
                "properties": {"value": {"type": kind}},
                "required": ["value"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "unknown_reason": {"enum": [r.value for r in UnknownReason]},
                    "unknown_detail": {"type": "string"},
                },
                "required": ["unknown_reason"],
                "additionalProperties": False,
            },
        ]
    }


THREAD_QUALITY_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "depth_profile",
        "unique_participants",
        "substantive_reply_ratio",
        "author_participation",
        "icp_share",
        "intentional_amplification",
        "reach_calibration_basis",
        "negative_signals",
        "window_state",
        "fake_engagement_flags",
        "entry_opportunity",
        "priority_tier",
        "confidence",
        "data_quality",
        "evidence_refs",
    ],
    "properties": {
        "depth_profile": _metric("integer"),
        "unique_participants": _metric("integer"),
        "substantive_reply_ratio": _metric("number"),
        "author_participation": _metric("number"),
        "icp_share": _metric("number"),
        "intentional_amplification": _metric("integer"),
        "reach_calibration_basis": {"type": "string"},
        "negative_signals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["signal_class", "evidence_ref"],
                "properties": {
                    # Negative classes only — a positive class in this place
                    # would turn the stop into a weight (L4472).
                    "signal_class": {
                        "enum": [
                            SignalClass.NEGATIVE_MODERATION.value,
                            SignalClass.BAN.value,
                            SignalClass.LOCK.value,
                        ]
                    },
                    "evidence_ref": {"type": "string"},
                },
            },
        },
        "window_state": {"enum": [outcome.value for outcome in WindowState]},
        # Log-only: the model MUST report them and has NO right to lower the tier over them.
        "fake_engagement_flags": {"type": "array", "items": {"type": "string"}},
        "entry_opportunity": {"type": "string", "minLength": 1},
        "priority_tier": {"enum": [t.value for t in PriorityTier]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "data_quality": {"type": "string", "minLength": 1},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
    },
}


DISCOVERY_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ordered"],
    "properties": {
        # AN ORDERED LIST. There is no `score` field here and none may be added (L4468).
        "ordered": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["thread_ref", "priority_tier", "shortlisted", "reason"],
                "properties": {
                    "thread_ref": {"type": "string"},
                    "priority_tier": {"enum": [t.value for t in PriorityTier]},
                    "shortlisted": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}


DRAFT_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text", "used_claims", "referenced_claims", "evidence_refs", "declined"],
    "properties": {
        "text": {"type": "string"},
        # Claims ASSERTED AS FACT — kept apart from those merely referenced (L751).
        "used_claims": {"type": "array", "items": {"type": "string"}},
        "referenced_claims": {"type": "array", "items": {"type": "string"}},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "declined": {"type": "boolean"},
        "decline_reason": {"enum": [r.value for r in DeclineReason]},
    },
}


#: The set of all schemas — for the consistency test and for the report.
OUTPUT_SCHEMAS: dict[str, dict] = {
    "thread_quality": THREAD_QUALITY_OUTPUT_SCHEMA,
    "discovery": DISCOVERY_OUTPUT_SCHEMA,
    "draft_reply": DRAFT_OUTPUT_SCHEMA,
}


__all__ = [
    "DISCOVERY_OUTPUT_SCHEMA",
    "DRAFT_OUTPUT_SCHEMA",
    "OUTPUT_SCHEMAS",
    "THREAD_QUALITY_OUTPUT_SCHEMA",
]
