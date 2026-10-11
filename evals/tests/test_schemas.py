"""Output schema: `UNKNOWN` is a first-class value, not `null` and not 0.

DoD D21 and D17: "validation rejects 0 where the source did not expose a number".
The schema is the contract with the model — if it lets `null` through, the model gets
permission to send a zero instead of an explicit absence of a read.
"""

from __future__ import annotations

import pytest
from community_os_contracts import Observed, UnknownReason

from evals.harness.schemas import (
    DISCOVERY_OUTPUT_SCHEMA,
    DRAFT_OUTPUT_SCHEMA,
    OUTPUT_SCHEMAS,
    THREAD_QUALITY_OUTPUT_SCHEMA,
)

METRICS = (
    "depth_profile",
    "unique_participants",
    "substantive_reply_ratio",
    "author_participation",
    "icp_share",
    "intentional_amplification",
)


@pytest.mark.parametrize("field", METRICS)
def test_metric_is_a_sum_type_number_or_unknown(field: str) -> None:
    schemat = THREAD_QUALITY_OUTPUT_SCHEMA["properties"][field]
    variants = schemat["oneOf"]
    assert len(variants) == 2
    with_value, with_reason = variants
    assert with_value["required"] == ["value"]
    assert with_reason["required"] == ["unknown_reason"]
    # `null` is not an admissible type in either of the variants.
    assert "null" not in str(with_value["properties"]["value"]["type"])


@pytest.mark.parametrize("field", METRICS)
def test_unknown_requires_a_reason(field: str) -> None:
    with_reason = THREAD_QUALITY_OUTPUT_SCHEMA["properties"][field]["oneOf"][1]
    allowed_reasons = with_reason["properties"]["unknown_reason"]["enum"]
    assert set(allowed_reasons) == {r.value for r in UnknownReason}
    assert with_reason["additionalProperties"] is False


def test_Observed_type_forbids_UNKNOWN_without_a_reason() -> None:
    """The same rule on the type side, not only in the schema (invariant 7 from 22.2)."""
    with pytest.raises(ValueError):
        Observed[float]()
    with pytest.raises(ValueError):
        Observed[float](value=0.0, unknown_reason=UnknownReason.NOT_EXPOSED_BY_PLATFORM)
    assert Observed[float].known(0.0).value == 0.0  # a measured zero IS allowed


def test_negative_signals_allows_only_negative_classes() -> None:
    element = THREAD_QUALITY_OUTPUT_SCHEMA["properties"]["negative_signals"]["items"]
    assert set(element["properties"]["signal_class"]["enum"]) == {
        "NEGATIVE_MODERATION",
        "BAN",
        "LOCK",
    }


def test_discovery_has_no_score_field() -> None:
    """L4468: we do not sum points into one score — in the output schema as well."""
    element = DISCOVERY_OUTPUT_SCHEMA["properties"]["ordered"]["items"]
    assert "score" not in element["properties"]
    assert element["additionalProperties"] is False
    assert DISCOVERY_OUTPUT_SCHEMA["properties"]["ordered"]["type"] == "array"


def test_draft_separates_asserted_claims_from_referenced() -> None:
    properties = DRAFT_OUTPUT_SCHEMA["properties"]
    assert "used_claims" in properties
    assert "referenced_claims" in properties


def test_all_schemas_are_closed() -> None:
    for nazwa, schemat in OUTPUT_SCHEMAS.items():
        assert schemat["additionalProperties"] is False, nazwa


# ---------------------------------------------------------------------------
# The contract with the model: what WE SEND IT, and what WE ACCEPT FROM IT
# ---------------------------------------------------------------------------


def test_hidden_metric_reaches_the_model_as_UNKNOWN_not_null() -> None:
    """The model is to receive exactly the signal it is to return.

    If we sent `null` or `0`, asking for `UNKNOWN` in the answer would be
    an inconsistency — and an inconsistent prompt is the cheapest way to ruin an eval.
    """
    from evals.harness import dataset
    from evals.harness.codex_runtime import observation_payload

    obs = dataset.observation("f-icp-hidden")
    assert obs.icp_share is None and "icp_share" in obs.hidden_metrics

    payload = observation_payload(obs)
    assert payload["icp_share"]["unknown_reason"] == "NOT_EXPOSED_BY_PLATFORM"
    assert "value" not in payload["icp_share"]
    assert payload["reach_impressions"]["unknown_reason"] == "NOT_EXPOSED_BY_PLATFORM"
    # A visible metric goes as a value, not as UNKNOWN.
    visible_obs = dataset.observation("f-depth-deep")
    assert observation_payload(visible_obs)["icp_share"]["value"] == visible_obs.icp_share


def test_null_from_the_model_is_an_error_not_a_zero() -> None:
    """Reading the model's answer rejects `null` — missing data without a reason is banned."""
    from evals.harness.codex_runtime import CodexRuntime

    assert CodexRuntime._observed_float(0.0).value == 0.0
    assert CodexRuntime._observed_float({"value": 0}).value == 0.0
    assert not CodexRuntime._observed_float({"unknown_reason": "NOT_EXPOSED_BY_PLATFORM"}).is_known
    for invalid in (None, {"value": None}, {}, "0"):
        with pytest.raises(ValueError, match="UNKNOWN"):
            CodexRuntime._observed_float(invalid)


def test_codex_adapter_imports_without_D12() -> None:
    """The adapter module itself is to be correct even before D12 exists.

    The import of the `codex_runtime` package is lazy — a syntax error or a wrong symbol name
    in the adapter must surface here, and not only at `make evals-live` on the account.
    """
    import importlib

    module = importlib.import_module("evals.harness.codex_runtime")
    assert module.CodexRuntime.kind == "codex"
    with pytest.raises(module.CodexRuntimeUnavailable, match="codex_runtime"):
        module._client_factory()
