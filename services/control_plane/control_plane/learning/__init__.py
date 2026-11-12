"""The learning loop of published replies and posts (13.10): measure, compare, remember, apply.

  * `model`: buckets, medians, the n-guard and the sentence of a lesson. Pure.
  * `store`: the two tables of migration 0040 and the reads over them.
  * `sources`: reading a published item's counters, the capsule first and the public mirror
    (`api.fxtwitter.com`) as the fallback.
  * `service`: one sweep (`learning.sweep`): register what was published, take the 1, 6, 24 and
    72 hour snapshots, enrich with the target's context, and turn the numbers into lessons.
  * `playbook`: what `playbook.read` hands the agent before it drafts.
  * `diary`: the founder's post log, imported once as the seed (`tools/dev/backfill_learning.py`).

The sweep runs inside the hourly experience sweep (`knowledge/handlers.py`), so there is no
second clock: the scheduler that already measures what came of a publication also measures how
it did over time.
"""
