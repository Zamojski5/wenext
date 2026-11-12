"""`playbook.read`: what worked in what was published, before the agent drafts the next one (13.10).

The founder: "powinno się uczyć". The learning loop (`control_plane/learning`) measures every
published X and Reddit item at about 1, 6, 24 and 72 hours, compares groups of them with a medians
and an n-guard, and keeps the clear differences in the project's memory as lessons. This tool is
the way those lessons reach the agent at the moment it matters: it is called BEFORE drafting, and
it returns the lessons with their numbers, the seeded rules, the best past replies and own posts
with their text and counters, how much data there is, and the founder's rules for the tone of a
reply.

READ-ONLY, NO ARGUMENTS THAT REACH ANYWHERE. It reads rows of this organization and workspace
(and this project's, plus the workspace-wide ones), writes nothing and calls no platform. Its
answer is ours, measured: the best-reply texts are the founder's own replies, not a stranger's
words, so none of it is "external material".

NO MODULE-LEVEL IMPORT FROM `catalog` OR `registry`, as in `knowledge_tools.py`: the catalog
imports this file, so the specs are plain data and the helpers are imported inside the method.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

PLAYBOOK_TOOL_SPECS = (
    {
        "name": "playbook.read",
        "summary": (
            "Co się sprawdziło w opublikowanych odpowiedziach i postach: lekcje z liczbami, "
            "najlepsze teksty i zasady tonu. Zawołaj PRZED każdym szkicem."
        ),
        "detail": (
            "Zwraca (1) `lekcje`: wnioski policzone z pomiarów po około 1, 6, 24 i 72 "
            "godzinach, każdy z porównywanymi grupami, ich medianami (wyświetlenia, polubienia) "
            "i liczebnością `n`; wniosek pojawia się dopiero przy co najmniej trzech pozycjach "
            "w każdej z dwóch grup i wyraźnej różnicy, (2) `zasady`: reguły zapisane w pamięci, "
            "oznaczone jako reguły, nie pomiary, (3) `glos`: zasady tonu odpowiedzi od "
            "założyciela, (4) `najlepsze_odpowiedzi` i `najlepsze_posty`: tekst i liczby tego, "
            "co dotąd miało największy zasięg, z wiekiem posta, pod którym padła odpowiedź, "
            "i wielkością jego autora, (5) `pokrycie`: ile pozycji jest śledzonych i ile ma "
            "pomiar na danym horyzoncie.\n"
            "Stosuj lekcje tam, gdzie pasują do posta, który masz przed sobą; wyraźnie lepszy "
            "kąt w samym poście jest ważniejszy. To obserwacje z naszej własnej, małej "
            "historii, nie przyczyny. Gdy lekcji nie ma (`brak_lekcji`), pisz według zasad "
            "i głosu i nie zgaduj lekcji. Nie cytuj liczby, której nie ma w tej odpowiedzi. "
            "Tylko odczyt."
        ),
        "state": "DOSTEPNE",
        "input_schema": {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": ["X", "REDDIT"],
                    "description": "Tylko lekcje i przykłady z tej platformy. Domyślnie obie.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "Ile najlepszych odpowiedzi i postów. Domyślnie 5.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        "tags": ("pamięć", "uczenie"),
    },
)


class PlaybookTools:
    """Mixin of `ToolRegistry`: the one read of the learning loop."""

    async def _playbook_read(self, args: dict[str, Any]) -> dict[str, Any]:
        from control_plane.learning.playbook import read_playbook

        from .registry import ToolError, done

        platform = args.get("platform")
        if platform is not None and platform not in ("X", "REDDIT"):
            raise ToolError("playbook.read: platform to X albo REDDIT")
        limit = args.get("limit")
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
            raise ToolError("playbook.read: limit musi być liczbą całkowitą")
        capability = self.capability  # type: ignore[attr-defined]
        project_id: UUID | None = capability.project_id
        return done(
            **await read_playbook(
                self.pool,  # type: ignore[attr-defined]
                organization_id=capability.organization_id,
                workspace_id=capability.workspace_id,
                project_id=project_id,
                platform=platform,
                limit=limit or 5,
            )
        )
