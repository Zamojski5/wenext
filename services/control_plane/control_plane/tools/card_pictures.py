"""The one app-only tool that gives the card its pictures (v11, 13.10).

The card draws a post the way the platform does: the author's photo, the post's first image, our own
avatar under it. It is one self-contained page with an empty content security policy, so it does
not point an image at the internet: it asks THIS tool, once per new set of addresses, and paints
the small inline images that come back (`opportunities/pictures.py` says how they are fetched and
bounded). Built like the card's other tools: `_meta.ui.visibility: ["app"]`, listed only for a
host that draws a card, never in the catalog the model reads.

THREAT MODEL, AS AN ADDITION TO `card_send.py`:

  1. IT READS, IT NEVER WRITES. No token, because nothing it does changes anything: it fetches
     pictures from the platforms' own image hosts and returns them. The model has no such tool.
  2. IT IS NOT A PROXY. An address that is not an https address on a platform picture host
     (`avatars.PICTURE_HOSTS`) is not fetched, whoever wrote it into a post or a profile; at most
     `MAX_URLS` addresses a call, no redirects, a size cap, only image types the page may draw (no
     SVG), five seconds a picture. A picture that fails is absent from the answer and the card
     keeps its initials.
  3. WHAT COMES BACK IS DATA THE PAGE PAINTS, never markup: a `data:image/...;base64,` string put
     in an `img` element's `src`.
"""

from __future__ import annotations

from typing import Any

from control_plane.opportunities.avatars import PICTURE_HOSTS
from control_plane.opportunities.pictures import MAX_URLS, fetch_pictures

CARD_PICTURE_TOOL_SPECS = (
    {
        "name": "room.pictures",
        "summary": "TYLKO DLA KARTY: małe obrazy autorów i postów jako dane do wklejenia w kartę.",
        "detail": (
            "Narzędzie dla karty Pokoju w agencie, niewidoczne dla modelu (visibility: app). "
            "Dostaje adresy obrazów, które karta zobaczyła w room.show (zdjęcie autora, pierwszy "
            "obraz posta, zdjęcie konta), i oddaje każdy jako mały obraz `data:` w `obrazy`, bo "
            "polityka treści karty jest pusta i zdalny obraz mógłby się nie załadować. Pobiera "
            "tylko https z hostów obrazów platform, bez przekierowań, z limitem rozmiaru i "
            "czasu; adres spoza listy albo obraz, którego nie udało się pobrać, po prostu "
            "nie ma w odpowiedzi. Niczego nie zapisuje."
        ),
        "state": "DOSTEPNE",
        "input_schema": {
            "type": "object",
            "properties": {
                "adresy": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": MAX_URLS,
                    "description": f"Adresy https z hostów: {', '.join(PICTURE_HOSTS)}.",
                }
            },
            "required": ["adresy"],
            "additionalProperties": False,
        },
        "tags": ("karta",),
    },
)


class CardPictureTools:
    """Mixin of `ToolRegistry`: `room.pictures`."""

    async def _room_pictures(self, args: dict[str, Any]) -> dict[str, Any]:
        from .registry import ToolError, done

        asked = args.get("adresy")
        if not isinstance(asked, list) or not all(isinstance(one, str) for one in asked):
            raise ToolError("adresy to lista adresów obrazów")
        return done(obrazy=await fetch_pictures(asked[:MAX_URLS]))


__all__ = ["CARD_PICTURE_TOOL_SPECS", "CardPictureTools"]
