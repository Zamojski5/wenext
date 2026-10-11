"""Granice zaplanowanego powrotu, w jednym miejscu (P4 etap C).

Osobny, mały moduł, bo te liczby czyta i NARZĘDZIE (`tools/registry.py`, żeby odmówić z
powodem), i TIK (żeby wiedzieć, ile powrotów wolno), i EKRAN (żeby napisać, co jest możliwe).
Trzy kopie tych samych progów rozjechałyby się przy pierwszej zmianie, a rozjazd byłby widoczny
dopiero jako narzędzie obiecujące coś, czego tik nie honoruje.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

#: Najwcześniejszy powrót. Krócej niż cykl tiku (10 min) nie ma sensu: agent, który planuje
#: powrót za minutę, szuka sposobu na czekanie w turze, a nie na odłożenie pracy.
MIN_AFTER_MINUTES = 5

#: Najpóźniejszy. Dalej niż tydzień to obietnica, o której nikt nie pamięta, a delegacja może
#: do tego czasu wygasnąć albo zostać odwołana.
MAX_AFTER_MINUTES = 7 * 24 * 60

#: Ile powrotów naraz może czekać na jeden projekt, gdy delegacja nie mówi inaczej.
DEFAULT_SCHEDULED_RETURNS = 10


def planned_moment(args: dict[str, Any], *, now: datetime | None = None) -> datetime:
    """`after_minutes` albo `at`, sprowadzone do momentu, z granicami.

    Przyjmuje DOKŁADNIE JEDNO z dwóch pól. Podanie obu jest odmową, a nie cichym wyborem
    jednego: model, który podał dwie różne daty, nie wie, kiedy chce wrócić, i lepiej, żeby się
    o tym dowiedział teraz niż z ekranu za tydzień.
    """
    moment = now or datetime.now(UTC)
    after = args.get("after_minutes")
    at = str(args.get("at") or "").strip()
    if after is not None and at:
        raise ValueError(
            "podaj `after_minutes` ALBO `at`, nie oba: dwie różne daty znaczą, że nie wiadomo, "
            "kiedy wrócić"
        )
    if after is None and not at:
        raise ValueError("podaj `after_minutes` albo `at`; powrót bez terminu nie jest powrotem")

    if at:
        try:
            parsed = datetime.fromisoformat(at)
        except ValueError as exc:
            raise ValueError(f"`at`={at!r} nie jest datą ISO 8601") from exc
        due = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    else:
        if isinstance(after, bool) or not isinstance(after, int):
            raise ValueError("`after_minutes` musi być liczbą całkowitą minut")
        due = moment + timedelta(minutes=after)

    minutes = (due - moment).total_seconds() / 60.0
    if minutes < MIN_AFTER_MINUTES:
        raise ValueError(
            f"najwcześniejszy powrót to {MIN_AFTER_MINUTES} min od teraz; poproszono o "
            f"{minutes:.0f}. Tik i tak chodzi co dziesięć minut"
        )
    if minutes > MAX_AFTER_MINUTES:
        raise ValueError(
            f"najpóźniejszy powrót to siedem dni; poproszono o {minutes / 1440:.1f} dnia. "
            "Dalej niż tydzień to obietnica, o której nikt nie pamięta"
        )
    return due
