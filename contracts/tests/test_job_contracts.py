"""Job contracts: the fencing token and the transport port (L5202, L5208).

The point of `JobTransportPort` is a boundary: swapping the transport in E3 must not touch a
single domain command (L5208). A boundary that imports a database driver is not a boundary,
so this file scans the module's AST — a grep in a Makefile recipe is not a test.
"""

from __future__ import annotations

import abc
import ast
import inspect
from pathlib import Path
from typing import get_type_hints
from uuid import uuid4

import pytest
from pydantic import ValidationError

from community_os_contracts import (
    JOB_TRANSPORT_PORT_METHODS,
    JobFencingToken,
    JobTransportPort,
    StaleJobFencingToken,
    require_fresh_job_lease,
)
from community_os_contracts import jobs as jobs_module

#: Names that must not appear anywhere in `jobs.py` — neither in an import, nor in a comment.
#: `postgres` is here in lower case only, exactly like the DoD grep; the module avoids the word
#: in every spelling anyway.
FORBIDDEN_IN_THE_PORT_MODULE = ("asyncpg", "psycopg", "postgres", "sqlalchemy")


def _token(**overrides) -> JobFencingToken:
    base = {
        "job_id": uuid4(),
        "attempt_id": uuid4(),
        "lease_generation": 1,
        "lease_owner": uuid4(),
    }
    base.update(overrides)
    return JobFencingToken(**base)


def test_job_fencing_token_carries_all_four_fields() -> None:
    """The fields are named one by one on purpose. Counting them would pass on four WRONG
    fields, and this token is what stops a second worker from taking the same record."""
    token = _token(lease_generation=3)

    assert token.job_id is not None
    assert token.attempt_id is not None
    assert token.lease_generation == 3
    assert token.lease_owner is not None

    fields = set(JobFencingToken.model_fields)
    assert "job_id" in fields
    assert "attempt_id" in fields
    assert "lease_generation" in fields
    assert "lease_owner" in fields
    assert fields == {"job_id", "attempt_id", "lease_generation", "lease_owner"}, (
        f"token ma pola {sorted(fields)} — zmiana składu tokenu zmienia znaczenie fencingu"
    )

    # Frozen: a token that can be edited after being handed out fences nothing.
    with pytest.raises(ValidationError):
        token.lease_generation = 4

    # Closed: an unknown field must not be silently carried along.
    with pytest.raises(ValidationError):
        JobFencingToken(
            job_id=uuid4(),
            attempt_id=uuid4(),
            lease_generation=1,
            lease_owner=uuid4(),
            workspace_id=uuid4(),
        )

    # Generation counts from 1 and never goes below it (the same rule as L2372 for profiles).
    with pytest.raises(ValidationError):
        _token(lease_generation=0)
    with pytest.raises(ValidationError):
        _token(lease_generation=-1)


def test_job_transport_port_is_a_protocol_without_a_driver_import() -> None:
    """A structural port, not an abstract class, and with no knowledge of any driver."""
    source_path = Path(inspect.getsourcefile(jobs_module) or "")
    assert source_path.name == "jobs.py", f"nie znaleziono źródła modułu: {source_path}"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")

    # Cardinality before the predicate: an empty import list would make the next assertion
    # trivially true, and a module that failed to parse would look clean.
    assert imported, "moduł nie ma żadnych importów — czy AST na pewno się sparsowało?"
    driver_imports = [
        name
        for name in imported
        for forbidden in FORBIDDEN_IN_THE_PORT_MODULE
        if forbidden in name.lower()
    ]
    assert not driver_imports, f"port importuje sterownik bazy: {driver_imports}"

    lowered = source.lower()
    present = [word for word in FORBIDDEN_IN_THE_PORT_MODULE if word in lowered]
    assert not present, (
        f"jobs.py wspomina {present} — port ma nie wiedzieć, co jest po drugiej stronie (L5208)"
    )

    assert getattr(JobTransportPort, "_is_protocol", False) is True, (
        "JobTransportPort nie jest typing.Protocol"
    )
    assert abc.ABC not in JobTransportPort.__mro__, (
        "port jest ABC — dziedziczenie po nim wiąże implementację transportu z kontraktami"
    )

    assert len(JOB_TRANSPORT_PORT_METHODS) >= 3

    # BOTH directions. The tuple → protocol direction alone would let a method be added to the
    # Protocol without being added to the tuple, and the vocabulary scan below iterates over the
    # tuple — so an unlisted method returning a connection would never be looked at.
    declared = {name for name in JOB_TRANSPORT_PORT_METHODS}
    on_the_protocol = {
        name
        for name in vars(JobTransportPort)
        if not name.startswith("_") and callable(vars(JobTransportPort)[name])
    }
    assert declared == on_the_protocol, (
        f"JOB_TRANSPORT_PORT_METHODS wymienia {sorted(declared)}, a na porcie jest "
        f"{sorted(on_the_protocol)} — lista i port muszą się zgadzać w obie strony"
    )

    # The port speaks in job identifiers and tokens. This is checked, not asserted in a comment:
    # a signature naming a connection, a cursor or a session would put the storage back inside
    # the seam that E3 is supposed to be able to replace (L5208, L5216).
    hints = {
        name: get_type_hints(getattr(JobTransportPort, name)) for name in JOB_TRANSPORT_PORT_METHODS
    }
    assert len(hints) == len(JOB_TRANSPORT_PORT_METHODS), "port nie ma opisanych sygnatur"
    vocabulary = sorted(
        {
            f"{method}.{parameter}: {annotation}"
            for method, annotations in hints.items()
            for parameter, annotation in annotations.items()
            for forbidden in (*FORBIDDEN_IN_THE_PORT_MODULE, "connection", "cursor", "session")
            if forbidden in str(annotation).lower()
        }
    )
    assert not vocabulary, f"sygnatura portu niesie typ warstwy składowania: {vocabulary}"


def test_a_stale_job_lease_generation_is_rejected() -> None:
    """The same rule as for the profile lease (L2377): an old generation does not execute."""
    current = _token(lease_generation=7)
    stale = current.model_copy(update={"lease_generation": 6})

    require_fresh_job_lease(current=current, presented=current)
    with pytest.raises(StaleJobFencingToken):
        require_fresh_job_lease(current=current, presented=stale)


def test_a_token_for_another_job_is_rejected() -> None:
    """A fresh generation of the WRONG job must not pass — two workers, one record (L5211)."""
    current = _token(lease_generation=2)
    other = _token(lease_generation=2)
    with pytest.raises(StaleJobFencingToken):
        require_fresh_job_lease(current=current, presented=other)
