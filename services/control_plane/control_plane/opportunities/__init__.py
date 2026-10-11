"""Opportunities: live conversations worth the founder's attention, and the page that shows them.

`store.py` is the table and the derived state, `ingest.py` makes a live Reddit thread publishable,
`room.py` builds the payload of the "Pokój" page. See D-055.
"""

from .store import (
    OPEN_STATUSES,
    STATUSES,
    NewOpportunity,
    OpportunityRefused,
    OpportunityStore,
    OpportunityView,
    new_opportunity,
    room_path,
    status_of,
)

__all__ = [
    "OPEN_STATUSES",
    "STATUSES",
    "NewOpportunity",
    "OpportunityRefused",
    "OpportunityStore",
    "OpportunityView",
    "new_opportunity",
    "room_path",
    "status_of",
]
