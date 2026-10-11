"""How one tool call looks in a session's trace, in ONE place (P2, plugin sessions).

A tool call becomes two `session_events` rows, `TOOL_CALL` and `TOOL_RESULT`, and what goes into
them is not decoration: the screen reads the sentences, `registry._already_done` reads the
digest and the outcome to recognise a repeated write, and the cards projection reads `karty`.
Two writers produce those rows, and they must produce the same rows:

  * `handlers.SessionTurnHandlers._record_item`, for a session Codex runs on the control plane's
    behalf (the engine reports the call as a thread item);
  * `mcp_server.MCPToolServer`, for a session driven by the person's own agent through the plugin
    (there is no engine to report anything, so the tool server is the only one who saw the call).

Before this module the shaping lived inside the first of them as private helpers. A second copy
in the tool server would have drifted the first time somebody changed a field, and the symptom,
a draft card that stops appearing in one of the two kinds of conversation, is exactly the kind
that no test of a single tool notices (KONWENCJE: klej między sprawdzonymi częściami). Everything
here is PURE: no database, no engine, and no import of the handler, because the tool server must
not depend on the job that starts the other kind of session.
"""

from __future__ import annotations

import re
from typing import Any


def args_digest(tool: str, arguments: Any) -> str:
    import hashlib
    import json

    canonical = json.dumps(
        {"tool": tool, "arguments": arguments}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: How many identifiers one tool result may contribute. A listing of a hundred findings is not a
#: hundred references; it is one call that saw a hundred things, and `result_refs` is read whole
#: by the screen.
MAX_RETURNED_IDS = 20

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def returned_ids(result: Any) -> list[str]:
    """Every identifier a tool result names, in the order it names them.

    WHY A WALK AND NOT A PER-TOOL READER. A table of "memory.list keeps its ids under `ustalenia`"
    is a second description of a shape somebody else owns, and it stops being true the first time
    they add a field. This collects values under keys called `id` or ending in `_id`, which is the
    convention every tool here already follows, and it keeps working when the shape around them
    changes.

    IT DOES NOT SCAN FREE TEXT. A UUID mentioned inside a finding's sentence is something the
    model read, not something the tool returned, and treating the two alike would let a reference
    be created by writing one down.
    """
    found: list[str] = []

    def walk(node: Any, key: str | None) -> None:
        if len(found) >= MAX_RETURNED_IDS:
            return
        if isinstance(node, dict):
            for name, value in node.items():
                walk(value, name)
        elif isinstance(node, list):
            for value in node:
                walk(value, key)
        elif (
            isinstance(node, str)
            and key is not None
            and (key == "id" or key.endswith("_id"))
            and _UUID.match(node)
            and node not in found
        ):
            found.append(node)

    walk(result, None)
    # MCP encodes our result as a JSON text block. Parse that envelope exactly once;
    # a UUID inside an article or a statement remains ordinary text, never a reference.
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        walk(first_json_block(result["content"]), None)
    return found


#: Co robi każde narzędzie, powiedziane czasownikiem i po polsku. Zamknięta mapa, a nie
#: generowanie z nazwy: „catalog.list" da się przetłumaczyć na „sprawdził, co umie", ale
#: „memory.write" na „zapisał" tylko wtedy, gdy ktoś to napisał. Narzędzie spoza mapy dostaje
#: swoją nazwę i tyle, bo zmyślony czasownik byłby zdaniem o czymś, czego nikt nie sprawdził.
TOOL_VERBS: dict[str, str] = {
    "guidance.list": "dobrał wiedzę do zadania",
    "guidance.read": "przeanalizował podejście do wzrostu",
    "news.search": "poszukał źródeł w newsletterze",
    "browser.inspect": "obejrzał stronę",
    "x.search": "przeszukał X",
    "x.read": "przeczytał stronę X",
    "x.watch": "ustawił, co obserwować na X",
    "x.trends": "sprawdził, co się niesie na X",
    "catalog.list": "sprawdził, czym dysponuje",
    "community.read": "przeczytał społeczność",
    "thread.read": "przeczytał wątek",
    "threads.list": "przejrzał listę wątków",
    "memory.search": "poszukał w pamięci projektu",
    "memory.write": "zapisał ustalenie",
    "knowledge.search": "poszukał w materiałach",
    "knowledge.add": "dodał materiał",
    "draft.create": "przygotował propozycję tekstu",
    "draft.mark_posted": "zapisał, że założyciel sam opublikował szkic",
    "accounts.list": "sprawdził konta projektu",
    "session.handoff": "zlecił pracę osobnej sesji",
    "session.result": "sprawdził wynik zleconej pracy",
    "session.schedule_return": "planuje powrót",
    "session.list_returns": "sprawdził zaplanowane powroty",
    "session.cancel_return": "odwołał zaplanowany powrót",
    "person.voice": "przeczytał głos osoby",
    # Obsługa produktu z rozmowy (D-046, E3). Bez tych wierszy człowiek czytał w swojej rozmowie
    # „wywołał communities.observe", czyli angielską nazwę techniczną w polskim zdaniu o własnej
    # pracy. Czasowniki nazywają SKUTEK, nie wywołanie: „zaczął obserwować" i „dołączył kontem"
    # to dla czytającego dwie różne rzeczy i taka właśnie jest różnica między tymi narzędziami.
    "catalog.describe": "sprawdził, jak działa narzędzie",
    "project.read": "przeczytał ustalenia projektu",
    "project.update": "zapisał cel projektu",
    "accounts.status": "sprawdził, czy konta działają",
    "accounts.connect": "poprosił o podłączenie konta",
    "communities.observe": "zaczął obserwować społeczność",
    "communities.join": "dołączył kontem do społeczności",
    "communities.leave": "przestał obserwować społeczność",
    "communities.list": "sprawdził obserwowane społeczności",
    "threads.search": "poszukał wątków",
    "comments.read": "przeczytał komentarze",
    "publications.list": "sprawdził, co już wysłano",
    "observations.read": "sprawdził skutek publikacji",
    "sessions.read": "przeczytał wcześniejszą rozmowę",
    "documents.list": "przejrzał dokumenty projektu",
    "documents.read": "przeczytał dokument projektu",
    "drafts.publish": "zgłosił tekst do publikacji",
    "drafts.read": "przeczytał przygotowany tekst",
    "delegation.propose": "zaproponował zakres powierzenia",
    "sources.search": "poszukał źródeł",
    "sources.read": "przeczytał źródło",
    "sources.list": "sprawdził materiały projektu",
    "sources.find": "wyszukał fragmenty materiału",
    "materials.save": "zapisał materiał w projekcie",
    "memory.list": "przejrzał ustalenia projektu",
    "memory.correct": "poprawił ustalenie",
    "memory.retract": "wycofał ustalenie",
    # Okazje (D-055): rozmowy na żywo, które agent znalazł i zapisał dla założyciela.
    "reddit.search": "poszukał rozmów na Reddicie",
    "reddit.read": "przeczytał wątek na Reddicie",
    "opportunities.mark": "zapisał rozmowę, w którą warto wejść",
    "opportunities.list": "przejrzał zapisane okazje",
    "room.show": "pokazał założycielowi Pokój",
    # Narzędzia karty (`tools/card_send.py`): wołane przez kartę po kliknięciu człowieka, nie przez
    # model. Są w śladzie, bo zapis pokazuje, kto co zrobił i kiedy.
    "room.send_token": "przygotował potwierdzenie wysyłki z karty",
    "room.send_draft": "wysłał szkic z karty po kliknięciu człowieka",
    "room.send_status": "sprawdził, co się stało z wysyłką z karty",
}


def call_summary(tool: str, arguments: Any) -> str:
    """Jedno zdanie o tym, co agent właśnie robi, z NAJWAŻNIEJSZYM argumentem w środku.

    Argument wybierany z zamkniętej listy kluczy, bo to człowiek ma to czytać: „przeczytał wątek
    t3_1w934kh" mówi coś, a „przeczytał wątek {'external_id': 't3_...', 'limit': 50}" nie.
    """
    verb = TOOL_VERBS.get(tool, f"wywołał {tool}")
    if isinstance(arguments, dict):
        for key in ("community", "community_key", "external_id", "thread_id", "query", "title"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return f"{verb}: {value.strip()[:80]}"
    return verb


def result_summary(tool: str, item: Any) -> str:
    """Jedno zdanie o tym, CZYM się skończyło. Błąd jest ważniejszy od statusu i idzie pierwszy."""
    data = item if isinstance(item, dict) else {}
    error = data.get("error")
    if error:
        return f"{tool}: błąd, {str(error)[:120]}"
    outcome = tool_outcome(data.get("result"))
    label = {"NIEDOSTEPNE": "niedostępne", "BLAD": "błąd", "OCZEKUJE": "oczekuje"}.get(outcome)
    if label:
        return f"{tool}: {label}"
    status = str(data.get("status") or "").strip()
    duration = data.get("durationMs")
    czas = f", {int(duration) / 1000:.1f} s" if isinstance(duration, int | float) else ""
    if status and status.lower() not in ("completed", "success", "ok"):
        return f"{tool}: {status}{czas}"
    return f"{tool}: gotowe{czas}"


def tool_outcome(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    inner = result
    if "stan" not in inner:
        inner = result.get("structuredContent") or first_json_block(result.get("content"))
    state = inner.get("stan") if isinstance(inner, dict) else None
    return state if state in ("WYKONANE", "OCZEKUJE", "NIEDOSTEPNE", "BLAD") else ""


def cards_of(result: Any) -> list[Any]:
    """Karty z wyniku narzędzia, jeżeli je ma. Kształt pilnuje `tools/cards.py`.

    WYNIK PRZYCHODZI W KOPERCIE MCP, nie jako goły słownik narzędzia: `tools/call` zwraca
    `{"content": [{"type": "text", "text": "<JSON>"}], "isError": false}`, bo tak wygląda wynik
    narzędzia w tym protokole (`registry.render_content`). Pierwsza wersja tej funkcji szukała
    `karty` na wierzchu koperty, nie znajdowała ich nigdy i karty nie docierały do rozmowy,
    chociaż każdy test samego narzędzia był zielony. Złapało to dopiero przejście CAŁEJ drogi
    przez silnik.
    """
    if not isinstance(result, dict):
        return []
    inner: Any = result
    if "karty" not in result:
        inner = result.get("structuredContent")
        if not isinstance(inner, dict):
            inner = first_json_block(result.get("content"))
    if not isinstance(inner, dict):
        return []
    cards = inner.get("karty")
    return [one for one in cards if isinstance(one, dict)] if isinstance(cards, list) else []


def first_json_block(content: Any) -> Any:
    """Pierwszy blok tekstowy koperty MCP, odczytany jako JSON. Nie-JSON to po prostu brak kart."""
    import json

    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        try:
            return json.loads(str(block.get("text") or ""))
        except json.JSONDecodeError:
            return None
    return None


def short_result(result: Any) -> Any:
    """Tool results are kept in the trace, but bounded: one thread read is kilobytes and the
    trace is read whole by the screen."""
    import json

    if result is None:
        return None
    # Keep image artifact references, never megabytes of base64 in the event log.
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        result = {
            **result,
            "content": [
                (
                    {"type": "image", "mimeType": block.get("mimeType"), "stored_as_artifact": True}
                    if isinstance(block, dict) and block.get("type") == "image"
                    else block
                )
                for block in result["content"]
            ],
        }
    text = json.dumps(result, ensure_ascii=False)
    if len(text) <= 4000:
        return result
    return {
        "skrocone": text[:4000],
        "pelna_dlugosc": len(text),
        "result_refs": [{"id": ident} for ident in returned_ids(result)],
    }


def tool_call_payload(item: dict[str, Any]) -> dict[str, Any]:
    """The `TOOL_CALL` payload for one tool call, from an engine-shaped item.

    `item` is the shape of a thread item of type `mcpToolCall`: `tool`, `server`, `arguments`,
    `status`, `error`, `durationMs` and `result`, the last being the MCP ENVELOPE
    (`{"content": [...], "isError": ...}`), not the bare dict the tool returned. The plugin tool
    server builds the same item from what it dispatched, so the two writers differ in where the
    item comes from and in nothing else. The call half needs only `tool`, `server` and
    `arguments`, which is why the plugin server can write it BEFORE the call runs.
    """
    tool = str(item.get("tool") or "?")
    arguments = item.get("arguments") or {}
    return {
        "tool": tool,
        "server": item.get("server"),
        "arguments": arguments,
        # JEDNO ZDANIE DLA CZŁOWIEKA, obok danych dla maszyny. Bez niego lista „co agent robił"
        # rozwija się do identyfikatorów narzędzi, a to nie jest odpowiedź na pytanie, co robił.
        "summary": call_summary(tool, arguments),
        # The digest is what makes an effect repeatable-safe across a restart; see
        # `tools/registry.py::_already_done`.
        "args_sha256": args_digest(tool, arguments),
    }


def tool_result_payload(item: dict[str, Any]) -> dict[str, Any]:
    """The `TOOL_RESULT` payload for one tool call; the counterpart of `tool_call_payload`.

    The fields `registry._already_done` reads are all here and must stay: `tool`, `status`
    (`failed` means the effect did not happen), `outcome` and `result`. `karty` rides NEXT to the
    result and not inside it, because `short_result` truncates long results and a card truncated
    to a string stops being a card.
    """
    tool = str(item.get("tool") or "?")
    return {
        "tool": tool,
        "status": item.get("status"),
        "error": item.get("error"),
        "duration_ms": item.get("durationMs"),
        "summary": result_summary(tool, item),
        "outcome": tool_outcome(item.get("result")),
        "result": short_result(item.get("result")),
        "karty": cards_of(item.get("result")),
    }


def tool_call_payloads(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Both payloads of a finished call, for the writer that sees the call only after the fact."""
    return tool_call_payload(item), tool_result_payload(item)
