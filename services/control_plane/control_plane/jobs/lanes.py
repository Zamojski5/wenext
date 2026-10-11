"""Two lanes of the queue per organization: jobs that start a Codex engine, and everything else.

WHY LANES EXIST (measured on live 23.09 and 26.09). One dispatcher per organization claimed up to
eight jobs and ran them strictly one after another. That had two costs, both observed:

  1. A conversation turn may run for hours (`sessions/handlers.py`, 180 min budget). While it ran,
     NOTHING else of that organization moved: no publisher tick, no visibility check, no
     observation poll. On 23.09 the agent drafted a reply, issued it and then waited INSIDE its own
     turn for a publication that could only happen after the turn ended; the "czekam" loop in
     `docs/PROBA-RDZENIA-2026-09-23.md` is that deadlock seen from the chat.
  2. The seven jobs claimed behind a slow one held leases that nobody renewed. After 60 s the
     reaper took them back, and when their turn finally came their results were refused
     ("wynik SUCCEEDED joba ... ODRZUCONY", dozens per hour after the 26.09 outage). Work done
     twice, results thrown away.

WHY NOT SIMPLY RUN EVERYTHING CONCURRENTLY. L2159: two Codex App Servers on one `CODEX_HOME` are
forbidden, because a parallel OAuth refresh rewrites `auth.json` under the other process
(`workspace/codex_home.py`, `docs/E9-DOM-NA-TURE.md` point 4). The sequential dispatcher was the
thing that kept that promise, silently. So the jobs that start an engine stay in ONE lane and keep
running one at a time; only the jobs that never start an engine move to a lane of their own.

THE LIST IS LITERAL ON PURPOSE. The job-type constants live in the handler modules
(`sessions/handlers.py`, `presence/handlers.py`, `observe/scheduling.py`, `pilot/handlers.py`,
`workspace/handlers.py`), which import the queue; importing them here would make `jobs/` depend
on its own callers. `tests/test_job_lanes.py` pins every literal to its constant, so a renamed
type fails a test instead of silently moving into the wrong lane.

A new job type that starts an engine and is NOT added here would run next to a conversation turn
on the same home. That is the failure this module can cause, and the test above cannot see it:
whoever adds an engine job adds it here.
"""

from __future__ import annotations

ENGINE_LANE = "engine"
IO_LANE = "io"

#: Job types whose handler starts a Codex App Server (a model turn, a login, an account read).
ENGINE_JOB_TYPES: frozenset[str] = frozenset(
    {
        "session.turn",
        "presence.tick",
        "judge.thread",
        "understand.community_picture",
        "understand.community_voice",
        "pilot.cycle",
        "codex.connection.login",
        "codex.connection.read",
    }
)
