"""The refusal that stands in front of every outbound write (L5217, L5334, L5218, L5692).

WHAT THIS FILE IS AND WHAT IT IS NOT. It is four checks and a `raise`. It is NOT the place where
the operator's consent is verified against the database — that is
`control_plane.approval.store.ApprovalStore.authorize_and_consume_intent`, which reads the
revision, the decision and the intent out of PostgreSQL, runs every check of
`contracts.authorize_side_effect`, burns the grant with a compare-and-set and hands back a
`SideEffectAuthorization`. By the time a proof reaches this module the hard work is done.

So why does this module exist at all, if the proof is already a proof? Because a proof is a value,
and values travel. Between the moment `authorize_and_consume_intent` returns and the moment
something is written on a platform there is code, and that code can:

  * pass no proof at all — the manual adapter call of L5334;
  * pass a proof that was never bound to a consumed intent — `model_construct` skips every
    pydantic validator, so the model's own guarantee is not a guarantee against a caller who
    wants to get around it;
  * pass a proof for OTHER content than the content it is about to write — the one that matters,
    because it is the shape a real bug takes: authorize the approved draft, publish the edited
    one;
  * pass a proof whose grant deadline has since passed (L5692).

Each of those four is a branch below, in that order, and each has a test whose data reaches THAT
branch rather than being caught by an earlier one — see the header of
`tests/test_side_effect_guard.py` for why that sentence is load-bearing in this repository.

WHAT IS DELIBERATELY NOT CHECKED HERE, so that nobody reads a promise that is not made:

  * that the revision is still `APPROVED` and the consent still matches it. Checked in
    `contracts.authorize_side_effect` against rows read inside one transaction; repeating it here
    against a value would be a weaker second copy that can disagree with the one that counted.
  * that the port on the other side really is the code named by `adapter_version`. That needs an
    adapter registry and is E1.F5 (L5323-5329).
  * that this content was not already written once. That is not a value check at all: it is
    `UNIQUE (intent_id)` on `execution_attempts` (migration 0003, L2821), and it is enforced in
    `store.record_attempt`.
"""

from __future__ import annotations

from datetime import datetime

from community_os_contracts.execution import ApprovalHashInput, SideEffectAuthorization


class SideEffectRefused(PermissionError):
    """No outbound write may happen. A refusal is never a return value that reads like success.

    `PermissionError` and not a bare `Exception`, because `contracts.authorize_side_effect` raises
    `PermissionError` for the same family of gaps and a caller that catches one should not have to
    know which layer refused it.
    """


def require_authorization(
    *,
    content: ApprovalHashInput,
    authorization: SideEffectAuthorization | None,
    now: datetime,
) -> SideEffectAuthorization:
    """Return the proof, or refuse. The only way past this function is a proof for THIS content.

    `authorization` is typed optional HERE and required in `adapter.SideEffectAdapter.perform`,
    and the difference is the point: the adapter promises the type, this function is the runtime
    backstop for a caller who ignores it. Python enforces no annotation at run time, and L5334 is
    about exactly the caller who is not going through the front door.

    `now` is an argument and never read off the process clock. Every deadline this function
    compares against was written by PostgreSQL (`execution_intents.expires_at`), so the moment it
    is compared with has to come from the same clock; `adapter` reads it with `SELECT now()`.
    The rule is `approval/store.py`'s, for the same reason: two clocks in one decision are two
    orders of the same events.

    Every one of the four refusals below is measured by a test that first breaks the check in this
    file and sees the test go red — the list is in the header of
    `tests/test_side_effect_guard.py`. Nothing is claimed here that was not measured that way.
    """
    if authorization is None:
        raise SideEffectRefused(
            "Brak dowodu autoryzacji. Żaden zapisujący side effect nie omija ExecutionIntent "
            "(L5217), a akcja bez ważnej zgody jest niewykonalna także przy RĘCZNYM wywołaniu "
            "adaptera (L5334). Dowód powstaje wyłącznie w "
            "ApprovalStore.authorize_and_consume_intent"
        )

    if authorization.consumed_intent.consumed_at is None:
        raise SideEffectRefused(
            f"Dowód autoryzacji dla intentu {authorization.intent_id} niesie grant, którego nikt "
            "nie zużył. Dowód powstaje PO zużyciu intentu, nie przed (L2821) — taki obiekt można "
            "zbudować wyłącznie z pominięciem walidatorów pydantica i nie jest dowodem niczego"
        )

    expected_digest = content.payload_hash()
    if authorization.payload_hash != expected_digest:
        raise SideEffectRefused(
            "Dowód autoryzacji dotyczy innej treści niż ta, która ma zostać zapisana. Zgoda "
            f"wskazuje payload hash {authorization.payload_hash}, a treść ma {expected_digest}. "
            "Approval wskazuje DOKŁADNY payload hash, a edycja treści unieważnia zgodę (L5218)"
        )

    if now >= authorization.consumed_intent.expires_at:
        raise SideEffectRefused(
            f"Grant wykonania {authorization.intent_id} wygasł o "
            f"{authorization.consumed_intent.expires_at.isoformat()}, a jest "
            f"{now.isoformat()}. Stary grant wykonania jest jednorazowy I WYGASA (L5692) — "
            "zużycie go nie zatrzymuje upływu czasu"
        )

    return authorization
