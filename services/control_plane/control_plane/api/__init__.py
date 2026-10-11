"""HTTP routers of the control plane. One module per area, each exporting `router`.

Modules here are DISCOVERED, not imported by hand: `control_plane.app.discover_routers()`
walks this package with `pkgutil` and mounts every `router` it finds, behind the operator
boundary. Dropping a file in is all it takes; there is no central list to update and therefore
none to forget.

Two rules, both enforced by tests in `tests/test_router_discovery.py`:

  * a module in this package MUST define `router` — a module without one raises, because a
    typo (`routes =` instead of `router =`) that merely skipped the module would produce a
    route that quietly is not there;
  * the router is mounted with `Depends(require_operator)` attached by the discovery loop, so
    a new area cannot forget the auth boundary. `/healthz` is registered directly on the app
    and is the only path outside it (D-016).

The package is empty in P1. Its contents arrive with the job queue, the approval path and the
audit trail — the areas E1.F1 actually writes (D-013).
"""
