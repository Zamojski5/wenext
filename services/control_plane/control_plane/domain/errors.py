"""The refusal a fenced write ends with. One exception, raised in one situation.

The situation is the sentence L2668 spells out:

    "Zero zaktualizowanych wierszy oznacza utracony fencing token i wynik starego workera jest
     odrzucany."

Which is why this module exists at all. In the driver this project uses, a CAS that matched
nothing DOES NOT RAISE — `UPDATE ... WHERE lease_generation = $1` with no matching row returns
the STRING `"UPDATE 0"` and the call succeeds. Zero rows announced as success is the most common
shape of this project's recurring disease in database code, so every fenced write in
`jobs/store.py` counts its rows and turns zero into this exception.

The freshness rule itself is NOT re-implemented here. When a live lease exists, the decision is
made by `contracts.require_fresh_job_lease` and the `StaleJobFencingToken` it raises is chained
as `__cause__` — so the evidence in the traceback comes from the contracts, and there is no
second copy of the comparison to drift.
"""

from __future__ import annotations

from typing import NoReturn

from community_os_contracts import (
    JobFencingToken,
    StaleJobFencingToken,
    require_fresh_job_lease,
)


class JobFencingRefused(Exception):
    """A write was refused because the token presented does not hold the job any more.

    Carries both tokens, because "CAS failed" is not something an operator can act on. `live`
    is `None` when the job has no live lease at all — the lease expired and nobody has taken over
    yet, which is a different situation from "somebody else holds it" and reads differently on
    the screen.
    """

    def __init__(
        self, *, presented: JobFencingToken, live: JobFencingToken | None, reason: str
    ) -> None:
        if live is None:
            detail = (
                f"job {presented.job_id} nie ma w tej chwili żywego lease'u — "
                f"przedstawiony lease (próba {presented.attempt_id}, generacja "
                f"{presented.lease_generation}) wygasł i nikt go jeszcze nie przejął"
            )
        else:
            detail = (
                f"job {presented.job_id} trzyma teraz próba {live.attempt_id}, generacja "
                f"{live.lease_generation}, właściciel {live.lease_owner}; przedstawiono próbę "
                f"{presented.attempt_id}, generację {presented.lease_generation}, właściciela "
                f"{presented.lease_owner}"
            )
        super().__init__(f"Zapis odrzucony ({reason}): {detail}.")
        self.presented = presented
        self.live = live
        self.reason = reason


def refuse_stale_write(
    *, presented: JobFencingToken, live: JobFencingToken | None, reason: str
) -> NoReturn:
    """Always raises. The single exit taken by every fenced write that updated zero rows.

    When a live lease exists, `require_fresh_job_lease` from the contracts is what decides that
    the presented token is stale — this function does not compare the fields itself. Its verdict
    is then wrapped, so the caller catches one exception type while the traceback still names the
    contracts rule that rejected the token.

    The fall-through matters too. If the contracts check does NOT object — the tuple looks
    current — the write still failed, which means the CAS lost on something else: the lease is
    past `expires_at`, or the expected status no longer holds. That is reported as its own
    refusal rather than swallowed. A silent success there would be the whole bug.
    """
    if live is not None:
        try:
            require_fresh_job_lease(current=live, presented=presented)
        except StaleJobFencingToken as stale:
            raise JobFencingRefused(presented=presented, live=live, reason=reason) from stale
    raise JobFencingRefused(presented=presented, live=live, reason=reason)
