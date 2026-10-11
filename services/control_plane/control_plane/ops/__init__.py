"""Gotowość operacyjna: czy usługa chodziła, co padło, ile to kosztowało (P7 etap B).

Cztery rzeczy, które trzeba umieć powiedzieć o usłudze działającej bez laptopa autora, i nic
ponadto: puls komponentów, incydenty, koszt w jednostkach i sufit na organizację. Kopie zapasowe
są POZA tym pakietem, w `tools/dev/backup.sh`: plik kopii nie może zależeć od bazy, którą się
odtwarza.
"""

from .handlers import seed_ops_chain
from .health import (
    COMPONENT_CAPSULE,
    COMPONENT_CODEX,
    COMPONENT_COLLECTOR,
    COMPONENT_DISPATCHER,
    COMPONENT_PUBLISHER,
    COMPONENTS,
    DETECTED_BY_HUMAN,
    DETECTED_BY_MISSING,
    DETECTED_BY_SELF,
    HealthStore,
    IncidentRow,
    availability,
)
from .ledger import Refill, refill
from .limits import OrgLimits, OrgSpend, org_limits_for, org_spend

__all__ = [
    "COMPONENTS",
    "COMPONENT_CAPSULE",
    "COMPONENT_CODEX",
    "COMPONENT_COLLECTOR",
    "COMPONENT_DISPATCHER",
    "COMPONENT_PUBLISHER",
    "DETECTED_BY_HUMAN",
    "DETECTED_BY_MISSING",
    "DETECTED_BY_SELF",
    "HealthStore",
    "IncidentRow",
    "OrgLimits",
    "OrgSpend",
    "Refill",
    "availability",
    "org_limits_for",
    "org_spend",
    "refill",
    "seed_ops_chain",
]
