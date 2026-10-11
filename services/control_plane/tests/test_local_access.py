"""Tryb lokalny: kiedy wolno otworzyć sesję bez hasła, a kiedy jest to odmowa.

BEZ BAZY I BEZ SERWERA, bo cała reguła jest czystą funkcją i taka ma zostać. Warunek
bezpieczeństwa sprawdzany wyłącznie w trasie HTTP byłby warunkiem, którego nie da się przejrzeć
bez postawienia stacku, a to jest dokładnie ten rodzaj reguły, który trzeba umieć przeczytać
w pięć sekund.

KAŻDY PRZYPADEK NIŻEJ TO JEDNA DROGA, KTÓRĄ KTOŚ MÓGŁBY WEJŚĆ BEZ HASŁA: wyłączony przełącznik,
publiczny adres API, publiczny adres dzwoniącego, wdrożenie z domeną, wdrożenie z brzegiem
wystawionym na świat.
"""

from __future__ import annotations

import pytest
from control_plane.identity.local_access import (
    EDGE_BIND_VARIABLE,
    LOCAL_AUTOLOGIN_VARIABLE,
    PUBLIC_DOMAIN_VARIABLE,
    deployment_is_local,
    who_is_armed,
)

ARMED = {LOCAL_AUTOLOGIN_VARIABLE: "wlasciciel@firma.test"}


def test_the_switch_is_off_by_default() -> None:
    assert who_is_armed({}) == ""
    allowed, reason = deployment_is_local(server_host="127.0.0.1", client_host="127.0.0.1", env={})
    assert allowed is False
    assert LOCAL_AUTOLOGIN_VARIABLE in reason


def test_a_local_machine_with_the_switch_on_is_allowed() -> None:
    allowed, reason = deployment_is_local(
        server_host="127.0.0.1", client_host="127.0.0.1", env=ARMED
    )
    assert allowed is True, reason


def test_containers_talking_over_the_compose_network_count_as_local() -> None:
    """Dashboard i API rozmawiają po sieci compose, więc adresy są prywatne, nie pętla zwrotna.

    Dosłowny warunek „bind równa się 127.0.0.1" byłby tu zawsze fałszywy i funkcja nie zadziałałaby
    nigdy; o dostępności z zewnątrz decydują warunki o domenie i brzegu, sprawdzane osobno.
    """
    allowed, reason = deployment_is_local(
        server_host="172.18.0.4", client_host="172.18.0.7", env=ARMED
    )
    assert allowed is True, reason


@pytest.mark.parametrize("host", ["8.8.8.8", "51.83.10.4", "2001:4860:4860::8888"])
def test_a_public_api_address_is_refused(host: str) -> None:
    allowed, reason = deployment_is_local(server_host=host, client_host="127.0.0.1", env=ARMED)
    assert allowed is False
    assert "nie jest adresem lokalnym" in reason


@pytest.mark.parametrize("host", ["8.8.8.8", "2001:4860:4860::8888", None, "", "0.0.0.0"])
def test_a_caller_that_is_not_local_is_refused(host: str | None) -> None:
    """`0.0.0.0` też: to nie jest adres, z którego ktoś dzwoni, tylko „słucham na wszystkim"."""
    allowed, _ = deployment_is_local(server_host="127.0.0.1", client_host=host, env=ARMED)
    assert allowed is False


def test_a_deployment_with_a_domain_refuses_even_with_the_switch_on() -> None:
    """To jest ten warunek, który chroni serwer: nakładka produkcyjna ustawia domenę."""
    allowed, reason = deployment_is_local(
        server_host="127.0.0.1",
        client_host="127.0.0.1",
        env={**ARMED, PUBLIC_DOMAIN_VARIABLE: "wenext.example"},
    )
    assert allowed is False
    assert PUBLIC_DOMAIN_VARIABLE in reason


@pytest.mark.parametrize("bind", ["0.0.0.0", "::", "51.83.10.4"])
def test_an_edge_bound_outside_the_loopback_refuses(bind: str) -> None:
    allowed, reason = deployment_is_local(
        server_host="127.0.0.1", client_host="127.0.0.1", env={**ARMED, EDGE_BIND_VARIABLE: bind}
    )
    assert allowed is False
    assert EDGE_BIND_VARIABLE in reason


def test_an_edge_bound_to_the_loopback_is_still_local() -> None:
    allowed, reason = deployment_is_local(
        server_host="127.0.0.1",
        client_host="127.0.0.1",
        env={**ARMED, EDGE_BIND_VARIABLE: "127.0.0.1"},
    )
    assert allowed is True, reason


def test_a_hostname_we_cannot_resolve_is_not_local() -> None:
    """Nazwa wskazująca w czyimś `/etc/hosts` na pętlę zwrotną jest z serwera nie do sprawdzenia."""
    allowed, _ = deployment_is_local(
        server_host="wenext.example", client_host="127.0.0.1", env=ARMED
    )
    assert allowed is False
