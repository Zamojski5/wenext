"""The approval path over PostgreSQL — package P9 of phase E1.F1 (DoD L5217-5218).

`store.ApprovalStore` is the only thing here that touches the database. `models` carries the one
value that crosses out of it and that `contracts.execution` does not already own.

Nothing is re-exported. `from control_plane.approval import *` would be a second, shorter list of
names to keep in step with the modules, and this repository has already paid for exactly that:
`__all__` in two packages named sixteen and eleven identifiers that had never existed, so
`import *` was broken for months with every test green
(`docs/STAN-PRAC.md` section 7, `test_every_all_entry_is_actually_bound`).
"""
