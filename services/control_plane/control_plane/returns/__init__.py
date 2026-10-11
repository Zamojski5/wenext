"""Widok powrotu: co się wydarzyło, odkąd człowieka nie było (P4 i P5).

Jeden moduł, bo to jest jedno pytanie zadane siedmiu tabelom. Rozbicie go na siedem miejsc
skończyłoby się siedmioma definicjami okna i pierwszą rozbieżnością przy pierwszej zmianie.
"""

from .store import (
    VISIT_GRACE_MINUTES,
    ReturnStore,
    ReturnView,
    Window,
)

__all__ = ["VISIT_GRACE_MINUTES", "ReturnStore", "ReturnView", "Window"]
