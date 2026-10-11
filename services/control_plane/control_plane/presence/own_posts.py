"""Kiedy wolno wrzucić WŁASNY post do społeczności (P4 etap C).

DLACZEGO TO JEST OSOBNA BRAMKA, A NIE „jeszcze jeden rodzaj publikacji". Odpowiedź pod cudzym
wątkiem wchodzi w rozmowę, która już trwa, i najgorsze, co może zrobić, to być nie na temat.
Własny post zabiera uwagę CAŁEJ społeczności i w większości miejsc na Reddicie jest dokładnie
tym, za co moderacja banuje najszybciej, zwłaszcza konto, które wcześniej nic tam nie napisało.

ROZSTRZYGA CZŁOWIEK, I MA NA TO SWOJE MIEJSCE (0025: `communities.own_posts_allowed`). Trzy
stany, bo trzy są naprawdę: NULL to „nikt nie zdecydował", false to „nie wolno", true to „wolno".
NULL JEST ODMOWĄ, tak samo jak false, ale mówi co innego i co innego ma po nim zrobić człowiek:
przy NULL ma zdecydować, przy false nie ma nic do roboty. Brak zasady nie znaczy, że wolno
wszystko; to jest ta sama reguła, co „brak danych to nie zero".

DLACZEGO NIE KARTA GŁOSU, skoro to ona opisuje społeczność. Karta (0005) jest WNIOSKIEM MODELU z
przeczytanych komentarzy: ma wersję, okno, nazwę promptu i nazwę modelu, bo to artefakt
obserwacji. Zgoda na publikowanie we własnym imieniu w cudzej społeczności jest decyzją, za którą
ktoś odpowiada, a model nie ma jak wziąć na siebie tego, co się potem stanie. Gorzej: następna
wersja karty, wygenerowana automatycznie, skasowałaby tę decyzję bez śladu. Karta zostaje tym, po
co powstała: kontekstem, który człowiek czyta PRZED kliknięciem przełącznika.

Do 19.09 bramka czytała klucz `own_posts` z karty i była zamknięta na głucho, bo nic tego klucza
nie zapisywało. Zbudowana ścieżka, której nie da się włączyć, jest tym samym, co jej brak, tylko
droższym w utrzymaniu.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {"wolno": self.allowed, "powod": self.reason}


async def may_post_in(
    conn: asyncpg.Connection,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    platform: str,
    community_key: str,
) -> Verdict:
    """Czy w tej społeczności wolno opublikować własny post.

    Trzy odpowiedzi, trzy różne rzeczy do zrobienia przez człowieka: nie znamy tej społeczności
    (dodaj ją i poobserwuj), nikt nie zdecydował (zdecyduj), człowiek powiedział nie (nic).

    PYTA O CAŁĄ CZWÓRKĘ, i to jest poprawka z przeglądu krzyżowego 19.09: unikalność w
    `communities` stoi na `(organization_id, workspace_id, platform, community_key)`, a pytanie
    o samą organizację i nazwę brało wiersz z CUDZEJ przestrzeni tej samej organizacji, czyli
    decyzję podjętą przez kogoś, kto tej społeczności nie prowadzi.

    NIE CZYTA KARTY GŁOSU, choć kiedyś czytała: bramka pyta o decyzję, a karta jest obserwacją.
    Kartę pokazuje EKRAN, obok przełącznika, żeby człowiek decydował z kontekstem w oczach.
    """
    row = await conn.fetchrow(
        """
        SELECT c.id, c.own_posts_allowed
          FROM communities c
         WHERE c.organization_id = $1
           AND c.workspace_id = $2
           AND c.platform = $3
           AND c.community_key = $4
        """,
        organization_id,
        workspace_id,
        platform,
        community_key,
    )
    if row is None:
        return Verdict(
            False,
            f"społeczność {community_key} nie jest obserwowana w tej przestrzeni; nie wiemy "
            "o niej nic, a własny post w nieznanym miejscu to strzał w ciemno",
        )
    # ROZSTRZYGA CZŁOWIEK, W SWOJEJ KOLUMNIE (0025). Karta głosu jest wnioskiem modelu z
    # przeczytanych komentarzy i zostaje kontekstem, który człowiek ogląda przed decyzją; nie
    # jest zgodą, bo model nie ma jak wziąć na siebie tego, co się stanie w cudzej społeczności.
    decided = row["own_posts_allowed"]
    if decided is True:
        return Verdict(True, f"człowiek dopuścił własne posty w {community_key}")
    if decided is False:
        return Verdict(
            False,
            f"człowiek nie dopuścił własnych postów w {community_key}. Tu nie ma nic do "
            "zrobienia po Twojej stronie: odpowiadaj w cudzych wątkach",
        )
    return Verdict(
        False,
        f"nikt jeszcze nie zdecydował, czy w {community_key} wolno zakładać własne wątki, a "
        "brak zasady nie znaczy, że wolno wszystko. Przygotuj w rozmowie kartę "
        "delegation.propose z OWN_POST dla tej społeczności. Jeden podpis powierza zakres; "
        "potem nie trzeba zatwierdzać każdego posta. Wcześniejszy zakaz wymaga osobnej zmiany.",
    )
