"""URL and egress policy as a property of the control plane (L3929-3946, L5197, D-019).

Three modules, one boundary:

* `urlpolicy` — canonicalization, exact origin as the authorization boundary, HTTPS only
  outside an explicitly marked fixture, and the L3944 scheme block. It IMPORTS the sidecar's
  `mcp_sidecar.origins`; it does not reimplement it.
* `egress` — private and metadata addresses refused after DNS resolution, and on rebinding,
  with the resolver injected so nothing here needs a network.
* `registry` — the machine-readable register of section 19.4, including the six positions F1
  does not enforce, each with its anchor and target phase (D-019).

`__all__` lists only names this package really binds. Two packages in this repository once
exported 27 names between them that had never existed, so `from <package> import *` had been
raising the whole time with no test noticing; `test_every_all_entry_is_actually_bound` in
tests/test_repo_consistency.py is what stops that from happening again.
"""

from control_plane.policy.egress import (
    BLOCKED_NETWORKS,
    METADATA_ADDRESSES,
    EgressDecision,
    EgressPolicy,
    EgressRejected,
    Resolver,
    blocked_reason,
    require_public_addresses,
)
from control_plane.policy.registry import (
    ENFORCED,
    NOT_ENFORCED,
    SECTION_19_4,
    EnforcementStatus,
    PolicyItem,
)
from control_plane.policy.urlpolicy import (
    FORBIDDEN_SCHEMES,
    AuthorizedUrl,
    PolicyNotLoaded,
    PolicyRefusal,
    UrlPolicy,
    UrlRejected,
    canonical_origin,
    require_url,
)

__all__ = [
    "BLOCKED_NETWORKS",
    "ENFORCED",
    "FORBIDDEN_SCHEMES",
    "METADATA_ADDRESSES",
    "NOT_ENFORCED",
    "SECTION_19_4",
    "AuthorizedUrl",
    "EgressDecision",
    "EgressPolicy",
    "EgressRejected",
    "EnforcementStatus",
    "PolicyItem",
    "PolicyNotLoaded",
    "PolicyRefusal",
    "Resolver",
    "UrlPolicy",
    "UrlRejected",
    "blocked_reason",
    "canonical_origin",
    "require_public_addresses",
    "require_url",
]
