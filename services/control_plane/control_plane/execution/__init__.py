"""The single writing point of the system — DoD L5217, package L5217 of phase E1.F1.

    "żaden zapisujący side effect nie omija `ExecutionIntent`" (L5217)
    "akcja bez ważnego approvala jest niewykonalna także przy ręcznym wywołaniu adaptera" (L5334)

Four modules and one rule about who may call which:

  * `guard.py` — the refusal. A pure function; no database, no clock of its own.
  * `store.py` — every SQL statement of `execution_attempts`. The only file here that knows
    `asyncpg`.
  * `adapter.py` — the ONLY path from consented content to an outbound write, and the only
    caller of `WritingSideEffectPort`.
  * `bypass_scan.py` — the static self-check: which call sites in this repository look like an
    outbound write, and whether any of them sits outside `adapter.py`.

WHY THIS PACKAGE EXISTS BEFORE ITS FIRST CONSUMER, which is a founder's decision of 2026-08-31
and not an accident of ordering. In E1.F1 there is not a single path that writes to an external
platform: the Action Adapter is E1.F5 (L5323-5329) and capability probing is E1.F2. A gate built
AFTER the first write appears is the way bypasses get legalised — the first consumer arrives, the
gate is not there yet, an exception is made "just for now", and the exception becomes the shape of
the system. So the gate stands first and its EMPTINESS IS WRITTEN DOWN rather than hidden:
`tests/test_no_bypass_static.py` says in its own docstring that a green scan today means "there is
nothing to bypass", not "nobody bypasses".

Nothing is re-exported here. `from control_plane.execution import *` would be a second, shorter
list of names to keep in step with the modules, and this repository has already paid for exactly
that (`docs/STAN-PRAC.md` section 7, `test_every_all_entry_is_actually_bound`).
"""
