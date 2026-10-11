"""Eval harness of phase E1.F0 — D17.

Scope L5168: "30-50 fixture evals for discovery, discussion quality classification
and reply drafting". A formal bridge to phase F3: DoD F3 says that the definition
of a "valuable discussion" passes the fixture evals from E1.F0 (L5283).

What these evals DO PROVE:
    the harness works offline, the quality definition from section 22.7 is ENCODED
    in executable code, and the result is reproducible.

What they DO NOT prove:
    anything about the predictive quality of the model. In F0 there is no ground truth
    (L5514), so `tools/gonogo/criteria.yaml` has `evale.prog_zdawalnosci: 0.0` and the
    harness sets no percentage threshold whatsoever. See NOTES.md, section "Próg".
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
