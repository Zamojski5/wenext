"""An observable signal or an explicitly unknown one.

Invariant 7 from section 22.2 (L4299) and rule 5 from E1.F2 (L5242):

    "A signal we do not see from the outside has the state `UNKNOWN`;
     it is neither zero nor a default value."
    "Missing data is never turned into zero; it is `UNKNOWN` with a reason."

That is why in this system a missing read must not be represented by `None` nor by `0`.
`None` disappears in a summation, and `0` lies — it says "we measured and it came out zero".
`Observed[T]` forces every numeric value coming from the outside world to carry with it the
information whether it was observed at all, and if it was not — for what reason.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class UnknownReason(StrEnum):
    """The reason a value is unknown. Always required — `UNKNOWN` without one is forbidden."""

    # The platform simply does not show this value (table L4485-4490).
    NOT_EXPOSED_BY_PLATFORM = "NOT_EXPOSED_BY_PLATFORM"
    # The account has no access to this view (e.g. someone else's reach statistics, L4405).
    NOT_VISIBLE_TO_THIS_ACCOUNT = "NOT_VISIBLE_TO_THIS_ACCOUNT"
    # A surface behind a login wall, no connected account (L4402).
    NO_CONNECTED_ACCOUNT = "NO_CONNECTED_ACCOUNT"
    # An account in REAUTH_REQUIRED/QUARANTINED produces no data (L5241).
    ACCOUNT_NOT_READY = "ACCOUNT_NOT_READY"
    # The session died during the read — an empty list is NOT a result (L5530).
    SESSION_LOST = "SESSION_LOST"
    # The read would go beyond the effective_remaining of the read budget (L4596).
    READ_BUDGET_EXHAUSTED = "READ_BUDGET_EXHAUSTED"
    # The capability has expired and is waiting for a re-probe (L2580).
    CAPABILITY_EXPIRED = "CAPABILITY_EXPIRED"
    # The adapter could not parse — controlled degradation instead of a silent error (L5511).
    ADAPTER_COULD_NOT_PARSE = "ADAPTER_COULD_NOT_PARSE"
    # The result of an external action is unresolved after SUBMIT_STARTED (L2603, L5554).
    AMBIGUOUS_EXTERNAL_RESULT = "AMBIGUOUS_EXTERNAL_RESULT"


class Observed[T](BaseModel):
    """A value that was observed or is explicitly unknown. Exactly one of the two.

    It is impossible to build an instance that is known and unknown at the same time,
    or one that is unknown without a reason.

        >>> Observed[int].known(0).value
        0
        >>> Observed[int].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM).is_known
        False
    """

    model_config = ConfigDict(frozen=True)

    value: T | None = None
    unknown_reason: UnknownReason | None = None
    unknown_detail: str | None = None

    @model_validator(mode="after")
    def _exactly_one_of_value_or_reason(self) -> Observed[T]:
        has_value = self.value is not None
        has_reason = self.unknown_reason is not None
        if has_value and has_reason:
            raise ValueError(
                "Observed nie może być jednocześnie znane i nieznane "
                f"(value={self.value!r}, unknown_reason={self.unknown_reason})"
            )
        if not has_value and not has_reason:
            raise ValueError(
                "Observed musi mieć albo wartość, albo powód nieznajomości. "
                "Brak danych bez powodu jest zakazany — patrz niezmiennik 7 sekcji 22.2."
            )
        if self.unknown_detail is not None and not has_reason:
            raise ValueError("unknown_detail bez unknown_reason nie ma sensu")
        return self

    @classmethod
    def known(cls, value: T) -> Observed[T]:
        return cls(value=value)

    @classmethod
    def unknown(cls, reason: UnknownReason, detail: str | None = None) -> Observed[T]:
        return cls(unknown_reason=reason, unknown_detail=detail)

    @property
    def is_known(self) -> bool:
        return self.unknown_reason is None

    def require(self) -> T:
        """Returns the value or raises. Use only where an unknown value is a program error."""
        if self.unknown_reason is not None:
            raise ValueError(
                f"Wartość jest UNKNOWN ({self.unknown_reason}) i nie wolno jej zamienić na liczbę. "
                f"Szczegół: {self.unknown_detail or '—'}"
            )
        assert self.value is not None
        return self.value

    def or_unknown_marker(self) -> Any:
        """For serialization in reports: returns the value or the string 'UNKNOWN:<reason>'."""
        if self.unknown_reason is not None:
            return f"UNKNOWN:{self.unknown_reason.value}"
        return self.value
