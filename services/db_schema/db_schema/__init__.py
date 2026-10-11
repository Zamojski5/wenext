"""Schema of the E1.F1 kernel: the migrations and the invariants they have to hold.

The package deliberately holds no data access layer. Reading and writing rows belongs to the
control plane; what lives here is the schema itself and the tests that prove PostgreSQL — not
application code — rejects what must be rejected (D-020: a child pointing at a parent from
another organization is refused by the database).
"""

from db_schema.migrations import (
    DIRECTORY_VARIABLE,
    MIGRATIONS_DIR,
    migration_files,
    migration_number,
)

__all__ = [
    "DIRECTORY_VARIABLE",
    "MIGRATIONS_DIR",
    "migration_files",
    "migration_number",
]
