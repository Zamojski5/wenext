"""Fixed, explicitly written down model/effort routes per thread role.

This is NOT the Model Router. The full router — with a policy, versioning and fallbacks — is
in E2 (L5266). In F0 the `TRASY` table is a frozen constant: four roles, four entries, no
selection logic whatsoever. Thanks to that the `router_policy_version` recorded in
`CodexRunThread` has a reproducible meaning, and the gate does not measure an accidental
decision of the router.

Two rules that this module enforces:

* An unsupported model/effort combination is rejected BEFORE the turn, and not attempted
  (L5287, L5631). Validation goes against `supportedReasoningEfforts` from `model/list`,
  not against a list written into the code.
* The effort values are WIRE values (`low`/`medium`/`high`/`xhigh`/...), not
  names from the UI (`Light`, `Extra High`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from community_os_contracts import AlertKind, ThreadRole

from .account_adapter import ModelCatalogSnapshot

#: The version of the route table. It goes into `CodexRunThread.router_policy_version`.
ROUTER_POLICY_VERSION = "f0-stala-tabela-1"

#: The effort values on the wire (enum ReasoningEffort). `model/list` may return a subset
#: per model — which is why this is only a spelling sanity check, not the source of truth
#: about availability.
WIRE_EFFORTS: frozenset[str] = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "persistent"}
)


@dataclass(frozen=True)
class Route:
    model: str
    effort: str
    uzasadnienie: str

    def __post_init__(self) -> None:
        if self.effort not in WIRE_EFFORTS:
            raise ValueError(
                f"effort={self.effort!r} nie jest wartością drutową. Nazwy z UI (Light, "
                f"Extra High) nie są wartościami API. Dozwolone: {sorted(WIRE_EFFORTS)}"
            )


#: Four roles from `ThreadRole`, four routes. Changing any entry requires an ADR,
#: because it invalidates the comparability of eval results between runs.
ROUTES: dict[ThreadRole, Route] = {
    ThreadRole.PLANNER: Route(
        model="gpt-5.6-sol",
        effort="high",
        uzasadnienie="Plan jest najdroższym błędem w całym łańcuchu — zły plan psuje "
        "wszystkie kolejne turny. Sol na high tylko tutaj.",
    ),
    ThreadRole.WORKER: Route(
        model="gpt-5.6-terra",
        effort="medium",
        uzasadnienie="Trasa domyślna z versions.lock.toml. Worker wykonuje najwięcej turnów, "
        "więc to on decyduje o zużyciu okna limitu.",
    ),
    ThreadRole.VERIFIER: Route(
        model="gpt-5.6-terra",
        effort="high",
        uzasadnienie="Weryfikacja ma łapać błędy workera, więc nie może być od niego słabsza; "
        "ten sam model, wyższy effort — różnica jest w wysiłku, nie w rodzinie modelu.",
    ),
    ThreadRole.POST_VERIFIER: Route(
        model="gpt-5.6-luna",
        effort="low",
        uzasadnienie="Post-weryfikacja sprawdza obserwowalny efekt po fakcie na gotowych "
        "dowodach — najtańszy model wystarcza i nie pali okna limitu.",
    ),
}


class RouteUnavailable(Exception):
    """A model/effort combination rejected BEFORE the turn (L5287, L5631)."""


class RouteMismatch(Exception):
    """`model/rerouted` without an observable effective effort blocks the commit (L5402, L5641)."""

    alert = AlertKind.ROUTE_MISMATCH


def route(role: ThreadRole) -> Route:
    return ROUTES[role]


def validate_route(role: ThreadRole, snapshot: ModelCatalogSnapshot) -> Route:
    """Checks the route against the model catalog. Raises BEFORE dispatch, never mid-turn."""
    if not snapshot.complete:
        raise RouteUnavailable(
            "snapshot katalogu modeli jest niekompletny — job idzie w WAITING_CAPABILITY, "
            "routing jest zablokowany (L2189)"
        )
    t = route(role)
    model = snapshot.model(t.model)
    if model is None:
        available = ", ".join(sorted(m.id for m in snapshot.models)) or "(pusty katalog)"
        raise RouteUnavailable(
            f"model {t.model!r} dla roli {role} nie istnieje w katalogu. Dostępne: {available}"
        )
    if t.effort not in model.supported_reasoning_efforts:
        raise RouteUnavailable(
            f"model {t.model!r} nie wspiera effortu {t.effort!r} dla roli {role}. "
            f"supportedReasoningEfforts: {list(model.supported_reasoning_efforts)}"
        )
    return t


def validate_all_routes(snapshot: ModelCatalogSnapshot) -> dict[ThreadRole, str]:
    """Returns a role → error message map for the routes that cannot be served."""
    problems: dict[ThreadRole, str] = {}
    for role in ThreadRole:
        try:
            validate_route(role, snapshot)
        except RouteUnavailable as exc:
            problems[role] = str(exc)
    return problems


def check_reroute(requested: Route, effective_model: str | None, effort_observable: bool) -> None:
    """The reaction to `model/rerouted` — L5402, L5641.

    If the runtime rerouted to another model or the effort stopped being observable,
    that is `ROUTE_MISMATCH` and a commit block. We do not fake a value we cannot see.
    """
    if effective_model is not None and effective_model != requested.model:
        raise RouteMismatch(
            f"runtime użył {effective_model!r} zamiast zadanego {requested.model!r} — "
            "commit zablokowany"
        )
    if not effort_observable:
        raise RouteMismatch(
            "effective effort jest nieobserwowalny (UNOBSERVED) — commit zablokowany, "
            "bo wynik nie da się przypisać do zadanej trasy"
        )


def env_mismatch() -> str | None:
    """Whether `WENEXT_CODEX_MODEL`/`WENEXT_CODEX_EFFORT` from compose agree with the table.

    Compose passes these variables to the `codex` service. The route table is the source of
    truth; the environment variables are to agree with it, and not to override it — otherwise
    a "fixed, explicitly written down route" would be fixed only in the file.
    """
    model = os.environ.get("WENEXT_CODEX_MODEL")
    effort = os.environ.get("WENEXT_CODEX_EFFORT")
    if model is None and effort is None:
        return None
    default = ROUTES[ThreadRole.WORKER]
    mismatches = []
    if model is not None and model != default.model:
        mismatches.append(f"WENEXT_CODEX_MODEL={model!r} ≠ TRASY[WORKER].model={default.model!r}")
    if effort is not None and effort != default.effort:
        mismatches.append(
            f"WENEXT_CODEX_EFFORT={effort!r} ≠ TRASY[WORKER].effort={default.effort!r}"
        )
    return "; ".join(mismatches) or None
