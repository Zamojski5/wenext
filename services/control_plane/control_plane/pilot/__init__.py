"""The 24 h pilot (`docs/START-PILOTA-24H.md`): read real conversations of one community for a
fixed window, pick moments, and PERSIST a draft, a WAIT or a SILENCE through the same generator
the evals measure (`observe/reactions.py`). Nothing here publishes anything.

* `store.py`: the manifest (`pilot_runs`), the cycles and the reactions, with the model-call
  budget reserved in the database before every call;
* `situation.py`: a fresh thread read → `ReactionSituation` (target and its parent chain kept
  when clipping) and the situation hash used to refuse a second reaction to the same version of
  a conversation;
* `handlers.py`: the `pilot.cycle` job: one chained job per 30 min slot;
* `cli.py`: `start | status | stop | report`, run inside the `api` container by the Makefile;
* `report.py`: the morning report in Markdown.
"""
