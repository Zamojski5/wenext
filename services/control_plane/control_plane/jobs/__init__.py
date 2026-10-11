"""The job queue of phase E1.F1 — PostgreSQL as canonical state (L5193, L5194).

Three modules and one rule about who may import which:

  * `store.py` — every SQL statement of the queue. The only file here that knows `asyncpg`.
  * `postgres_transport.py` — `JobQueuePort` over that store. What E3 replaces with a broker.
  * `devcli.py` — the development path from D-013. Not a producer of production data.

`api/` and the CLI reach the queue through `control_plane.domain.commands`, never through this
package directly. The domain layer is the checked entrance; a second one is the one that stops
being checked.
"""
