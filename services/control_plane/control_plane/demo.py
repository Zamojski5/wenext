"""The recording sandbox: an organization that exists to be filmed and that nothing may act for.

WHY THIS FILE EXISTS. The launch film shows the real plugin in a real Codex window, with fictional
people and a fictional product ("tallymark"). `scripts/demo/seed_tallymark.py` puts that world in
the local database as an organization of its own. The stack that runs here also holds the
founder's real accounts and the capsules logged in as them, and the background work is wired once
for the whole process: a dispatcher for an organization reads Reddit and X and publishes through
THOSE capsules. A sandbox organization with a dispatcher would therefore, the moment the card sent
a draft, ask the founder's browser to look at x.com or to check who is logged in.

So the sandbox has NO dispatcher (`runtime._organizations` leaves it out). Its jobs are written
(the card's send queues a request and nudges the publisher) and never claimed, its chains are never
seeded, and nothing in it can reach a platform, whatever state the capsules are in. The film's last
beat, "Posted", is staged by `scripts/demo/seed_tallymark.py post`, which says so.

The id is spelled once, here, and the script spells it again on purpose: it runs inside the `api`
container as a piped file and must not need this module to exist there. A test compares the two.
"""

from __future__ import annotations

from uuid import UUID

#: `dddddddd-0000-4000-8000-000000000001`: not a random id, so a seed and a reset can be scoped to
#: it by a constant, and so nobody has to look it up to be sure which organization is meant.
DEMO_ORGANIZATION_ID = UUID("dddddddd-0000-4000-8000-000000000001")
DEMO_ORGANIZATION_IDS: frozenset[UUID] = frozenset({DEMO_ORGANIZATION_ID})


def is_demo_organization(organization_id: UUID) -> bool:
    return organization_id in DEMO_ORGANIZATION_IDS
