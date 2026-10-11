"""Conversation evals (R1 of `docs/ROADMAP-JAKOSC-ROZMOW.md`): episodes, the baseline generator
run through the product's `ModelTurnPort`, counters and a review report.

`schema.py` is the episode contract (owner: the backend session). `runner.py` runs the generator
of `control_plane.observe.reactions` over episodes and writes `artifacts/conversation-quality/
<run-id>/`. Nothing here publishes anything anywhere.
"""
