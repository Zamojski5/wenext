"""Podpis karty szkicu: nazwa społeczności, nigdy identyfikator celu.

DLACZEGO OSOBNY PLIK BEZ BAZY. Trasy zgody i poprawki mają testy z bazą (`test_project_approvals_
api.py`), ale dwie z trzech dróg tej funkcji rozstrzygają się PRZED zapytaniem: własny post ma
nazwę społeczności wprost w celu, a pusty cel nie ma czego pokazać. Te dwie da się zmierzyć bez
Postgresa, a atrapa puli, która wybucha przy dotknięciu, jest zarazem dowodem, że baza w tych
przypadkach nie jest pytana.

Skąd ten plik się wziął: zrzut rundy 2 z 21.09. Po poprawce karta miała w podpisie `t3_e2edraft`
zamiast `r/saas_e2e`. Dzień wcześniej naprawiono w tym samym wywołaniu TYTUŁ, a pole obok
zostało z tą samą pomyłką.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from control_plane.api.project_approvals import _community_caption


class _PoolThatMustNotBeUsed:
    """Pula, która nie daje połączenia. Test przechodzi tylko wtedy, gdy nikt o nie nie poprosi."""

    def acquire(self):
        raise AssertionError(
            "trasa poszła do bazy po nazwę, którą miała już w celu propozycji; przy własnym "
            "poście `target_external_id` JEST społecznością"
        )


@pytest.mark.parametrize("target", ["r/ecommerce", "r/saas_e2e", "Ask HN"])
async def test_an_own_post_is_signed_with_the_community_it_targets(target: str) -> None:
    caption = await _community_caption(
        _PoolThatMustNotBeUsed(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
        payload={"target_external_id": target},
    )

    assert caption == target


@pytest.mark.parametrize("payload", [{}, {"target_external_id": ""}, {"target_external_id": "  "}])
async def test_without_a_target_there_is_no_caption(payload: dict) -> None:
    """Pusty podpis mówi „nie wiem". To jest poprawna odpowiedź, a nie brak odpowiedzi."""
    caption = await _community_caption(
        _PoolThatMustNotBeUsed(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
        payload=payload,
    )

    assert caption is None


@pytest.mark.parametrize("target", ["t3_e2edraft", "t1_abc123", "t3_zgoda01"])
async def test_an_identifier_never_becomes_a_caption_on_its_own(target: str) -> None:
    """Identyfikator wątku ma prowadzić do zapytania o nazwę, a nie trafiać na ekran.

    Tu atrapa puli wybucha CELOWO: wyjątek jest dowodem, że kod poszedł po nazwę do bazy.
    Gdyby zwrócił napis bez pytania, byłaby to dokładnie usterka ze zrzutu.
    """
    with pytest.raises(AssertionError):
        await _community_caption(
            _PoolThatMustNotBeUsed(),
            organization_id=uuid4(),
            workspace_id=uuid4(),
            payload={"target_external_id": target},
        )
