"""Control plane of phase E1.F1 (L5184-5219).

What lives here: the FastAPI application, the single-operator auth boundary, and — arriving in
later packages of this phase — the PostgreSQL job queue with its in-process dispatcher, the
approval path and the audit trail.

`__all__` below lists only names this package really binds. Two packages in this repository
once exported 27 names between them that had never existed, so `from <package> import *` had
been raising the whole time with no test noticing; `test_every_all_entry_is_actually_bound` in
tests/test_repo_consistency.py is what stops that from happening again.
"""

from control_plane import runtime
from control_plane.app import create_app, discover_routers, mounted_routes
from control_plane.auth import PUBLIC_PATHS, OperatorRequired, deny_reason, require_operator

__all__ = [
    "PUBLIC_PATHS",
    "OperatorRequired",
    "create_app",
    "deny_reason",
    "discover_routers",
    "mounted_routes",
    "require_operator",
    "runtime",
]
