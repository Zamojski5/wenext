"""Isolation of Codex threads — section 13.3-13.4 (L2191-2266).

The separation the document considers essential (L2241):

    ThreadSecurityBinding — the durable security of resume
    CodexRunThread        — the metadata of a single run

"Resume is allowed only on an exact match of the stable binding and the active principal
 generation. A new run may have a different `automation_run_id` or `job_attempt_id`
 without a false conflict, but any difference in the security binding ends resume
 fail-closed." (L2266)
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import ThreadRole


class ThreadSecurityBinding(BaseModel):
    """The stable part of a thread's identity (L2244-2254). Decides whether resume is allowed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    codex_connection_id: UUID
    workspace_id: UUID
    social_account_id: UUID | None = None
    role: ThreadRole
    principal_generation: int | None = None
    tool_manifest_version: str
    security_policy_version: str
    #: The actual `session_id` returned by the runtime — recorded, not guessed (L2265).
    thread_id: str | None = None
    session_id: str | None = None

    def stable_key(self) -> tuple:
        """The tuple that has to match to the letter for resume to be allowed."""
        return (
            self.codex_connection_id,
            self.workspace_id,
            self.social_account_id,
            self.role,
            self.principal_generation,
            self.tool_manifest_version,
            self.security_policy_version,
        )


class ResumeRefused(Exception):
    """Fail-closed on any difference in the security binding (L2266, L5645)."""


def authorize_resume(
    stored: ThreadSecurityBinding,
    requested: ThreadSecurityBinding,
    *,
    active_principal_generation: int | None,
) -> None:
    """Checks whether the thread may be resumed. Raises `ResumeRefused` on the slightest
    difference.

    Acceptance test: "a new run may resume a matching stable ThreadSecurityBinding,
    but a difference in workspace, role, principal generation or tool manifest is fail-closed"
    (L5645).
    """
    if stored.stable_key() != requested.stable_key():
        differing_fields = [
            nazwa
            for nazwa, a, b in zip(
                (
                    "codex_connection_id",
                    "workspace_id",
                    "social_account_id",
                    "role",
                    "principal_generation",
                    "tool_manifest_version",
                    "security_policy_version",
                ),
                stored.stable_key(),
                requested.stable_key(),
                strict=True,
            )
            if a != b
        ]
        raise ResumeRefused(f"Różnica w stabilnym bindingu: {', '.join(differing_fields)}")

    if (
        stored.principal_generation is not None
        and stored.principal_generation != active_principal_generation
    ):
        raise ResumeRefused(
            f"Principal generation nieaktywna: binding {stored.principal_generation}, "
            f"aktywna {active_principal_generation}."
        )


class CodexRunThread(BaseModel):
    """The metadata of a single run (L2255-2263).

    Records the requested model/effort AND the observed effective model (L2265) — because
    "a reroute without an observable effective effort does not fake the value" (L5642).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    thread_security_binding_id: UUID
    automation_run_id: UUID
    job_attempt_id: UUID
    attempt: int = Field(ge=1)
    prompt_version_id: UUID
    router_policy_version: str

    requested_model: str
    requested_effort: str
    #: The model actually used, read from the runtime. Absent = we do not pretend to know.
    effective_model: str | None = None
    #: Whether effort is observable at all in this runtime.
    effort_observable: bool = False
    effective_effort: str | None = None

    started_at: datetime
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def _effort_is_not_faked(self) -> CodexRunThread:
        """ "a reroute without an observable effective effort does not fake the value" (L5642)."""
        if not self.effort_observable and self.effective_effort is not None:
            raise ValueError(
                "effective_effort ustawiony, mimo że effort nie jest obserwowalny — "
                "to jest udawanie wartości, zakazane w L5642."
            )
        return self


class TurnSpec(BaseModel):
    """Every turn explicitly sets the model, the effort and the output schema (L5647).

    "an unsupported model/effort is rejected before the turn" (L5631) — that is why the
    availability check is on the caller's side, and this type only enforces completeness.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: ThreadRole
    model: str
    effort: str
    #: The result's JSON Schema — structured outputs are a requirement, not an option (L5265).
    output_schema: dict

    @model_validator(mode="after")
    def _schema_is_not_empty(self) -> TurnSpec:
        if not self.output_schema:
            raise ValueError("output_schema nie może być pusty — każdy turn ma structured output")
        return self
