"""MCP Apps: the one card the plugin draws inside the person's own agent (Codex, Claude Code).

A tool result is JSON the agent reads; a person in Codex sees whatever the agent writes about it,
which on 10.10 was a paragraph. MCP Apps lets a tool name a UI resource
(`_meta.ui.resourceUri`); a host that supports it fetches the HTML with `resources/read` and
renders it in a sandboxed iframe next to the answer, feeding it the tool's `structuredContent`.

ONLY FOR A PLUGIN HOST. A Wenext-run session has no person watching the thread, so the server
advertises no resources there (the module docstring of `mcp_server` explains why the surface is
kept small).

THE CARD IS THE POKÓJ, IN THE CHAT. Its tool is `room.show`, which returns what the page
`GET .../room` returns (`opportunities/room.py`): each person's post, the reply waiting for the
founder drawn as it will look on the platform, and after the publication its numbers. The card
adds nothing of its own to that data and no query: it draws it.

THE CARD IS SELF-CONTAINED: no script, font or image from anywhere else, so the content security
policy it declares is empty and the host has nothing to allow. It talks to the host only through
the MCP Apps bridge (`postMessage` JSON-RPC), with `window.openai` as the fallback ChatGPT and
older Codex builds expose. Everything it does is something the person could also ask in words:
open the thread, ask the agent for a reply or a revision, or open the Pokój page at the draft.

IT CAN SEND, AND ONLY A CLICK CAN (v4, 11.10, the founder's decision). "Wyślij" next to "Popraw"
runs two steps in the person's own agent: the first press asks the server for a one-time
confirmation (`room.send_token`) and shows what the click will do ("Wyślij z u/X?", or, with no
signed scope yet, the consent itself, which names every account of the project it covers: "Wenext
może pisać z @a i u/b. Każdą odpowiedź pokażę Ci przed wysłaniem"); only "Tak, wyślij" sends
(`room.send_draft`), and then the card follows the
request (`room.send_status`) until the publisher says PUBLISHED, with the link, or says why not.
Those three are app-only tools (`tools/card_send.py` has the threat model): the model neither
sees nor can call them, the confirmation travels in `_meta` and never as text, and the server
refuses a send without it. "Zatwierdź w Pokoju" stays, as a link, for a host that cannot proxy a
tool call or hands the card no confirmation: then there is no "Wyślij" at all.

IT SPEAKS POLISH AND ENGLISH (v5, 11.10, for the public launch). The words are two dictionaries,
`CARD_STRINGS`, put into the card's script; nothing the person reads is written in the markup.
WHICH ONE is decided in the card (`pickLang`), in this order: (1) `jezyk` in the result of
`room.show`, which this server sets from the environment variable WENEXT_LANG when it is `pl` or
`en` (`card_language`; the launcher `plugins/wenext/bin/wenext` reads it from its environment or
from `lang` in ~/.wenext/config.json and hands it on, and `bin/wenext setup --lang pl` writes it);
(2) the host's locale, `hostContext.locale` of `ui/initialize` or `window.openai.locale`, and only
when the host says nothing the browser's language; (3) Polish for `pl`, `pl-PL`, `pl_PL`, English
for everything else and for no signal at all. So a launch user on any English host needs to do
nothing, and a Polish install that must stay Polish whatever the host reports sets WENEXT_LANG=pl.
The server's refusals keep their Polish sentence (`powod`) and carry a stable `kod`: the card shows
its own English sentence for a code it knows (`ref.<kod>` in the dictionary) and the server's
Polish one for a code it does not. The server does not translate and the model's own text (the
reply, `dlaczego`, `kat`) is never touched. Dates and numbers use Intl with the chosen locale;
the deadline is still told in Warsaw time, and English says so ("GMT+2").

REFUSALS THAT HEAL THEMSELVES (v6, 11.10, from the founder's first real use). The server refuses a
send for reasons that pass: a previous reply still in the publisher's queue (`ZAKRES_KOLEJKA`), a
scope that moved because another click signed one (`ZAKRES_ZMIENIONY`), a confirmation that expired
or was spent (`POTWIERDZENIE`). v5 showed each of them as a sentence ending "click Send again", and
the sentence outlived its cause. Now the card asks again by itself: for the queue it asks
`room.send_token` every 4 seconds for about a minute under "Waiting for the previous reply to go
out..."; for the other two it asks once and shows the new sentence. WHAT IT NEVER DOES is send:
every way out of a refusal ends at the confirmation, which waits for the person's click on "Tak,
wyślij". The machine is `makeSender` (between the SEND markers), pure and driven in node by
`tests/test_card_send_card_unit.py`; `room.send_draft` is named in one place in it. Any other refusal
keeps a "Spróbuj ponownie" button. The consent sentence names the accounts the server put in the plan
(`konta`), and the card never sends them back.

ONE PERSON AT A TIME, THE WHOLE CARD ON ONE SCREEN (v7, 11.10, the founder's verdict on the first
recording: "the card is too tall and runs away, you can't see all of it"). v6 laid every person out
as a column of their own: headline, three columns, the results and the footer were far taller than
the chat, so Codex scrolled past the headline at once and cut the right-hand column off. v7 draws
the same data in about 560 px at the width of a Codex chat column (720 to 820; it holds to 360):
a quiet bar (mark, name, time), the headline, ONE summary line ("Newest 3 h ago", "3 replies waiting
for your OK"), ONE focused panel for the person on screen, a pager under it (dots, "1 of 4", two
chevrons, the arrow keys) and one footer line. THE RESULTS OF THE LAST DAYS ARE NOT ON THE CARD (the
founder's decision, 11.10: v7 first carried them as a line of the summary): the numbers of a reply that
went out are on its own page, and every other count is in the Pokój, one click on "Open Room". The
aggregate block `wyniki` is read for `pozycje` only, and a number nobody measured is still "not
measured" and never 0 (`figure`). In the panel: where and when on one line, the
post as a title and two lines, the reply as a compact native-looking block clamped to four lines
with "Show all" on the end of the last line, who it goes out from, and the buttons (Send, Edit,
Original as a quiet link). "Why this person" and "Suggested angle" sit behind one "Why?".
THE PAGES are all in the DOM, stacked in one grid cell, and exactly one is active (`data-active`):
the others are `visibility: hidden`, `inert` and `aria-hidden`, and the click handler ignores a
button of a page that is not active, so a Send can only ever reach the draft on screen. The card is
as tall as its tallest page, so it does not jump when the person pages; the pages slide a few
pixels and fade (CSS transitions, off under `prefers-reduced-motion`); a page the person leaves is
folded again. THE STATES of a send (the confirmation, waiting, queued, posted, refused) are drawn
in the region of the same page, where the reply and the buttons were: the region keeps the height
it had, so the card moves by a few pixels at most (the machine itself, `makeSender`, is untouched).
THE HEIGHT is told to the host as the card's own (`ui/notifications/size-changed`,
`window.openai.notifyIntrinsicHeight`) every time something changes it (a ResizeObserver, once per
frame), measured on the card and not on the document, which is never shorter than the frame the host
gave it. The page does not scroll (`overflow: hidden`): a host that does not size the frame cuts it.

MANUAL SENDING (v8, 11.10, the hosted product). A hosted Wenext has no publisher and no account of the
person's behind it, so `room.show` says `wysylka: "reczna"` and the card sends nothing. The primary
button changes with the platform: on Reddit "Copy and open thread" copies the reply
(`navigator.clipboard.writeText`; when the host's sandbox refuses it, a selected read-only textarea
and the words "press Cmd+C") and opens `watek_url`; on X "Post on X" opens `intent_url`, the composer
with the reply filled in. Then the card asks "Posted it? Paste the link (optional)" and calls
`draft.mark_posted` through the host (`tools/call`), or, when the host cannot carry a call or
refuses it, asks the agent to call it. The resource asks the host for `permissions.clipboardWrite`
(MCP Apps: a host MAY grant it, so the card never assumes it). `wysylka: "karta"`, which is every
local session and every older result, keeps the send flow above byte for byte: the machine
`makeSender` between the SEND markers is not touched. THE MANUAL STATES (copied, posted?, saving, done)
are drawn in the same region, where the buttons were, like the send states, and "Done" only ever
records what the person says; the card never claims it was verified.

The URI is a cache key: a host may keep the HTML it read, so a breaking change to the card gets a
new version in the URI rather than an edit under the old one. v8 adds manual sending; v7 is one person at a time; v6 heals
refusals and names every account; v5 added English; v4 added sending; v3 was the Pokój card without
it, and v2 drew the opportunities only and was fed by `opportunities.list`, which no longer carries
a card.
"""

# The card is a page (HTML, CSS and JS) kept in a string; its lines are the page's, not Python's.
# ruff: noqa: E501

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

OPPORTUNITIES_URI = "ui://wenext/okazje-v8.html"
MIME_TYPE = "text/html;profile=mcp-app"

#: The environment variable that pins the card's language: `pl`, `en`, empty or `auto` for "decide
#: by the host's locale". Set by the launcher (`bin/wenext`), read here.
ENV_LANG = "WENEXT_LANG"

#: The tools that render a card, and which card.
#: `room.show` and no other: a card fed by a filtered list, or by a tool called at the start of the
#: work, would show a half-empty room to a person who is watching the thread.
TOOL_UI: dict[str, str] = {"room.show": OPPORTUNITIES_URI}


def language_of(raw: str | None) -> str | None:
    """`pl` or `en` for an explicit setting, `None` for none (empty, or `auto`).

    `pl`, `pl-PL` and `pl_PL` are Polish; any other value is English, the card's fallback for every
    language it has no dictionary for. `None` means "not set": the card then asks the host.
    """
    text = (raw or "").strip().lower().replace("_", "-")
    if text in ("", "auto"):
        return None
    return "pl" if text == "pl" or text.startswith("pl-") else "en"


def card_language(environ: Mapping[str, str] | None = None) -> str | None:
    """The language the person pinned for the card (WENEXT_LANG), or `None` when they did not."""
    source = os.environ if environ is None else environ
    return language_of(source.get(ENV_LANG))


def tool_meta(name: str) -> dict[str, Any] | None:
    """`_meta` for a tool entry in `tools/list`, or `None` for a tool without a card."""
    uri = TOOL_UI.get(name)
    if uri is None:
        return None
    # The standard key, and the alias ChatGPT read before MCP Apps was a standard.
    return {"ui": {"resourceUri": uri}, "openai/outputTemplate": uri}


def app_only_meta() -> dict[str, Any]:
    """`_meta` of a tool only the card may call (`card_send`): MCP Apps `visibility: ["app"]`.

    The spec obliges the host to keep such a tool out of the agent's tool list and to reject a
    view's call to a tool that does not carry "app". Never combined with a `resourceUri`: these
    tools draw nothing.
    """
    return {"ui": {"visibility": ["app"]}}


#: What a host may show about the card before it opens it. The server knows the language only when
#: it was pinned; unpinned, the metadata is English like the card's own fallback.
_RESOURCE_TEXT = {
    "pl": {
        "name": "Pokój Wenext",
        "description": (
            "Ludzie, którzy piszą o problemie Twojego kodu, gotowe odpowiedzi w Twoim głosie "
            "i wyniki opublikowanych."
        ),
        "widget": "Karty ludzi z Reddita i X, ich wątki, gotowe odpowiedzi i wyniki.",
    },
    "en": {
        "name": "Wenext Room",
        "description": (
            "People writing about the problem your code solves, ready replies in your voice "
            "and the results of what you posted."
        ),
        "widget": "Cards of people on Reddit and X, their threads, ready replies and results.",
    },
}


def resources(lang: str | None = None) -> list[dict[str, Any]]:
    text = _RESOURCE_TEXT[lang or card_language() or "en"]
    return [
        {
            "uri": OPPORTUNITIES_URI,
            "name": text["name"],
            "description": text["description"],
            "mimeType": MIME_TYPE,
        }
    ]


def read(uri: str, lang: str | None = None) -> dict[str, Any] | None:
    """The `resources/read` result, or `None` for a URI this server does not have."""
    if uri != OPPORTUNITIES_URI:
        return None
    text = _RESOURCE_TEXT[lang or card_language() or "en"]
    return {
        "contents": [
            {
                "uri": OPPORTUNITIES_URI,
                "mimeType": MIME_TYPE,
                "text": OPPORTUNITIES_HTML,
                "_meta": {
                    "ui": {
                        "csp": {"connectDomains": [], "resourceDomains": []},
                        # Only the manual flow copies, and a host MAY refuse: the card detects.
                        "permissions": {"clipboardWrite": {}},
                        "prefersBorder": False,
                    },
                    "openai/widgetDescription": text["widget"],
                },
            }
        ]
    }


#: Every word on the card, one dictionary per language. The two have exactly the same keys (a test
#: holds them to it). A value is a string, or, for a count, a list of forms: three for Polish (one,
#: two to four, the rest), two for English (one, the rest). `{name}` is filled by the card with a
#: value it escapes; a template may carry `<b>`, `<strong>` or `<i>`, a value never does. Key
#: prefixes: ago, expiry, fig, state, seen, handle (the placeholder account), pv (the preview), ap
#: (the approval row), c (the consent), sp (the send box), e (what the card says when the agent
#: cannot), w (the publisher's verdicts), ref (the server's refusal codes), why (the person and the
#: angle, and the "Why?" that opens them), nav (the pager), pub, oc, dc,
#: rd (the page), ask (what the card asks the agent in the person's name), man (manual sending, v8).
#: No long dash in any of it (the founder's rule), and no word is written in the markup.
CARD_STRINGS: dict[str, dict[str, Any]] = {
    "pl": {
        "ago.now": "przed chwilą",
        "ago.min": "{m} min temu",
        "ago.hour": "{h} h temu",
        "ago.yesterday": "wczoraj",
        "ago.days": "{d} dni temu",
        "expiry.unreadable": "Termin nieczytelny",
        "expiry.closed": "Okno zgody minęło",
        "expiry.min": "Wygasa za {m} min",
        "expiry.hour": "Wygasa za {h} h",
        "expiry.date": "Wygasa {day}",
        "fig.none": "brak pomiaru",
        "state.NOWA": "Nowa",
        "state.SZKIC": "Odpowiedź gotowa",
        "state.ODPOWIEDZIANO": "Opublikowano",
        "state.POMINIETA": "Pominięta",
        "state.waiting": "Czeka na zgodę",
        "seen.CONFIRMED": "widoczny",
        "seen.REMOVED": "usunięty z platformy",
        "seen.unconfirmed": "niepotwierdzona",
        "seen.pending": "jeszcze niepotwierdzona",
        "handle.x": "@twoje_konto",
        "handle.reddit": "u/twoje_konto",
        "pv.thread": "Wątek",
        "pv.threadYours": "Wątek, do którego odpowiadasz",
        "pv.now": "teraz",
        "pv.replyingTo": "W odpowiedzi do",
        "pv.reply": "Odpowiedz",
        "pv.share": "Udostępnij",
        "pv.labelPost": "Twój post, tak jak wyjdzie",
        "pv.labelReply": "Twoja odpowiedź, tak jak wyjdzie",
        "pv.more": "Pokaż całość",
        "pv.less": "Zwiń",
        "ap.goes": "Wyjdzie z konta {h}, po Twoim kliknięciu.",
        "ap.goesProject": "Wyjdzie z konta przypisanego do projektu, po Twoim kliknięciu.",
        "ap.inRoom": " Zatwierdzasz w Pokoju, w dashboardzie Wenext.",
        "ap.approve": "Zatwierdź w Pokoju ↗",
        "ap.original": "Oryginał ↗",
        "ap.edit": "Popraw",
        "ap.send": "Wyślij",
        "c.thisPost": "ten post",
        "c.thisReply": "tę odpowiedź",
        "c.sign": "Wenext może pisać z {accounts}. Każdą odpowiedź pokażę Ci przed wysłaniem.",
        "c.signPost": "Wenext może pisać z {accounts}. Każdy post i każdą odpowiedź pokażę Ci przed wysłaniem.",
        "c.widen": [
            "Dodaję konto {accounts} do Twojego zakresu i wysyłam {what}. Każdą następną też pokażę Ci przed wysłaniem.",
            "Dodaję konta {accounts} do Twojego zakresu i wysyłam {what}. Każdą następną też pokażę Ci przed wysłaniem.",
            "Dodaję konta {accounts} do Twojego zakresu i wysyłam {what}. Każdą następną też pokażę Ci przed wysłaniem.",
        ],
        "c.and": " i ",
        "c.small": "To kliknięcie podpisuje zakres: konta tego projektu, każdy tekst czeka na Twoje kliknięcie{until}{limits}. Zmienisz go albo cofniesz w Obecności.",
        "c.smallWiden": "To kliknięcie podpisuje rozszerzony zakres: każdy tekst nadal czeka na Twoje kliknięcie{until}{limits}. Zmienisz go albo cofniesz w Obecności.",
        "c.changed": "Zakres zmienił się w międzyczasie, więc to zdanie jest nowe.",
        "c.until": ", do {until}",
        "c.limits": ", najwyżej {h} na godzinę i {d} na dobę w całym projekcie",
        "c.plainPost": "Wyślij z <b>{h}</b>? Post wyjdzie dokładnie tak, jak go widzisz.",
        "c.plainReply": "Wyślij z <b>{h}</b>? Odpowiedź wyjdzie dokładnie tak, jak ją widzisz.",
        "sp.checking": "Sprawdzam szkic",
        "sp.yes": "Tak, wyślij",
        "sp.cancel": "Anuluj",
        "sp.sending": "Wysyłam do publikatora",
        "sp.waiting": "Czekam, aż poprzednia odpowiedź wyjdzie...",
        "sp.waitingNote": "Gdy wyjdzie, pokażę Ci potwierdzenie. Nic nie wychodzi bez Twojego kliknięcia.",
        "sp.retry": "Spróbuj ponownie",
        "sp.queued": "W kolejce publikatora.",
        "sp.queuedSoon": "Wyjdzie za chwilę, a ja sprawdzę, czy platforma ją przyjęła.",
        "sp.queuedSlow": "Trwa dłużej niż zwykle. Wynik zobaczysz w Pokoju.",
        "sp.recheck": "Sprawdź jeszcze raz",
        "sp.posted": "Opublikowano.",
        "sp.visibility": "Widoczność: {seen}.",
        "sp.view": "Zobacz odpowiedź ↗",
        "sp.done": "Gotowe",
        "sp.failed": "Nie wyszło.",
        "sp.failedDefault": "Publikator nie przyjął wniosku.",
        "sp.close": "Zamknij",
        "sp.presence": "Otwórz Obecność ↗",
        "sp.refusedDefault": "Nie udało się wysłać.",
        "e.noAnswer": "Agent nie przekazał karcie odpowiedzi. Zatwierdź w Pokoju.",
        "e.noConnect": "Nie udało się połączyć z Wenext przez agenta. Zatwierdź w Pokoju.",
        "e.noToken": "Ten agent nie przekazał karcie potwierdzenia, więc wysyłka z karty nie jest możliwa. Zatwierdź w Pokoju.",
        "e.unsure": "Nie wiem, czy to poszło: agent nie odpowiedział. Sprawdź w Pokoju, zanim klikniesz jeszcze raz.",
        "e.unknown": "Nie wiadomo, czy odpowiedź wyszła. Sprawdź na platformie, zanim wyślesz jeszcze raz.",
        "w.BLOCKED": "Publikator zablokował wniosek.",
        "w.SKIPPED": "Publikator pominął wniosek.",
        "w.STOPPED": "Publikator zatrzymał wniosek.",
        "ref.STAN": "Ta propozycja nie czeka na zgodę, więc nie da się jej zatwierdzić. Odśwież Pokój.",
        "ref.OKNO": "Okno zgody tej propozycji minęło. Poproś agenta o nową wersję.",
        "ref.ODCISK": "Ta wersja szkicu nie jest już tą, którą widziałeś: ktoś ją w międzyczasie zmienił. Odśwież Pokój i zdecyduj o najnowszej.",
        "ref.ZAKRES": "Projekt nie ma żywego powierzenia, więc nie ma zakresu, w którym ta publikacja miałaby się odbyć. Podpisz powierzenie i zatwierdź ponownie.",
        "ref.BUDZET": "Limit wysyłki jest na razie wyczerpany. Sprawdź limity w Obecności.",
        "ref.ZGODA": "Zgoda nie przeszła. Spróbuj jeszcze raz albo zatwierdź w Pokoju.",
        "ref.NIEAKTUALNY": "Ta wersja szkicu nie czeka już na zgodę: ktoś ją w międzyczasie zatwierdził albo poprawił. Odśwież Pokój i zdecyduj o najnowszej.",
        "ref.ZAKRES_WSTRZYMANY": "Zakres tego projektu jest wstrzymany. Wznów go w Obecności: wstrzymanie to Twój stop i kliknięcie w karcie go nie zdejmuje.",
        "ref.ZAKRES_INNY_TRYB": "Zakres tego projektu działa w innym trybie i nie obejmuje tego konta. Dodaj je w Obecności: z karty dodaję konta tylko do zakresu, w którym każdy tekst czeka na Twoje kliknięcie.",
        "ref.ZAKRES_KOLEJKA": "Poprzednia odpowiedź jeszcze czeka w kolejce publikatora, a nowy podpis zatrzymałby ją. Poczekaj, aż wyjdzie, i spróbuj ponownie.",
        "ref.NIE_ZNALEZIONO": "Nie ma takiej propozycji ani takiego wniosku w tym projekcie.",
        "ref.POTWIERDZENIE": "Brak ważnego potwierdzenia kliknięciem: nieznane, zużyte albo wygasłe. Spróbuj ponownie.",
        "ref.ZAKRES_ZMIENIONY": "Zakres zmienił się od chwili, gdy pokazano potwierdzenie. Spróbuj ponownie.",
        "ref.PODPIS": "Nie udało się podpisać zakresu. Podpisz go w Obecności.",
        "ref.DZIALANIE": "Tego działania nie wyślę z karty. Zatwierdź w Pokoju.",
        "why.person": "Dlaczego ta osoba",
        "why.angle": "Proponowany kąt",
        "why.ask": "Dlaczego?",
        "nav.label": "Osoby",
        "nav.prev": "Poprzednia osoba",
        "nav.next": "Następna osoba",
        "nav.of": "{i} z {n}",
        "nav.go": "Pokaż osobę {i} z {n}",
        "pub.votes": "Głosy",
        "pub.replies": "Odpowiedzi",
        "pub.visibility": "Widoczność",
        "pub.when": "Opublikowano {when}.",
        "pub.older": "Wynik starszy niż 7 dni jest w Pokoju.",
        "oc.draftGone": "Szkic nie czeka już na zgodę. Zobacz w Pokoju.",
        "oc.openRoom": "Otwórz Pokój ↗",
        "oc.score": "wynik",
        "oc.comments": "komentarze",
        "oc.write": "Napisz odpowiedź",
        "dc.postOn": "Własny post na {p}",
        "dc.replyOn": "Odpowiedź na {p}",
        "rd.errH": "Nie udało się <strong>odczytać Pokoju</strong>.",
        "rd.errDefault": "Spróbuj jeszcze raz za chwilę.",
        "rd.noneH": "Na razie <strong>nikogo</strong>.",
        "rd.noneBody": "Poproś agenta: znajdź moich pierwszych użytkowników.",
        "rd.allDone": "Wszystkie rozmowy <strong>obsłużone</strong>.",
        "rd.headNow": [
            "<strong>{n} osoba</strong> właśnie pisze o problemie, który rozwiązuje Twój kod.",
            "<strong>{n} osoby</strong> właśnie piszą o problemie, który rozwiązuje Twój kod.",
            "<strong>{n} osób</strong> właśnie pisze o problemie, który rozwiązuje Twój kod.",
        ],
        "rd.headRecent": [
            "<strong>{n} osoba</strong> ostatnio pisze o problemie, który rozwiązuje Twój kod.",
            "<strong>{n} osoby</strong> ostatnio piszą o problemie, który rozwiązuje Twój kod.",
            "<strong>{n} osób</strong> ostatnio pisze o problemie, który rozwiązuje Twój kod.",
        ],
        "rd.newest": "najnowsza {ago}",
        "rd.waiting": [
            "{n} odpowiedź czeka na Twoją zgodę",
            "{n} odpowiedzi czekają na Twoją zgodę",
            "{n} odpowiedzi czeka na Twoją zgodę",
        ],
        "rd.foot": "Nic nie wychodzi bez Twojego kliknięcia.",
        "rd.asOf": "Odczyt o {time}",
        "rd.noDataH": "Nie mam <strong>danych Pokoju</strong>.",
        "rd.noDataBody": "Poproś agenta, żeby pokazał Pokój jeszcze raz.",
        "ask.who": "tej osoby",
        "ask.inThread": " w wątku {url}",
        "ask.inCommunity": " w {c}",
        "ask.fixOwn": "Popraw szkic mojego posta{where}. {tail}",
        "ask.fixReply": "Popraw odpowiedź dla {who}{thread}. {tail}",
        "ask.fixTail": "Szkic już czeka na moją zgodę, więc napisz nową wersję innym tekstem: przeczytaj szkic i wątek, i zapisz ją w Wenext jako szkic. Jeśli nie wiesz, co mi w nim nie pasuje, zapytaj mnie jednym zdaniem. Na końcu pokaż mi Pokój jeszcze raz.",
        "ask.reply": "Napisz odpowiedź dla {who} w wątku {url}. Przeczytaj wątek, odpowiedz pomocnie, w moim głosie, i zapisz szkic w Wenext. Na końcu pokaż mi Pokój.",
        "man.note": "Publikujesz sam, ze swojego konta.",
        "man.copyOpen": "Skopiuj i otwórz wątek",
        "man.postX": "Opublikuj na X",
        "man.xLong": "Ta odpowiedź jest za długa na X. Skróć ją przed publikacją.",
        "man.copied": "Odpowiedź skopiowana. Wątek jest otwarty: wklej ją tam i opublikuj.",
        "man.copiedNoLink": "Odpowiedź skopiowana. Otwórz wątek, wklej ją i opublikuj.",
        "man.copyFail": "Nie udało się skopiować samo. Zaznacz tekst poniżej, naciśnij Cmd+C (Ctrl+C w Windows) i wklej go w wątku.",
        "man.xOpened": "X jest otwarty z Twoją odpowiedzią. Opublikuj ją tam.",
        "man.posted": "Opublikowane? Wklej link (opcjonalnie)",
        "man.badLink": "Podaj link do swojego posta na Reddicie albo X (https://...), albo zostaw pole puste.",
        "man.done": "Gotowe",
        "man.again": "Otwórz jeszcze raz",
        "man.notYet": "Jeszcze nie opublikowane",
        "man.saving": "Zapisuję",
        "man.saved": "Oznaczone jako opublikowane.",
        "man.savedNote": "Wenext nie sprawdził tego na platformie.",
        "man.viewPost": "Zobacz Twój post ↗",
        "man.failed": "Nie udało się zapisać.",
        "man.failedHint": "Spróbuj jeszcze raz albo poproś o to agenta.",
        "man.retry": "Spróbuj ponownie",
        "man.askAgent": "Poproś agenta",
        "man.asked": "Poprosiłem agenta o zapisanie. Odpowie w rozmowie.",
        "man.askMsg": "Opublikowałem odpowiedź ze szkicu {rev}{link}. Zapisz to przez draft.mark_posted.",
        "man.askLink": " (mój post: {url})",
        "rd.waitingManual": [
            "{n} odpowiedź gotowa do publikacji",
            "{n} odpowiedzi gotowe do publikacji",
            "{n} odpowiedzi gotowych do publikacji",
        ],
        "rd.footManual": "Wenext niczego nie publikuje. Publikujesz sam, ze swojego konta.",
        "state.ready": "Gotowa do publikacji",
        "oc.draftGoneManual": "Ten szkic nie jest już dostępny. Poproś agenta o nową wersję.",
        "ask.fixTailManual": "Szkic jest gotowy, więc napisz nową wersję innym tekstem: przeczytaj szkic i wątek, i zapisz ją w Wenext jako szkic. Jeśli nie wiesz, co mi w nim nie pasuje, zapytaj mnie jednym zdaniem. Na końcu pokaż mi kartę jeszcze raz.",
    },
    "en": {
        "ago.now": "just now",
        "ago.min": "{m} min ago",
        "ago.hour": "{h} h ago",
        "ago.yesterday": "yesterday",
        "ago.days": "{d} days ago",
        "expiry.unreadable": "Expiry unreadable",
        "expiry.closed": "Approval window closed",
        "expiry.min": "Expires in {m} min",
        "expiry.hour": "Expires in {h} h",
        "expiry.date": "Expires {day}",
        "fig.none": "not measured",
        "state.NOWA": "New",
        "state.SZKIC": "Reply ready",
        "state.ODPOWIEDZIANO": "Posted",
        "state.POMINIETA": "Skipped",
        "state.waiting": "Waiting for your OK",
        "seen.CONFIRMED": "visible",
        "seen.REMOVED": "removed by the platform",
        "seen.unconfirmed": "not confirmed",
        "seen.pending": "not confirmed yet",
        "handle.x": "@your_account",
        "handle.reddit": "u/your_account",
        "pv.thread": "Thread",
        "pv.threadYours": "The thread you are replying to",
        "pv.now": "now",
        "pv.replyingTo": "Replying to",
        "pv.reply": "Reply",
        "pv.share": "Share",
        "pv.labelPost": "Your post, as it will appear",
        "pv.labelReply": "Your reply, as it will appear",
        "pv.more": "Show all",
        "pv.less": "Show less",
        "ap.goes": "Goes out from {h} after your click.",
        "ap.goesProject": "Goes out from the account assigned to the project, after your click.",
        "ap.inRoom": " You approve in the Room, in the Wenext dashboard.",
        "ap.approve": "Approve in Room ↗",
        "ap.original": "Original ↗",
        "ap.edit": "Edit",
        "ap.send": "Send",
        "c.thisPost": "this post",
        "c.thisReply": "this reply",
        "c.sign": "Wenext can write from {accounts}. I'll show you every reply before it goes out.",
        "c.signPost": "Wenext can write from {accounts}. I'll show you every post and reply before it goes out.",
        "c.widen": [
            "I'm adding the account {accounts} to your scope and sending {what}. I'll show you every next one before it goes out.",
            "I'm adding the accounts {accounts} to your scope and sending {what}. I'll show you every next one before it goes out.",
        ],
        "c.and": " and ",
        "c.small": "This click signs a scope: the accounts of this project, and every text waits for your click{until}{limits}. You can change or revoke it in Presence.",
        "c.smallWiden": "This click signs the widened scope: every text still waits for your click{until}{limits}. You can change or revoke it in Presence.",
        "c.changed": "The scope changed in the meantime, so this sentence is new.",
        "c.until": ", until {until}",
        "c.limits": ", at most {h} per hour and {d} per day across the project",
        "c.plainPost": "Send from <b>{h}</b>? The post will go out exactly as you see it.",
        "c.plainReply": "Send from <b>{h}</b>? The reply will go out exactly as you see it.",
        "sp.checking": "Checking the draft",
        "sp.yes": "Yes, send",
        "sp.cancel": "Cancel",
        "sp.sending": "Sending to the publisher",
        "sp.waiting": "Waiting for the previous reply to go out...",
        "sp.waitingNote": "As soon as it has, I'll show you the confirmation. Nothing goes out without your click.",
        "sp.retry": "Try again",
        "sp.queued": "Queued.",
        "sp.queuedSoon": "It goes out in a moment, and I'll check that the platform accepted it.",
        "sp.queuedSlow": "This is taking longer than usual. You'll see the result in the Room.",
        "sp.recheck": "Check again",
        "sp.posted": "Posted.",
        "sp.visibility": "Visibility: {seen}.",
        "sp.view": "View reply ↗",
        "sp.done": "Done",
        "sp.failed": "It didn't go out.",
        "sp.failedDefault": "The publisher did not accept the request.",
        "sp.close": "Close",
        "sp.presence": "Open Presence ↗",
        "sp.refusedDefault": "Could not send.",
        "e.noAnswer": "The agent did not give the card an answer. Approve in the Room.",
        "e.noConnect": "Could not reach Wenext through the agent. Approve in the Room.",
        "e.noToken": "This agent did not pass the card a confirmation, so sending from the card is not possible. Approve in the Room.",
        "e.unsure": "I can't tell whether this went out: the agent did not answer. Check the Room before you click again.",
        "e.unknown": "It is not known whether the reply went out. Check the platform before you send again.",
        "w.BLOCKED": "The publisher blocked the request.",
        "w.SKIPPED": "The publisher skipped the request.",
        "w.STOPPED": "The publisher stopped the request.",
        "ref.STAN": "This draft is not waiting for approval, so it can't be approved. Refresh the Room.",
        "ref.OKNO": "The approval window for this draft has closed. Ask the agent for a new version.",
        "ref.ODCISK": "This draft was changed after you saw it. Refresh the Room and decide on the latest version.",
        "ref.ZAKRES": "This project has no signed scope to post in. Sign a scope in Presence, then approve again.",
        "ref.BUDZET": "The posting limit is used up for now. Check the limits in Presence.",
        "ref.ZGODA": "The approval did not go through. Try again, or approve in the Room.",
        "ref.NIEAKTUALNY": "This draft is no longer waiting for approval: it was approved or revised in the meantime. Refresh the Room and decide on the latest version.",
        "ref.ZAKRES_WSTRZYMANY": "This project's scope is paused. Resume it in Presence: a pause is your stop, and a click on the card does not lift it.",
        "ref.ZAKRES_INNY_TRYB": "This project's scope runs in a different mode and doesn't cover this account. Add the account in Presence: from the card I only add accounts to a scope where every text waits for your click.",
        "ref.ZAKRES_KOLEJKA": "The previous reply is still in the publisher's queue, and a new signature would stop it. Wait until it goes out, then try again.",
        "ref.NIE_ZNALEZIONO": "That draft or request isn't in this project.",
        "ref.POTWIERDZENIE": "No valid confirmation from your click: it is unknown, expired or already used. Try again.",
        "ref.ZAKRES_ZMIENIONY": "The scope changed after the confirmation was shown. Try again.",
        "ref.PODPIS": "The scope could not be signed. Sign it in Presence.",
        "ref.DZIALANIE": "I can't send this kind of action from the card. Approve it in the Room.",
        "why.person": "Why this person",
        "why.angle": "Suggested angle",
        "why.ask": "Why?",
        "nav.label": "People",
        "nav.prev": "Previous person",
        "nav.next": "Next person",
        "nav.of": "{i} of {n}",
        "nav.go": "Show person {i} of {n}",
        "pub.votes": "Votes",
        "pub.replies": "Replies",
        "pub.visibility": "Visibility",
        "pub.when": "Posted {when}.",
        "pub.older": "Results older than 7 days are in the Room.",
        "oc.draftGone": "This draft is no longer waiting for approval. See it in the Room.",
        "oc.openRoom": "Open Room ↗",
        "oc.score": "score",
        "oc.comments": "comments",
        "oc.write": "Write a reply",
        "dc.postOn": "Your post on {p}",
        "dc.replyOn": "Reply on {p}",
        "rd.errH": "Couldn't <strong>read the Room</strong>.",
        "rd.errDefault": "Try again in a moment.",
        "rd.noneH": "<strong>Nobody</strong> yet.",
        "rd.noneBody": "Ask your agent: find my first users.",
        "rd.allDone": "Every conversation is <strong>handled</strong>.",
        "rd.headNow": [
            "<strong>{n} person</strong> is writing right now about the problem your code solves.",
            "<strong>{n} people</strong> are writing right now about the problem your code solves.",
        ],
        "rd.headRecent": [
            "<strong>{n} person</strong> wrote recently about the problem your code solves.",
            "<strong>{n} people</strong> wrote recently about the problem your code solves.",
        ],
        "rd.newest": "newest {ago}",
        "rd.waiting": [
            "{n} reply waiting for your OK",
            "{n} replies waiting for your OK",
        ],
        "rd.foot": "Nothing posts without your click.",
        "rd.asOf": "As of {time}",
        "rd.noDataH": "I have no <strong>Room data</strong>.",
        "rd.noDataBody": "Ask your agent to show the Room again.",
        "ask.who": "this person",
        "ask.inThread": " in the thread {url}",
        "ask.inCommunity": " in {c}",
        "ask.fixOwn": "Revise the draft of my post{where}. {tail}",
        "ask.fixReply": "Revise the reply to {who}{thread}. {tail}",
        "ask.fixTail": "The draft is already waiting for my OK, so write a new version with different wording: read the draft and the thread, and save it in Wenext as a draft. If you don't know what I don't like about it, ask me in one sentence. At the end, show me the Room again.",
        "ask.reply": "Write a reply to {who} in the thread {url}. Read the thread, answer helpfully in my voice, and save the draft in Wenext. At the end, show me the Room.",
        "man.note": "You post it yourself, from your account.",
        "man.copyOpen": "Copy and open thread",
        "man.postX": "Post on X",
        "man.xLong": "This reply is over X's length limit. Shorten it before you post.",
        "man.copied": "Reply copied. The thread is open: paste it there and post.",
        "man.copiedNoLink": "Reply copied. Open the thread, paste it and post.",
        "man.copyFail": "Couldn't copy automatically. Select the text below, press Cmd+C (Ctrl+C on Windows) and paste it in the thread.",
        "man.xOpened": "X is open with your reply filled in. Post it there.",
        "man.posted": "Posted it? Paste the link (optional)",
        "man.badLink": "Paste the link to your post on Reddit or X (https://...), or leave it empty.",
        "man.done": "Done",
        "man.again": "Open again",
        "man.notYet": "Not posted yet",
        "man.saving": "Saving",
        "man.saved": "Marked as posted.",
        "man.savedNote": "Wenext did not check it on the platform.",
        "man.viewPost": "View your post ↗",
        "man.failed": "Couldn't save it.",
        "man.failedHint": "Try again, or ask your agent to do it.",
        "man.retry": "Try again",
        "man.askAgent": "Ask your agent",
        "man.asked": "I asked your agent to record it. It will answer in the chat.",
        "man.askMsg": "I posted the reply of draft {rev}{link}. Record it with draft.mark_posted.",
        "man.askLink": " (my post: {url})",
        "rd.waitingManual": [
            "{n} reply ready to post",
            "{n} replies ready to post",
        ],
        "rd.footManual": "Wenext never posts. You post it yourself, from your account.",
        "state.ready": "Ready to post",
        "oc.draftGoneManual": "This draft is no longer available. Ask your agent for a new version.",
        "ask.fixTailManual": "The draft is ready, so write a new version with different wording: read the draft and the thread, and save it in Wenext as a draft. If you don't know what I don't like about it, ask me in one sentence. At the end, show me the card again.",
    },
}


def _strings_json() -> str:
    """The dictionaries as the script literal. `</` is cut so no string can close the script."""
    return json.dumps(CARD_STRINGS, ensure_ascii=False, indent=1).replace("</", "<\\/")


#: The Wenext mark (apps/dashboard/public/brand/mark-white.png at 56 px), inline because the card
#: loads nothing from anywhere.
_MARK_PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAADgAAAAuCAYAAAB9CdqYAAAAAXNSR0IArs4c6QAAAERlWElmTU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAOKADAAQAAAABAAAALgAAAADi9wpxAAAGC0lEQVRoBe2abWhWZRjHN81qbdNSm6VhYgUrXTV6Q0NbIVKRZOKX6kOElGSB9aEXKIUSKoogCupLJL1SgdMIF7ZsDSo1y3CatdFq5Wvp5ixbzTmffv/Dc57OOd73Ofc57MUnuuDnfe7ruu6X69yv55klJf9xKS32+HK53EnEMBrKoBcOlpaW9pMWvxDcOFgIq6AVGmFW8UdGBAQyFh6CTghKE5nKog6SAE6DB6AHonIURV3RBkjnR8DN0AU2WV7MAZ5HVFttkeX17/oBagcaNqEzo2h8HGgXPAK72QH7SI2C/6kY7oeLjQ7/Kif6j8MWIJ09m07UwHVwIXTBa+ibCTLHs0lmoLzDZIjoKqln5LAcF2oYquEZ2AtB+ZpMVaSzXhZ9BXwQdI553oZNs2NoRY3CTFgHNllg6hXO86DXViiibyE/UvWMMFU2GDoa1HK4Gl6EuTFt1EZtlC1Hdx+cHLVZ8n+j96b5kKxBOqgr4ZXwPFwCcTLJYNTt5BqD3qbqZv0dk3GoRrCatp6FpODUp9BL5+Wcgm4xKHWV33zHQQ+QDk6gsSdgpt9oQtoTsWvKzonokrI7fYdBDTCwdhb6DTqke30fyms0F0GFr3NMf3D0s7vReBnowuvtVlFPdQ4eAdN9EbVVbvHrwuNaOGT1NBuOoL7CryM0331lXErhydgvBx3OY+F7dG+yqP/iOSh1ZO6BsqAy4VnTs1U+1KkdU6OnW04a6cT55zQFPF8a1O1gPrwCugvqzeptdUDo6kR+MqyHtLKDAjoSFOAMOJC2AvyboTBwhYe4iClwPvZbQYewggmu3XPJnwUtoI7pvrgEZiufUr7EX1Nao3c36J6aVrYwm476hRIDpDGdX/fCPDjDLxhINTX3BPJaQ3dCYt2BMnrUwfwJnaPJnHbcm6TMIBudy9BQHayFPrDJZgzeLkeqafWNzTFBvx/7FBgDqxN8beZuDBc4BYijgmuCJHlYFeI0DRqSnGPsa7Bp510Eh2P84kwbMCZfCHC6Cj6NqylvayedCtPhbeiHLKJyt0EtbM9SQb7MU9HRO26d4KghXgYudz9tCjo2tKnMh+DmQ9ZZfsfzMDwK05xLhR31wbw+rIrkCG40vAT64cZFNuHUBFlHzm9DR86HoGMnq7RSUOdySKJvXKNwOxhvJ6GSJSU7yetQbYePwbu9k2YRHebXww7YnqUCyjSyA3dFyxamKNGfg3EpqLE4UWCfQRPo7NMRoVuNpnTyAsfJIP3oGmAlTM9D4iy9eL5v8i4EiFHnzqUmp7xOo/U5rIVmaNOZRaodVOejvvmyiEZ+DTxJfVuo68wMlWjktR8cJ16AVKrOKcDolFUBbQCb4SNohBY6ojcelMvI6PaRRdZRaAV1bs0X1gimlXrKHzIV8gLESIw57WK+KICfQAFpxLbBHvwKVyDynuRfzgI/nzKtx38p9e5SOerSFJ+l5xTSge+rNv/gFH0aJy1w3SV/Aa2v3fArHdAWbJPxGGptxhi9rlTL/ODyfhNJp8aUMZneQ7nPZAjpeHv6OW88VIF+onNaU/jVgeuxgqsn7fw7G0JtkL8BjoGr6HoXe24WRpA3qWl5IBS1W0ZrxuVY8Wvbz4Oudxto09ukfANpDYSCDthMj6tQtpkMvs60qfg217Ta1RE/bViPQQPB9RnKXWTQ2VR6US9b6imUGYgAqwq1xT9oE1sO79CpnqgrU00jNyWqj8m/gU3HQ6wMRIAudXTTC43c6wSnUTSJdtAJJoNB144ucfRUrrAGDZW4qnSziRMdNytgNcEpUJto9660GQN67RXPwY8B3eA9MrVuBNMuqt1QF+i5UJHUA3y0g++DJKnHQTenoREa049RL0AnKFB9GXwBD4L+ijTKpSf4nQ67IE7aMKY6c9NsydZ+0ugkjGpYG04naI3oj5kHSZ2EOnTV+wpqLAX0pbAYNNU1TYdW6KD+NFae72imxin7FphEv7UsgfJMFZ8ohQhA/7kg+gNXB7q7YMyJ0s/M/SAIrefH4Vv4DlbCHEj6RrW2OSBr0Fp7BgPBaBr6o/Unz3+w5vTN+L+Y3sA//JeyGJD6m6MAAAAASUVORK5CYII="

OPPORTUNITIES_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wenext</title>
<style>
/* The studio palette (apps/dashboard/app/studio.css, `.st`), on purpose dark in a light host:
   the card is a window into Wenext, not a part of Codex. The reply preview leaves the palette on
   purpose, like in the Pokój (room.css): a draft is drawn as the platform will show it.
   v7 is ONE person at a time: the pages are stacked in one grid cell, so the card is as tall as
   its tallest page and does not jump when the person pages; only the active page is visible. */
:root {
  color-scheme: dark;
  --ground: #080808; --surface: #131313; --raised: #1b1b1b; --well: #0e0e0e;
  --line: rgba(255, 255, 255, 0.07); --ink: #f3f3f1; --ink-2: #a6a6a2; --ink-3: #8f8f89;
  --body: #d6d6d2; --accent: #d3e4af; --warn: #d8c08a;
  --ease: cubic-bezier(0.2, 0.75, 0.15, 1);
  --side: 28px;
}
* { box-sizing: border-box; }
html, body { margin: 0; background: transparent; overflow: hidden; }
body { color: var(--ink); -webkit-font-smoothing: antialiased;
  font: 14px/1.5 "Helvetica Neue", Helvetica, -apple-system, BlinkMacSystemFont, Arial, sans-serif; }
svg { width: 14px; height: 14px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.8;
  stroke-linecap: round; stroke-linejoin: round; }
button { font: inherit; font-size: 13px; cursor: pointer; border: 0; border-radius: 999px; padding: 0;
  color: inherit; background: transparent;
  transition: background-color 240ms var(--ease), color 240ms var(--ease), opacity 240ms var(--ease); }
button:focus-visible { outline: 2px solid rgba(211, 228, 175, 0.55); outline-offset: 2px; }
button[disabled] { cursor: default; }
.panel { background: var(--ground); border-radius: 20px; padding: 18px var(--side) 14px; }

/* The bar: the mark, the name, and when this was read. */
.top { display: flex; align-items: center; gap: 8px; min-height: 16px; font-size: 12px; color: var(--ink-3); }
.top img { width: 17px; height: 14px; opacity: 0.95; }
.top .brand { color: var(--ink); font-size: 12.5px; font-weight: 500; }
.top .asof { margin-left: auto; font-variant-numeric: tabular-nums; }
h1 { margin: 12px 0 0; font-weight: 400; font-size: clamp(21px, 2.1vw + 11px, 27px); line-height: 1.14;
  letter-spacing: -0.042em; color: var(--ink-2); max-width: 21em; text-wrap: balance; }
h1 strong { font-weight: 400; color: var(--ink); }
.sum { margin: 8px 0 0; font-size: 12px; line-height: 1.6; color: var(--ink-3); }
.sum::first-letter { text-transform: uppercase; }
.sum > span:not(:last-child)::after { content: "\00b7"; margin: 0 3px 0 7px; }

/* One person: the pages share one grid cell; the active one is the only one that shows. */
.stage { display: grid; margin-top: 14px; background: var(--surface); border-radius: 18px; overflow: hidden; }
.page { grid-area: 1 / 1; min-width: 0; display: flex; flex-direction: column; padding: 14px 20px 16px;
  opacity: 0; visibility: hidden; transform: translateX(-22px);
  transition: opacity 150ms ease, transform 320ms var(--ease), visibility 0s linear 320ms; }
.page[data-active] ~ .page { transform: translateX(22px); }
.page[data-active] { opacity: 1; visibility: visible; transform: none;
  transition: opacity 280ms ease 90ms, transform 420ms var(--ease), visibility 0s; }

.meta { display: flex; align-items: center; gap: 12px; min-height: 22px; }
.cmeta { display: flex; align-items: center; gap: 6px; flex: 1 1 auto; min-width: 0; overflow: hidden;
  font-size: 12px; color: var(--ink-3); white-space: nowrap; }
/* The author gives way first; the community only when it alone takes more than half of the line. */
.cmeta .where { color: var(--ink-2); flex: 0 0 auto; max-width: 55%; overflow: hidden; text-overflow: ellipsis; }
.cmeta .when { flex: none; }
.cmeta .author { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.cmeta .when::before, .cmeta .author::before { content: "\00b7"; margin-right: 6px; }
.badge { display: inline-grid; place-items: center; width: 22px; height: 22px; flex: none; border-radius: 50%;
  background: var(--raised); color: var(--ink-2); font-size: 10.5px; font-weight: 600; }
.statetxt { flex: none; display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ink-2); }
.statetxt::before { content: ""; width: 5px; height: 5px; border-radius: 50%; background: var(--accent); }
.whyt { flex: none; display: inline-flex; align-items: center; gap: 3px; margin-right: -6px; padding: 4px 6px 4px 10px;
  font-size: 12px; color: var(--ink-3); }
.whyt:hover { color: var(--ink); }
.whyt svg { width: 13px; height: 13px; transition: transform 320ms var(--ease); }
.page[data-why] .whyt svg { transform: rotate(180deg); }
.page[data-why] .whyt { color: var(--ink); }

.title { margin: 10px 0 0; font-size: 16px; font-weight: 500; line-height: 1.34; letter-spacing: -0.02em;
  color: var(--ink); overflow-wrap: anywhere; display: -webkit-box; -webkit-box-orient: vertical;
  -webkit-line-clamp: 2; overflow: hidden; }
.title.solo { -webkit-line-clamp: 3; }
.excerpt { margin: 4px 0 0; font-size: 13px; line-height: 1.55; color: var(--ink-2); overflow-wrap: anywhere;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }
.page[data-kind="new"] .excerpt { -webkit-line-clamp: 4; }

/* "Why?": the person and the angle, behind one small disclosure. Closed it takes no room. */
.why { display: grid; grid-template-rows: 0fr; visibility: hidden;
  transition: grid-template-rows 340ms var(--ease), visibility 0s linear 340ms; }
.page[data-why] .why { grid-template-rows: 1fr; visibility: visible; transition-delay: 0s; }
.why > div { min-height: 0; overflow: hidden; }
.why dl { margin: 10px 0 0; padding: 11px 14px; display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px 20px; border-radius: 12px; background: var(--well); }
dt { font-size: 10.5px; letter-spacing: 0.07em; text-transform: uppercase; color: var(--ink-3); }
dd { margin: 3px 0 0; font-size: 12.5px; line-height: 1.55; color: var(--body); }
.why dd { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 4; overflow: hidden; }

/* The region: what the person decides on. It sits at the bottom of the page, so the buttons are
   in the same place for every person; while a send is under way it keeps the height it had. */
.region { margin-top: auto; padding-top: 14px; display: flex; flex-direction: column; }
.page[data-kind="orphan"] .region { margin-top: 0; }
/* In every state of a send the box sits where the buttons were, whatever it holds. */
.region[data-phase]:not([data-phase="idle"]) .send { margin-top: auto; padding-top: 12px; }

/* The reply, drawn as the post it will become (room.css, `.st-room-preview`), in a few lines. */
.pv { --pvbg: #0e1113; --pvline: rgba(255, 255, 255, 0.06); }
.pv[data-p="X"] { --pvbg: #000; --pvline: #2f3336; }
.pv-label { margin-bottom: 7px; font-size: 10.5px; letter-spacing: 0.07em; text-transform: uppercase; color: var(--ink-3); }
.pvb { padding: 10px 14px 11px; border-radius: 12px; background: var(--pvbg); box-shadow: inset 0 0 0 1px var(--pvline);
  color: #dcdedf; }
.pv-head { display: flex; align-items: center; gap: 6px; min-height: 22px; font-size: 12px; color: #8a9499; }
.pv-head b { font-weight: 600; color: #eceeef; overflow-wrap: anywhere; }
.av { display: grid; place-items: center; width: 22px; height: 22px; flex: none; margin-right: 2px; border-radius: 50%;
  background: #2a2f33; color: #d5d9db; font-size: 9px; font-weight: 600; letter-spacing: 0.02em; }
.chrome { margin-left: auto; display: inline-flex; align-items: center; gap: 12px; padding-left: 12px; opacity: 0.7; font-size: 11.5px; }
.chrome > span { display: inline-flex; align-items: center; gap: 4px; }
.chrome svg { width: 13px; height: 13px; }
.re { margin-top: 2px; font-size: 12px; color: #8a9499; } .re span { color: #c9cdd0; }
.pv-where { margin-top: 6px; font-size: 12px; font-weight: 600; color: #eceeef; }
.pv-title { margin-top: 4px; font-size: 14px; font-weight: 500; line-height: 1.4; color: #eceeef; }
.ptw { position: relative; margin-top: 4px; }
.pt { font-size: 13.5px; line-height: 1.5; color: #dcdedf; white-space: pre-wrap; overflow-wrap: anywhere;
  max-height: calc(4 * 1.5em); overflow: hidden; }
.ptw[data-lines="3"] .pt { max-height: calc(3 * 1.5em); }
.ptw[data-lines="2"] .pt { max-height: calc(2 * 1.5em); }
/* "Show all" shows all: the bound (14 lines) is for a reply no card should be asked to hold, and then the
   box scrolls, with a scrollbar that is always drawn (an overlay one would hide that there is more). */
.ptw[data-open] .pt { max-height: calc(14 * 1.5em); overflow-y: auto; }
.ptw[data-open] .pt::-webkit-scrollbar { width: 6px; }
.ptw[data-open] .pt::-webkit-scrollbar-thumb { border-radius: 3px; background: #3a4044; }
/* "Show all" sits on the end of the last line, on a fade of the box's own colour: no row of its own. */
.more { display: none; position: absolute; right: 0; bottom: 0; padding: 0 0 0 48px; font-size: 13px; line-height: 1.5em;
  color: #c9cdd0; background: linear-gradient(90deg, transparent, var(--pvbg) 26px); border-radius: 0; }
.more:hover { color: #fff; }
.ptw[data-over] .more { display: block; }
.ptw[data-open] .more { position: static; display: block; margin: 4px 0 0 auto; padding: 0; background: none; }
.qt { display: grid; gap: 2px; margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid var(--line); }
.qt .t { font-size: 13px; font-weight: 500; line-height: 1.4; color: var(--ink); overflow-wrap: anywhere;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }
.qt .by { font-size: 11.5px; color: var(--ink-3); }
.qt .pv-label { margin: 0; }

.note { margin: 10px 0 0; font-size: 11.5px; line-height: 1.5; color: var(--ink-3); }
.note [data-expired] { color: var(--warn); }
.acts { margin-top: 12px; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 6px 8px; }
.acts .lead { grid-column: 1; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; }
.acts .hint { grid-column: 2; padding-left: 8px; font-size: 11.5px; line-height: 1.45; color: var(--ink-3); }
.acts .hint [data-expired] { color: var(--warn); }
.acts .end { grid-column: 3; justify-self: end; display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 0 14px; }
.primary, .soft { min-height: 34px; padding: 0 17px; font-weight: 500; }
.primary { background: #eaeae2; color: #101010; }
.primary:hover { background: #fff; }
.soft { background: var(--raised); color: var(--ink); }
.soft:hover { background: #272725; }
.quiet { padding: 4px 0; font-size: 12px; background: transparent; color: var(--ink-3); }
.quiet:hover { color: var(--ink); }
.metrics { display: flex; gap: 18px; margin-top: 12px; font-size: 12px; color: var(--ink-3); }
.metrics b { font-weight: 400; color: var(--ink); font-variant-numeric: tabular-nums; }
.metrics i { font-style: normal; color: var(--warn); }

/* A published reply: the numbers, each one measured or "not measured". */
.res { margin: 0; display: grid; grid-template-columns: repeat(3, auto); justify-content: start; gap: 2px 32px; }
.res > div { min-width: 0; }
.res dd { margin: 2px 0 0; font-size: 14px; color: var(--ink); font-variant-numeric: tabular-nums; }
.res dd.n { font-size: 22px; line-height: 1.25; letter-spacing: -0.02em; }
.res b { font-weight: 400; }
.res i { font-style: normal; font-size: 13px; color: var(--warn); }
.quote { margin-top: 12px; padding-left: 12px; border-left: 2px solid var(--line); font-size: 12.5px; line-height: 1.55;
  color: var(--ink-2); white-space: pre-wrap; overflow-wrap: anywhere; display: -webkit-box; -webkit-box-orient: vertical;
  -webkit-line-clamp: 2; overflow: hidden; }

/* The send box: the two-step confirmation and what became of the request, in the region. */
.send { font-size: 14px; line-height: 1.5; color: var(--body); }
.send b { font-weight: 500; color: var(--ink); }
.send small { display: block; margin-top: 6px; font-size: 12px; line-height: 1.55; color: var(--ink-3); }
.send:focus { outline: none; }
.send-a { margin-top: 14px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; }
.send[data-p="refused"] .msg::before, .send[data-p="failed"] .msg::before { content: ""; display: inline-block; width: 6px; height: 6px;
  margin: 0 8px 2px 0; border-radius: 50%; background: var(--warn); }
.ok { display: inline-grid; place-items: center; width: 20px; height: 20px; margin-right: 8px; vertical-align: -5px;
  border-radius: 50%; background: rgba(211, 228, 175, 0.15); color: var(--accent); }
.ok svg { width: 12px; height: 12px; stroke-width: 2.2; }
.wait::after { content: ""; display: inline-block; width: 5px; height: 5px; margin-left: 8px; vertical-align: 2px;
  border-radius: 50%; background: var(--accent); animation: pulse 1.1s ease-in-out infinite; }
/* Manual sending (v8): "Posted it?" and the copy fallback sit in the send box, where the buttons were. */
.send .posted { margin-top: 10px; }
.plr { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 0 14px; }
.send .plab { font-size: 12px; color: var(--ink-3); }
.pact { display: inline-flex; gap: 14px; }
.pact .quiet { padding: 2px 0; }
.prow { margin-top: 4px; display: flex; align-items: center; gap: 8px; }
input.link { flex: 1 1 auto; min-width: 0; height: 34px; padding: 0 14px; border: 0; border-radius: 999px;
  background: var(--well); color: var(--ink); font: inherit; font-size: 13px; box-shadow: inset 0 0 0 1px var(--line); }
input.link::placeholder { color: var(--ink-3); }
input.link:focus-visible { outline: 2px solid rgba(211, 228, 175, 0.55); outline-offset: 2px; }
.copybox { display: block; width: 100%; margin-top: 8px; padding: 8px 12px; border: 0; border-radius: 10px; resize: none;
  background: var(--well); color: var(--body); font: inherit; font-size: 12.5px; line-height: 1.5; box-shadow: inset 0 0 0 1px var(--line); }
.send .badlink { margin-top: 6px; font-size: 12px; color: var(--warn); }
.empty { margin-top: 16px; padding: 18px 20px; border-radius: 16px; background: var(--surface); color: var(--ink-2); }
.empty.load { min-height: 56px; display: flex; align-items: center; }
.load .wait::after { margin-left: 0; }
@keyframes pulse { 0%, 100% { opacity: 0.25; } 50% { opacity: 1; } }

/* Paging: dots and "1 of 4" on the left, the two chevrons on the right. */
.bar { margin-top: 8px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 2px 24px; }
.pager { display: flex; align-items: center; gap: 4px; min-height: 28px; }
.dots { display: flex; align-items: center; }
.dot { position: relative; width: 16px; height: 28px; transition: width 340ms var(--ease); }
.dot::before { content: ""; position: absolute; top: 50%; left: 50%; width: 6px; height: 6px; margin: -3px 0 0 -3px;
  border-radius: 999px; background: rgba(255, 255, 255, 0.22); transition: width 340ms var(--ease), margin 340ms var(--ease), background-color 240ms var(--ease); }
.dot:hover::before { background: rgba(255, 255, 255, 0.45); }
.dot[aria-current="true"] { width: 26px; }
.dot[aria-current="true"]::before { width: 18px; margin-left: -9px; background: var(--ink); }
.count { margin-left: 8px; font-size: 12px; color: var(--ink-3); font-variant-numeric: tabular-nums; }
.nav { display: grid; place-items: center; width: 26px; height: 26px; background: var(--surface); color: var(--ink-2); }
.nav:hover:not([disabled]) { background: var(--raised); color: var(--ink); }
.nav[disabled] { opacity: 0.3; }
.foot { flex: 1 0 auto; display: flex; align-items: center; justify-content: flex-end; gap: 14px; min-height: 28px; font-size: 11.5px; color: var(--ink-3); }
.foot .quiet { padding: 4px 0; white-space: nowrap; }

@media (max-width: 600px) {
  :root { --side: 22px; }
  .page { padding: 14px 16px 16px; }
  .chrome .lbl { display: none; }
  .acts { grid-template-columns: auto 1fr; }
  .acts .end { grid-column: 2; }
  .acts .hint { grid-column: 1 / -1; grid-row: 2; padding-left: 0; }
  .foot { justify-content: space-between; }
}
@media (max-width: 440px) {
  :root { --side: 18px; }
  .region[data-phase]:not([data-phase="idle"]) .ptw[data-lines="2"]:not([data-open]) .pt { max-height: calc(1 * 1.5em); }
  .panel { padding-top: 18px; padding-bottom: 16px; }
  h1 { margin-top: 14px; }
  .stage { margin-top: 16px; border-radius: 16px; }
  .chrome { display: none; }
  .res { gap: 2px 22px; }
}
@media (prefers-reduced-motion: reduce) {
  .page, .why, .dot, .dot::before, .whyt svg, .wait::after { animation: none; }
  * { transition: none !important; }
}
</style>
</head>
<body>
<div class="panel" id="app"></div>
<script>
(function () {
  var MARK = "data:image/png;base64,__MARK__";
  // Every word the card says, one dictionary per language (`apps_ui.CARD_STRINGS`, put here by the server).
  var DICT = __STRINGS__;
  var LANG = "en";
  var LOCALE = "en-US";
  var hostLocale = "";   // the host's own locale, from `ui/initialize` or `window.openai`
  // The timezone the Pokój tells its deadlines in (`room-words.ts`), the same in both languages.
  var TZ = "Europe/Warsaw";
  var nextId = 1;
  var pending = {};
  var items = [];
  var room = null;
  var drawn = false;
  var lastData = null;
  // Sending from the card (v4). `via` is how a call reaches the server: the MCP Apps bridge, or
  // `window.openai` when that is all the host has. `canSend` is false only when the host SAID it
  // cannot proxy a tool call; a host that says nothing is tried, and a refusal says why.
  var via = "bridge";
  var canSend = true;

  /*LANG-BEGIN*/
  // THE LANGUAGE OF THE CARD (v5). Decided in this order, first match wins:
  //   1. an explicit setting: `jezyk` ("pl" or "en") in the result of `room.show`, which the server
  //      sets from WENEXT_LANG (the launcher passes it; `apps_ui.card_language`). It lets a person
  //      pin the language whatever their host reports, and keeps a Polish install Polish;
  //   2. the host's locale: `hostContext.locale` of the `ui/initialize` answer (MCP Apps), or
  //      `window.openai.locale`, and last the browser's own language when the host says nothing;
  //   3. `pl`, `pl-PL`, `pl_PL` give Polish; any other locale, or none at all, gives English.
  // A pure function, so a test can run it on its own (it is cut out of this file by its markers).
  function pickLang(explicit, locale) {
    var e = String(explicit == null ? "" : explicit).trim().toLowerCase();
    if (e === "pl" || e === "en") return e;
    var l = String(locale == null ? "" : locale).trim().toLowerCase().replace(/_/g, "-");
    return /^pl(-|$)/.test(l) ? "pl" : "en";
  }
  // The Intl locale for dates and numbers: pl-PL for Polish; for English the host's own English
  // (en-GB puts the day first, en-US the month) when it has one, else en-US.
  function localeFor(lang, locale) {
    if (lang === "pl") return "pl-PL";
    var l = String(locale == null ? "" : locale).trim().replace(/_/g, "-");
    if (/^en(-[A-Za-z0-9]+)*$/.test(l)) {
      try { if (Intl.DateTimeFormat.supportedLocalesOf([l]).length) return l; } catch (e) {}
    }
    return "en-US";
  }
  /*LANG-END*/
  function setLang(explicit) {
    var loc = hostLocale || (typeof navigator !== "undefined" && navigator.language) || "";
    LANG = pickLang(explicit, loc);
    LOCALE = localeFor(LANG, loc);
    try { document.documentElement.lang = LANG; } catch (e) {}
  }

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  // A string of the dictionary, in the card's language (English, then the key itself, when it is missing).
  function T(key) {
    var v = (DICT[LANG] || DICT.en)[key];
    if (v == null) v = DICT.en[key];
    return v == null ? key : v;
  }
  // A string that may be absent: the label of a code the card has no word for says the code.
  function L(key, fallback) { var v = (DICT[LANG] || DICT.en)[key]; return v == null ? fallback : v; }
  // Fill {name} with a value, HTML-escaped (S) or as it is (P). The template is the card's own and
  // may carry markup (<b>, <strong>); a value is never markup, except the few the card BUILT itself
  // from escaped parts and names in `raw` (the bold list of accounts in the consent sentence).
  function fmt(tpl, vars, plain, raw) {
    return String(tpl).replace(/\{(\w+)\}/g, function (m, k) {
      var v = vars && vars[k] != null ? vars[k] : "";
      return plain || (raw && raw.indexOf(k) >= 0) ? String(v) : esc(v);
    });
  }
  function S(key, vars, raw) { return fmt(T(key), vars, false, raw); }   // HTML out
  function P(key, vars) { return fmt(T(key), vars, true); }    // plain text out (not put in markup unescaped)
  // Polish has three forms (1, 2 to 4, the rest), English two (1, the rest).
  function plural(n, forms) {
    if (LANG !== "pl") return n === 1 ? forms[0] : forms[1];
    if (n === 1) return forms[0];
    var t = n % 10, h = n % 100;
    return (t >= 2 && t <= 4 && (h < 12 || h > 14)) ? forms[1] : forms[2];
  }
  function PL(n, key, vars) { return fmt(plural(n, T(key)), vars, false); }

  function send(msg) { try { window.parent.postMessage(msg, "*"); } catch (e) {} }
  // The whole answer message ({result} or {error}), or null when nothing came back in time.
  function rpc(method, params, ms) {
    var id = nextId++;
    send({ jsonrpc: "2.0", id: id, method: method, params: params || {} });
    return new Promise(function (resolve) { pending[id] = resolve; setTimeout(function () {
      if (pending[id]) { delete pending[id]; resolve(null); } }, ms || 4000); });
  }
  function request(method, params) {
    return rpc(method, params).then(function (m) { return m && m.result ? m.result : null; });
  }
  function notify(method, params) { send({ jsonrpc: "2.0", method: method, params: params || {} }); }

  // Addresses come from strangers' posts and from the platforms: only http(s) is ever opened.
  function web(u) { return typeof u === "string" && /^https?:\/\//i.test(u) ? u : null; }

  function minutesSince(iso) {
    if (!iso) return null; var t = new Date(iso).getTime(); if (isNaN(t)) return null;
    return Math.floor((Date.now() - t) / 60000);
  }
  function ago(iso) {
    var m = minutesSince(iso); if (m === null) return null;
    if (m < 2) return T("ago.now"); if (m < 60) return P("ago.min", { m: m });
    var h = Math.floor(m / 60); if (h < 24) return P("ago.hour", { h: h });
    var d = Math.floor(h / 24); return d === 1 ? T("ago.yesterday") : P("ago.days", { d: d });
  }
  function clock() {
    var d = new Date();
    try { return d.toLocaleTimeString(LOCALE, { hour: LANG === "pl" ? "2-digit" : "numeric", minute: "2-digit" }); } catch (e) {}
    return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }
  function num(v) { try { return Number(v).toLocaleString(LOCALE); } catch (e) { return String(v); } }
  // A null number is "not measured", never 0 (the rule of the dashboard).
  function figure(v) { return v == null ? "<i>" + esc(T("fig.none")) + "</i>" : "<b>" + esc(num(v)) + "</b>"; }

  // The approval window, in the words of the Pokój (`expirySentence` in room-words.ts).
  function expiry(iso) {
    if (!iso) return null;
    var at = new Date(iso).getTime(); if (isNaN(at)) return { text: T("expiry.unreadable"), expired: false };
    var m = Math.ceil((at - Date.now()) / 60000);
    if (m <= 0) return { text: T("expiry.closed"), expired: true };
    if (m < 60) return { text: P("expiry.min", { m: m }), expired: false };
    var h = Math.floor(m / 60); if (h < 36) return { text: P("expiry.hour", { h: h }), expired: false };
    var day = "";
    // The deadline is told in Warsaw time. English names the zone, so a reader elsewhere is not misled.
    var shape = { day: "numeric", month: "long", hour: LANG === "pl" ? "2-digit" : "numeric", minute: "2-digit", timeZone: TZ };
    if (LANG !== "pl") shape.timeZoneName = "short";
    try { day = new Date(at).toLocaleString(LOCALE, shape); } catch (e) {}
    return { text: day ? P("expiry.date", { day: day }) : P("expiry.hour", { h: h }), expired: false };
  }

  var IC = {
    up: '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    down: '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    chat: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
    share: '<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="m16 6-4-4-4 4"/><path d="M12 2v13"/>',
    repeat: '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    heart: '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>',
    chevD: '<path d="m6 9 6 6 6-6"/>',
    chevL: '<path d="m15 18-6-6 6-6"/>',
    chevR: '<path d="m9 18 6-6-6-6"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>'
  };
  function ic(name) { return '<svg viewBox="0 0 24 24" aria-hidden="true">' + IC[name] + '</svg>'; }

  function handleOf(platform, raw) {
    var x = platform === "X";
    var clean = String(raw || "").trim().replace(x ? /^@/ : /^u\//, "");
    if (!clean) return T(x ? "handle.x" : "handle.reddit");
    return (x ? "@" : "u/") + clean;
  }
  function initials(handle) {
    var s = String(handle).replace(/^(u\/|@)/, "").replace(/[^A-Za-z0-9]+/g, " ").trim();
    if (!s) return "?";
    var parts = s.split(" ");
    return (parts.length > 1 ? parts[0][0] + parts[1][0] : s.slice(0, 2)).toUpperCase();
  }
  function communityOf(platform, c) {
    if (platform === "X") return "X";
    return c ? "r/" + String(c).replace(/^r\//, "") : "Reddit";
  }
  function whoOf(platform, a) {
    if (!a) return null;
    return platform === "REDDIT" ? "u/" + String(a).replace(/^u\//, "") : "@" + String(a).replace(/^@/, "");
  }

  // The reply as the platform will show it, in a few lines. The text is EXACTLY what goes out: never
  // trimmed or softened, only cut to the box, with a way to read the rest ("Show all").
  function preview(d, withThread, compact) {
    var x = d.platforma === "X", own = d.rodzaj === "OWN_POST";
    var handle = handleOf(d.platforma, d.konto);
    var target = d.w_odpowiedzi_na;
    var quote = "";
    if (withThread && !compact && !own && !x && target) {
      var by = [communityOf("REDDIT", d.spolecznosc), whoOf("REDDIT", target.autor)].filter(Boolean).join(" · ");
      quote = '<div class="qt"><span class="pv-label">' + esc(T("pv.thread")) + '</span>' +
        '<span class="t">' + esc(target.tytul || T("pv.threadYours")) + '</span>' +
        '<span class="by">' + esc(by) + '</span></div>';
    }
    var replying = (x && !own && target && target.autor)
      ? '<div class="re">' + esc(T("pv.replyingTo")) + ' <span>' + esc(whoOf("X", target.autor)) + '</span></div>' : "";
    var title = (own && !x && d.tytul) ? '<div class="pv-title">' + esc(d.tytul) + '</div>' : "";
    var where = own && !x ? '<div class="pv-where">' + esc(communityOf("REDDIT", d.spolecznosc)) + '</div>' : "";
    var chrome = x
      ? '<span class="chrome" aria-hidden="true"><span>' + ic("chat") + '</span><span>' + ic("repeat") + '</span><span>' +
        ic("heart") + '</span><span>' + ic("share") + '</span></span>'
      : '<span class="chrome" aria-hidden="true"><span>' + ic("up") + ic("down") + '</span><span>' + ic("chat") +
        '<span class="lbl">' + esc(T("pv.reply")) + '</span></span><span>' + ic("share") +
        '<span class="lbl">' + esc(T("pv.share")) + '</span></span></span>';
    var open = !!expanded[d.revision_id];
    var lines = compact ? 2 : title ? 3 : 4;
    return '<div class="pv" data-p="' + esc(x ? "X" : "REDDIT") + '">' + quote +
      (compact ? '' : '<div class="pv-label">' + esc(T(own ? "pv.labelPost" : "pv.labelReply")) + '</div>') +
      '<div class="pvb"><div class="pv-head"><span class="av">' + esc(initials(handle)) + '</span><b>' + esc(handle) +
      '</b><span>·</span><span>' + esc(T("pv.now")) + '</span>' + chrome + '</div>' + replying + where + title +
      '<div class="ptw" data-lines="' + lines + '"' + (open ? ' data-open="1" data-over="1"' : '') + '><div class="pt">' +
      esc(d.tekst) + '</div><button class="more" data-more="1">' + esc(T(open ? "pv.less" : "pv.more")) +
      '</button></div></div></div>';
  }

  // Whether the card may send this draft, and what is left of the time the person has.
  function draftState(d) {
    var ex = expiry(d.wygasa), expired = !!(ex && ex.expired);
    // The send button only for a host that can carry the call, and a draft whose digest the card was
    // given: without it a confirmation cannot be tied to the text on the screen.
    return { ex: ex, expired: expired, able: canSend && !expired && /^[0-9a-f]{64}$/.test(String(d.odcisk || "")) };
  }
  function originBtn(i, url) {
    return web(url) ? '<button class="quiet" data-open="' + i + '">' + esc(T("ap.original")) + '</button>' : '';
  }

  // Under the preview: which account it leaves from, how long the person has, and the buttons.
  function approval(d, i) {
    var e = items[i], url = e ? originOf(e) : null;
    var handle = handleOf(d.platforma, d.konto), s = draftState(d);
    var go = function (cls) {
      return web(room) ? '<button class="' + cls + '" data-approve="' + i + '">' + esc(T("ap.approve")) + '</button>' : '';
    };
    var note = (d.konto ? S("ap.goes", { h: handle }) : S("ap.goesProject")) +
      (s.ex ? ' <span' + (s.expired ? ' data-expired="1"' : '') + '>' + esc(s.ex.text) + '.</span>' : "") +
      (!s.expired && !go("x") && !s.able ? S("ap.inRoom") : "");
    var lead = s.able ? act("send", i, T("ap.send"), "primary") : (s.expired ? "" : go("primary"));
    return preview(d, !(e && e.opp)) + '<div class="acts"><span class="lead">' + lead +
      '<button class="soft" data-fix="' + i + '">' + esc(T("ap.edit")) + '</button></span><span class="hint">' + note +
      '</span><span class="end">' + (s.able ? go("quiet") : "") + originBtn(i, url) + '</span></div>';
  }
  // What the region of a draft holds in the phase of its send. Idle: the reply, who sends it, the buttons.
  // In every other phase the reply stays (shorter) and the box under it, where the buttons were, says
  // what the click agrees to or where the request is.
  function regionInner(d, i) {
    if (manual) {
      var m = mOf(d); m.i = i;
      if (m.phase === "idle") return manualApproval(d, i);
      // While the reply sits in the box to copy by hand, the box IS the reply: it is not drawn twice.
      return (m.phase === "opened" && m.kind !== "x" && m.copied === false ? "" : preview(d, false, true)) + manualPanel(d, m);
    }
    var st = sendOf(d); st.i = i;
    return st.phase === "idle" ? approval(d, i) : preview(d, false, true) + sendPanel(d, st);
  }
  function region(d, i) {
    return '<div class="region" data-box="' + esc(d.revision_id) + '" data-phase="' + esc(manual ? mOf(d).phase : sendOf(d).phase) + '">' +
      regionInner(d, i) + '</div>';
  }

  // ---- Send: two steps in the person's own agent, then follow the request (see the docstring).
  function sendOf(d) { return sender.of(d); }
  function box(html, phase, actions) {
    return '<div class="send" role="status" tabindex="-1" data-p="' + phase + '"><div class="msg">' + html + '</div>' +
      (actions ? '<div class="send-a">' + actions + '</div>' : '') + '</div>';
  }
  function act(name, i, label, cls) {
    return '<button class="' + cls + '" data-act="' + name + '" data-i="' + i + '">' + esc(label) + '</button>';
  }
  function term(iso) {
    if (!iso) return null; var t = new Date(iso); if (isNaN(t.getTime())) return null;
    try { return t.toLocaleString(LOCALE, { day: "numeric", month: "long", timeZone: TZ }); } catch (e) { return null; }
  }
  // The accounts a click will sign, as the server named them in the plan (`konta`), each in bold:
  // "@a", "@a and u/b", "@a, u/b and @c". The card only draws them: it never sends them anywhere.
  function accountNames(list, d) {
    var names = (list || []).map(function (a) { return '<b>' + esc(handleOf(a.platforma, a.konto)) + '</b>'; });
    if (!names.length) names = ['<b>' + esc(handleOf(d.platforma, d.konto)) + '</b>'];
    return { html: names.length === 1 ? names[0] : names.slice(0, -1).join(", ") + esc(T("c.and")) + names[names.length - 1], n: names.length };
  }
  // What this click will do about the scope, in the sentence the person agrees to.
  function consent(d, st) {
    var handle = handleOf(d.platforma, d.konto);
    var plan = st.plan || {};
    var own = d.rodzaj === "OWN_POST";
    var what = T(own ? "c.thisPost" : "c.thisReply");
    // A confirmation asked for again after the scope moved shows its new sentence, and says so.
    var changed = st.changed ? '<small>' + esc(T("c.changed")) + '</small>' : '';
    if (plan.rodzaj === "PODPISZ" || plan.rodzaj === "ROZSZERZ") {
      var sign = plan.rodzaj === "PODPISZ";
      var names = accountNames(plan.konta, d);
      var lim = plan.limity || {}, until = term(plan.wygasa);
      var head = sign
        ? S(own ? "c.signPost" : "c.sign", { accounts: names.html }, ["accounts"])
        : fmt(plural(names.n, T("c.widen")), { what: what, accounts: names.html }, false, ["accounts"]);
      var small = S(sign ? "c.small" : "c.smallWiden", {
        until: until ? P("c.until", { until: until }) : "",
        limits: lim.godzina != null && lim.doba != null ? P("c.limits", { h: num(lim.godzina), d: num(lim.doba) }) : ""
      });
      return head + changed + '<small>' + small + '</small>';
    }
    return S(own ? "c.plainPost" : "c.plainReply", { h: handle }) + changed;
  }
  function sendPanel(d, st) {
    var i = st.i;
    switch (st.phase) {
      case "checking": return box('<span class="wait">' + esc(T("sp.checking")) + '</span>', "checking");
      case "waiting":
        return box('<span class="wait">' + esc(T("sp.waiting")) + '</span><small>' + esc(T("sp.waitingNote")) + '</small>',
          "waiting", act("cancel", i, T("sp.cancel"), "quiet"));
      case "confirm":
        return box(consent(d, st), "confirm", act("go", i, T("sp.yes"), "primary") + act("cancel", i, T("sp.cancel"), "quiet"));
      case "sending": return box('<span class="wait">' + esc(T("sp.sending")) + '</span>', "sending");
      case "queued":
        return box('<b>' + esc(T("sp.queued")) + '</b> ' + esc(T(st.slow ? "sp.queuedSlow" : "sp.queuedSoon")) +
          (st.slow ? '' : '<span class="wait"></span>'), "queued",
          st.slow ? act("refresh", i, T("sp.recheck"), "soft") : "");
      case "published":
        return box('<span class="ok">' + ic("check") + '</span><b>' + esc(T("sp.posted")) + '</b>' + (st.seen ? ' ' + esc(P("sp.visibility", { seen: st.seen })) : ''), "published",
          (web(st.url) ? act("live", i, T("sp.view"), "primary") : "") + act("close", i, T("sp.done"), "quiet"));
      case "failed":
        return box('<b>' + esc(T("sp.failed")) + '</b> ' + esc(st.reason || T("sp.failedDefault")) +
          (st.note ? '<small>' + esc(st.note) + '</small>' : ''), "failed", act("close", i, T("sp.close"), "quiet"));
      case "refused":
        // "Try again" is always there: a refusal is a state to leave, never a dead end. It asks the
        // server again from the start and ends, as the first press does, at a confirmation to click.
        return box(esc(st.reason || T("sp.refusedDefault")), "refused",
          (web(st.presence) ? act("presence", i, T("sp.presence"), "primary") : "") +
          act("retry", i, T("sp.retry"), web(st.presence) ? "soft" : "primary") + act("close", i, T("sp.close"), "quiet"));
      default: return "";
    }
  }
  function paintSend(d) {
    var st = sendOf(d), el = document.querySelector('[data-box="' + d.revision_id + '"]');
    if (!el) return;
    var had = !!(document.activeElement && el.contains(document.activeElement));
    // While a send is under way the region keeps the height it had when it was idle: the card does not
    // jump. A reply that was open is folded first, so the height kept is the one of the page as it was.
    if (st.phase === "idle") el.style.minHeight = "";
    else if (!el.style.minHeight) {
      var wide = el.querySelector(".ptw[data-open]"); if (wide) fold(wide);
      el.style.minHeight = el.offsetHeight + "px";
    }
    el.setAttribute("data-phase", st.phase);
    el.innerHTML = regionInner(d, st.i);
    // The button that was pressed is gone: the focus goes to the box (which sends nothing), never to a
    // button, so a key held down cannot reach "Yes, send".
    var box = el.querySelector(".send");
    if (had && box) { try { box.focus({ preventScroll: true }); } catch (e) {} }
    fit(); resize();
  }
  // Why a send was refused, as one sentence. Polish shows the server's own sentence, which can carry
  // details (which limit is spent). Any other language shows the card's sentence for the refusal's
  // `kod`, and falls back to the server's Polish one for a code it has no words for.
  function reasonOf(data, why) {
    var own = data && data.kod ? L("ref." + data.kod, null) : null, server = data && data.powod;
    return (LANG === "pl" ? server || own : own || server) || why || T("e.noAnswer");
  }

  // One tool call to the server, through the host. The result is { result } or { error }.
  function callTool(name, args) {
    if (via === "openai" && window.openai && window.openai.callTool) {
      return Promise.resolve().then(function () { return window.openai.callTool(name, args); }).then(
        function (r) { return { result: r && typeof r === "object" ? r : {} }; },
        function () { return { error: "rejected" }; });
    }
    return rpc("tools/call", { name: name, arguments: args }, 30000).then(function (m) {
      if (!m) return { error: "silent" };
      if (m.error) return { error: "rejected" };
      return { result: m.result || {} };
    });
  }

  /*SEND-BEGIN*/
  // THE SEND MACHINE (v6). One box per draft, one phase at a time:
  //
  //   idle -> checking -> confirm -> sending -> queued -> published | failed
  //              |   ^       ^
  //              v   |       |
  //           waiting +------+      the publisher's queue is busy: ask again until it is not
  //   refused                       any refusal; "Try again" starts from the top
  //
  // THE ONE RULE: a send leaves this card in ONE place, the `go` step, and only the "Yes, send"
  // button reaches it (a click in this frame, through the document's click handler). Everything
  // that heals a refusal by itself (waiting for the queue, asking again for a confirmation) ends at
  // the phase `confirm`, which waits for that click. Asking for a confirmation (`room.send_token`)
  // sends nothing and signs nothing: it is a question, and the server answers it the same way each
  // time. A test holds this in node, with a fake host and a fake clock.
  //
  // Pure: no DOM, no window. What it needs comes in through `env`: `call` (a tool, resolves to
  // { result } or { error }), `later` (a timer), `paint` (redraw a box), `T` (a word), `lang`,
  // `reason` (a refusal's sentence) and `open` (a link).
  function makeSender(env) {
    var sends = {};            // revision_id -> the state of its box, kept across redraws
    var seq = 0;               // numbers the runs; what a stale run's timer or answer brings is dropped
    var WAIT_STEP = 4000;      // ask the queue again every 4 s ...
    var WAIT_LIMIT = 60000;    // ... for about a minute, then show the refusal with "Try again"
    var FOLLOW_TRIES = 36;     // then follow the request for about three minutes

    // The data object of a result: `structuredContent`, else the JSON of the first text block.
    function dataOf(res) {
      var r = res && res.result; if (!r) return null;
      if (r.structuredContent && typeof r.structuredContent === "object") return r.structuredContent;
      try { return JSON.parse(r.content[0].text); } catch (e) { return null; }
    }
    // The confirmation rides ONLY in `_meta`: never in text, never in structured content.
    function tokenOf(res) {
      var m = res && res.result && res.result._meta;
      var t = m && m["wenext/potwierdzenie"];
      return typeof t === "string" && t ? t : null;
    }
    function own(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
    function of(d) { return sends[d.revision_id] || (sends[d.revision_id] = { phase: "idle", run: 0 }); }
    // Replace the state of a box with `patch`, keeping which button it is (`i`) and which run it is.
    function set(d, patch) {
      var st = of(d), i = st.i, run = st.run, key;
      for (key in st) if (own(st, key)) delete st[key];
      for (key in patch) if (own(patch, key)) st[key] = patch[key];
      st.i = i; if (st.run == null) st.run = run;
      env.paint(d); return st;
    }
    // Is this run still the one the box is in, and in one of these phases? A cancelled or restarted
    // box has a new run, so an old timer or answer finds nothing to do.
    function live(d, run, a, b) {
      var st = sends[d.revision_id];
      return !!st && st.run === run && (st.phase === a || st.phase === b);
    }
    function refuse(d, data, why) {
      set(d, { phase: "refused", reason: env.reason(data, why), presence: data && data.obecnosc_adres });
    }

    // The question: may this draft go, and what will the click do about the scope? The answer is
    // either a refusal, or a confirmation to show. It never sends. `how` carries what the run has
    // done so far: `waited` (ms spent waiting for the queue), `reminted` (the confirmation was asked
    // for again after a send was turned down), `changed` (because the scope moved).
    function mint(d, run, how) {
      env.call("room.send_token", { revision_id: d.revision_id, odcisk: d.odcisk }).then(function (res) {
        if (!live(d, run, "checking", "waiting")) return;
        var data = dataOf(res), token = tokenOf(res);
        if (!res || res.error || !data) return refuse(d, null, res && res.error ? env.T("e.noConnect") : null);
        if (data.stan !== "WYKONANE") {
          if (data.kod === "ZAKRES_KOLEJKA") return wait(d, run, data, how);
          return refuse(d, data);
        }
        if (!token) return refuse(d, null, env.T("e.noToken"));
        set(d, { phase: "confirm", token: token, plan: data.zakres, reminted: !!how.reminted, changed: !!how.changed });
      });
    }
    // The previous reply is still in the publisher's queue and a new signature would stop it: wait,
    // asking again, until the queue is clear. The person is never asked to click again for this.
    function wait(d, run, data, how) {
      if (how.waited >= WAIT_LIMIT) return refuse(d, data);
      set(d, { phase: "waiting", reminted: !!how.reminted, changed: !!how.changed });
      env.later(function () {
        if (!live(d, run, "waiting")) return;
        mint(d, run, { waited: how.waited + WAIT_STEP, reminted: how.reminted, changed: how.changed });
      }, WAIT_STEP);
    }
    function begin(d, from) {
      if (of(d).phase !== from) return;
      var run = ++seq;
      set(d, { phase: "checking", run: run });
      mint(d, run, { waited: 0, reminted: false, changed: false });
    }

    // THE ONLY PLACE A SEND IS MADE. Reached by the "Yes, send" button and by nothing else.
    function go(d) {
      var st = of(d);
      if (st.phase !== "confirm" || !st.token) return;
      var run = st.run, token = st.token, reminted = st.reminted;
      set(d, { phase: "sending", reminted: reminted });
      env.call("room.send_draft", { revision_id: d.revision_id, token: token }).then(function (res) {
        if (!live(d, run, "sending")) return;
        var data = dataOf(res);
        if (!res || res.error || !data) return refuse(d, null, env.T("e.unsure"));
        if (data.stan === "OCZEKUJE" && data.wniosek) {
          set(d, { phase: "queued", request: data.wniosek });
          return follow(d, run, 0);
        }
        // The confirmation went stale (the scope moved, or it expired or was spent): the server
        // turned the send down before it did anything. Ask again, ONCE, and show the new sentence.
        // That ends at `confirm` again, and the person clicks again; a second refusal is shown.
        if (!reminted && (data.kod === "ZAKRES_ZMIENIONY" || data.kod === "POTWIERDZENIE")) {
          set(d, { phase: "checking", reminted: true });
          return mint(d, run, { waited: 0, reminted: true, changed: data.kod === "ZAKRES_ZMIENIONY" });
        }
        refuse(d, data);
      });
    }

    // Ask the server what became of the request until the publisher says. PUBLISHED is the only
    // word that is "Posted": a request in the queue is not a post.
    function follow(d, run, n) {
      env.later(function () {
        if (!live(d, run, "queued")) return;
        env.call("room.send_status", { request_id: of(d).request }).then(function (res) {
          if (!live(d, run, "queued")) return;
          var st = of(d), data = dataOf(res), w = data && data.wniosek;
          if (w && w.status === "PUBLISHED") {
            st.phase = "published"; st.url = w.adres;
            st.seen = w.widocznosc === "CONFIRMED" ? env.T("seen.CONFIRMED") : w.widocznosc === "REMOVED" ? env.T("seen.REMOVED") : env.T("seen.pending");
            return env.paint(d);
          }
          if (w && /^(BLOCKED|SKIPPED|STOPPED)$/.test(w.status)) {
            // The publisher's own reason is in Polish: Polish shows it as the reason, another
            // language shows its own sentence for the status and keeps the Polish one as a note.
            st.phase = "failed";
            st.reason = env.lang() === "pl" && w.powod ? w.powod : env.T("w." + w.status);
            st.note = env.lang() !== "pl" && w.powod ? w.powod : "";
            return env.paint(d);
          }
          if (w && w.status === "UNKNOWN") {
            st.phase = "failed"; st.reason = env.T("e.unknown"); return env.paint(d);
          }
          if (n < FOLLOW_TRIES) return follow(d, run, n + 1);
          st.slow = true; env.paint(d);
        });
      }, n === 0 ? 2500 : 5000);
    }

    // What a button of the box does. `st.i` says which draft; the caller finds it.
    function dispatch(name, d) {
      var st = of(d);
      if (name === "send") begin(d, "idle");
      else if (name === "retry") begin(d, "refused");
      else if (name === "go") go(d);
      else if (name === "cancel" || name === "close") set(d, { phase: "idle", run: ++seq });
      else if (name === "refresh") {
        if (st.phase !== "queued" || !st.request) return;
        st.slow = false; env.paint(d); follow(d, st.run, 0);
      } else if (name === "live") env.open(st.url);
      else if (name === "presence") env.open(st.presence);
    }
    return { of: of, dispatch: dispatch };
  }
  /*SEND-END*/

  var sender = makeSender({
    call: callTool,
    later: function (fn, ms) { return setTimeout(fn, ms); },
    paint: paintSend,
    T: T,
    lang: function () { return LANG; },
    reason: reasonOf,
    open: function (url) { openLink(url); }
  });
  // The click handler's way in: which draft the button belongs to, and what it asked.
  function sendAction(name, i) {
    var e = items[i], d = e && e.draft; if (!d) return;
    sender.dispatch(name, d);
  }

  // ---- MANUAL SENDING (v8, hosted): nothing leaves Wenext, the person posts from their own account.
  // `room.show` says so (`wysylka: "reczna"`); every local session says "karta" or nothing and never
  // reaches this code. One box per draft, one phase at a time:
  //   idle -> opened -> marking -> done      "opened": the reply was copied (Reddit) or X was opened,
  //             |  ^        |                 and the card asks "Posted it? Paste the link";
  //             v  |        v                 "failed": the call did not go through, so ask again or
  //           (idle) +--- failed | asked      ask the agent (`asked`). "done" only records what the
  //                                           person said: the card never calls it verified.
  var manual = false;
  var mstate = {};       // revision_id -> the state of its manual box, kept across redraws
  function mOf(d) { return mstate[d.revision_id] || (mstate[d.revision_id] = { phase: "idle" }); }
  // Only a link to the person's own post on Reddit or X is taken (the server checks the same).
  function ownPostLink(u) { return /^https:\/\/([a-z0-9-]+\.)*(reddit\.com|redd\.it|x\.com|twitter\.com)(\/|\?|#|$)/i.test(String(u || "")); }
  // The clipboard, in three rungs: the async API (the host may not grant it to this frame), a hidden
  // textarea and `execCommand`, and last the person's own Cmd+C on a selected box. Resolves true or false.
  function legacyCopy(text) {
    try {
      var ta = document.createElement("textarea");
      ta.value = text; ta.setAttribute("readonly", "");
      ta.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;";
      document.body.appendChild(ta); ta.select();
      var ok = !!(document.execCommand && document.execCommand("copy"));
      document.body.removeChild(ta);
      return ok;
    } catch (e) { return false; }
  }
  function copyText(text) {
    return new Promise(function (resolve) {
      var settled = false;
      function done(ok) { if (!settled) { settled = true; resolve(ok); } }
      setTimeout(function () { done(false); }, 1500);   // a host that never answers is a host that did not copy
      try {
        if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
          navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(legacyCopy(text)); });
          return;
        }
      } catch (e) {}
      done(legacyCopy(text));
    });
  }
  function dataOfResult(res) {
    var r = res && res.result; if (!r) return null;
    if (r.structuredContent && typeof r.structuredContent === "object") return r.structuredContent;
    try { return JSON.parse(r.content[0].text); } catch (e) { return null; }
  }
  // Idle: the reply as it will appear, the one button for this platform, and what the person does.
  function manualApproval(d, i) {
    var e = items[i], url = e ? originOf(e) : null;
    var viaX = d.platforma === "X" && !!web(d.intent_url);
    var note = S("man.note") + (d.intent_uwaga ? ' <span data-expired="1">' + esc(T("man.xLong")) + '</span>' : "");
    return preview(d, !(e && e.opp)) + '<div class="acts"><span class="lead">' +
      '<button class="primary" data-man="go" data-i="' + i + '">' + esc(T(viaX ? "man.postX" : "man.copyOpen")) + '</button>' +
      '<button class="soft" data-fix="' + i + '">' + esc(T("ap.edit")) + '</button></span><span class="hint">' + note +
      '</span><span class="end">' + originBtn(i, url) + '</span></div>';
  }
  function mbtn(name, i, label, cls) {
    return '<button class="' + cls + '" data-man="' + name + '" data-i="' + i + '">' + esc(label) + '</button>';
  }
  function manualPanel(d, m) {
    var i = m.i;
    switch (m.phase) {
      case "opened": {
        var x = m.kind === "x";
        var head = x ? T("man.xOpened") : m.copied === false ? T("man.copyFail") : T(m.opened ? "man.copied" : "man.copiedNoLink");
        var fallback = !x && m.copied === false
          ? '<textarea class="copybox" readonly rows="3" data-copybox="1" aria-label="' + esc(T("pv.labelReply")) + '">' + esc(d.tekst) + '</textarea>' : '';
        var tick = x || m.copied ? '<span class="ok">' + ic("check") + '</span>' : '';
        return '<div class="send" role="status" tabindex="-1" data-p="opened"><div class="msg">' + tick + esc(head) + '</div>' + fallback +
          '<div class="posted"><div class="plr"><label class="plab" for="ml-' + esc(d.revision_id) + '">' + esc(T("man.posted")) + '</label>' +
          '<span class="pact">' + mbtn("again", i, T("man.again"), "quiet") + mbtn("no", i, T("man.notYet"), "quiet") + '</span></div><div class="prow">' +
          '<input class="link" id="ml-' + esc(d.revision_id) + '" type="url" data-man-link="1" inputmode="url" autocomplete="off" placeholder="https://" value="' + esc(m.link || "") + '">' + mbtn("done", i, T("man.done"), "primary") + '</div>' +
          (m.bad ? '<div class="badlink">' + esc(T("man.badLink")) + '</div>' : '') + '</div></div>';
      }
      case "marking": return box('<span class="wait">' + esc(T("man.saving")) + '</span>', "marking");
      case "done":
        return box('<span class="ok">' + ic("check") + '</span><b>' + esc(T("man.saved")) + '</b><small>' + esc(T("man.savedNote")) + '</small>', "done",
          web(m.url) ? mbtn("view", i, T("man.viewPost"), "soft") : "");
      case "failed":
        return box('<b>' + esc(T("man.failed")) + '</b> ' + esc(T("man.failedHint")), "failed",
          mbtn("retry", i, T("man.retry"), "soft") + mbtn("ask", i, T("man.askAgent"), "primary"));
      case "asked": return box(esc(T("man.asked")), "asked");
      default: return "";
    }
  }
  function paintManual(d) {
    var m = mOf(d), el = document.querySelector('[data-box="' + d.revision_id + '"]');
    if (!el) return;
    var had = !!(document.activeElement && el.contains(document.activeElement));
    // Like the send states: while a box is open the region keeps the height the idle one had.
    if (m.phase === "idle") el.style.minHeight = "";
    else if (!el.style.minHeight) {
      var wide = el.querySelector(".ptw[data-open]"); if (wide) fold(wide);
      el.style.minHeight = el.offsetHeight + "px";
    }
    el.setAttribute("data-phase", m.phase);
    el.innerHTML = regionInner(d, m.i);
    var cb = el.querySelector("[data-copybox]");
    var inp = el.querySelector("input[data-man-link]");
    var sb = el.querySelector(".send");
    // The fallback box is selected so Cmd+C is all that is left; otherwise focus goes to the box,
    // never to a button, so a key held down cannot press "Done".
    try { if (cb) { cb.focus({ preventScroll: true }); cb.select(); } else if (m.bad && inp) inp.focus({ preventScroll: true }); else if (had && sb) sb.focus({ preventScroll: true }); } catch (e) {}
    fit(); resize();
  }
  // Copy (Reddit) and open the place to post: both start inside the person's click.
  function manualStart(d, m) {
    var viaX = d.platforma === "X" && !!web(d.intent_url);
    var target = viaX ? d.intent_url : d.watek_url;
    m.kind = viaX ? "x" : "reddit"; m.opened = !!web(target); m.bad = false;
    if (viaX) { m.copied = null; openLink(target); m.phase = "opened"; paintManual(d); return; }
    var copying = copyText(d.tekst);
    openLink(target);
    copying.then(function (ok) { m.copied = ok; m.phase = "opened"; paintManual(d); });
  }
  function manualAsk(d, m) {
    var link = m.link ? P("man.askLink", { url: m.link }) : "";
    ask(P("man.askMsg", { rev: d.revision_id, link: link }));
    m.phase = "asked"; paintManual(d);
  }
  // "Done": what the person says, recorded. The server stores it as THEIR statement, unverified.
  function manualDone(d, m) {
    var el = document.querySelector('[data-box="' + d.revision_id + '"]');
    var inp = el && el.querySelector("input[data-man-link]");
    var link = inp ? String(inp.value || "").trim() : (m.link || "");
    m.link = link;
    if (link && !ownPostLink(link)) { m.bad = true; paintManual(d); return; }
    m.bad = false;
    // A host that said it cannot carry a tool call: the agent is asked in the person's name instead.
    if (!canSend) { manualAsk(d, m); return; }
    m.phase = "marking"; paintManual(d);
    var args = { revision_id: d.revision_id }; if (link) args.url = link;
    callTool("draft.mark_posted", args).then(function (res) {
      if (m.phase !== "marking") return;
      var data = dataOfResult(res);
      if (res && res.result && !res.result.isError && data && data.stan === "WYKONANE") {
        m.phase = "done"; m.url = (data.zapisano && data.zapisano.adres_posta) || link || null;
      } else m.phase = "failed";
      paintManual(d);
    });
  }
  function manualAction(name, i) {
    var e = items[i], d = e && e.draft; if (!d) return;
    var m = mOf(d); m.i = i;
    if (name === "go" && m.phase === "idle") manualStart(d, m);
    else if (name === "again" && m.phase === "opened") manualStart(d, m);
    else if (name === "done" && m.phase === "opened") manualDone(d, m);
    else if (name === "no" && m.phase === "opened") { m.phase = "idle"; paintManual(d); }
    else if (name === "retry" && m.phase === "failed") { m.phase = "opened"; paintManual(d); }
    else if (name === "ask" && m.phase === "failed") manualAsk(d, m);
    else if (name === "view" && m.phase === "done") openLink(m.url);
  }

  // ---- The pages: ONE person at a time (v7). Every entry has a page, all in the DOM and stacked in one
  // grid cell; exactly one is active. A page that is not active is hidden, inert and out of the
  // reading order, so a click or a key can only ever reach the page the person is looking at.
  var cur = 0;          // the active page
  var curKey = null;    // which entry it is, to stay on it when the card is drawn again
  var total = 0;
  var expanded = {};    // revision_id -> the reply is open ("Show all")

  function entryKey(e) { return e.opp ? "o" + e.opp.id : "d" + e.draft.revision_id; }
  // The thread an entry is about, for "Original" and for what the card asks the agent.
  function originOf(e) {
    var o = e.opp, d = e.draft;
    return o ? o.url : (d && d.w_odpowiedzi_na ? d.w_odpowiedzi_na.url : null);
  }

  // The words of a post: its title, and what it says under it. An X post is often only its text, and
  // some posts repeat the title as their first line: it is said once.
  function postWords(o) {
    var t = String(o.tytul || "").replace(/\s+/g, " ").trim(), b = String(o.fragment || "").replace(/\s+/g, " ").trim();
    if (b && t && b === t) b = "";
    if (!t && b) { t = b; b = ""; }
    return { title: t, body: b };
  }
  function postBlock(o) {
    var p = postWords(o);
    return (p.title ? '<h2 class="title' + (p.body ? '' : ' solo') + '">' + esc(p.title) + '</h2>' : '') +
      (p.body ? '<p class="excerpt">' + esc(p.body) + '</p>' : '');
  }

  // Where and when, on one line; on its right the one disclosure ("Why?") and, for a post that went out, its state.
  function metaOf(o, open) {
    var reddit = o.platforma === "REDDIT";
    var where = o.spolecznosc ? (reddit ? "r/" + String(o.spolecznosc).replace(/^r\//, "") : o.spolecznosc) : (reddit ? "Reddit" : "X");
    var author = whoOf(o.platforma, o.autor);
    var when = ago(o.utworzono);
    var state = o.stan === "ODPOWIEDZIANO"
      ? '<span class="statetxt" data-s="' + esc(o.stan) + '">' + esc(L("state." + o.stan, o.stan)) + '</span>' : '';
    var more = (o.dlaczego || o.kat)
      ? '<button class="whyt" data-why="1" aria-expanded="' + (open ? "true" : "false") + '">' + esc(T("why.ask")) + ic("chevD") + '</button>' : '';
    return '<div class="meta"><div class="cmeta"><span class="badge">' + (reddit ? "R" : "X") + '</span>' +
      '<span class="where">' + esc(where) + '</span>' + (when ? '<span class="when">' + esc(when) + '</span>' : '') +
      (author ? '<span class="author">' + esc(author) + '</span>' : '') + '</div>' + state + more + '</div>';
  }

  // Why this person and the angle: closed, they take no room.
  function whyWell(o) {
    if (!(o.dlaczego || o.kat)) return "";
    return '<div class="why"><div><dl>' +
      (o.dlaczego ? '<div><dt>' + esc(T("why.person")) + '</dt><dd>' + esc(o.dlaczego) + '</dd></div>' : '') +
      (o.kat ? '<div><dt>' + esc(T("why.angle")) + '</dt><dd>' + esc(o.kat) + '</dd></div>' : '') + '</dl></div></div>';
  }

  // What came of a published reply. A gap in the measurement is a gap, never a zero.
  function published(o, r, i) {
    var seen = r ? L("seen." + r.widocznosc, T("seen.unconfirmed")) : null;
    var when = r ? ago(r.opublikowano) : null;
    var body = r
      ? '<dl class="res"><div><dt>' + esc(T("pub.votes")) + '</dt><dd class="n">' + figure(r.glosy) + '</dd></div>' +
        '<div><dt>' + esc(T("pub.replies")) + '</dt><dd class="n">' + figure(r.odpowiedzi) + '</dd></div>' +
        '<div><dt>' + esc(T("pub.visibility")) + '</dt><dd>' +
        (r.widocznosc === "REMOVED" || r.widocznosc === "CONFIRMED" ? '<b>' + esc(seen) + '</b>' : '<i>' + esc(seen) + '</i>') +
        '</dd></div></dl>' + (r.tekst ? '<div class="quote">' + esc(r.tekst) + '</div>' : '') +
        (when ? '<div class="note">' + S("pub.when", { when: when }) + '</div>' : '')
      : '<div class="note">' + esc(T("pub.older")) + '</div>';
    var link = r && web(r.url) ? '<button class="primary" data-result="' + i + '">' + esc(T("sp.view")) + '</button>' : '';
    return '<div class="region">' + body + '<div class="acts"><span class="lead">' + link + '</span><span class="end">' +
      originBtn(i, o.url) + '</span></div></div>';
  }

  // A person nobody has answered yet has room on the page (there is no reply to draw): where the card is
  // wide enough for the two reasons side by side, they start open.
  function opensWhy(kind) { return kind === "new" && window.innerWidth >= 600; }
  function pageOf(inner, i, kind) {
    return '<article class="page" data-panel="1" data-i="' + i + '" data-kind="' + kind + '"' + (opensWhy(kind) ? ' data-why="1"' : '') +
      ' role="group" aria-roledescription="slide" aria-label="' +
      esc(P("nav.of", { i: i + 1, n: total })) + '">' + inner + '</article>';
  }

  function oppPage(o, i, d, r) {
    var kind = o.stan === "ODPOWIEDZIANO" ? "posted" : o.stan === "SZKIC" ? (d ? "draft" : "gone") : "new", tail;
    var inner = metaOf(o, opensWhy(kind)) + postBlock(o) + whyWell(o);
    if (kind === "posted") {
      tail = published(o, r, i);
    } else if (kind === "draft") {
      tail = region(d, i);
    } else if (kind === "gone") {
      tail = '<div class="region"><div class="note">' + esc(T(manual ? "oc.draftGoneManual" : "oc.draftGone")) + '</div><div class="acts"><span class="lead">' +
        (web(room) ? '<button class="soft" data-room="1">' + esc(T("oc.openRoom")) + '</button>' : '') +
        '</span><span class="end">' + originBtn(i, o.url) + '</span></div></div>';
    } else {
      tail = '<div class="region"><div class="metrics"><span>' + esc(T("oc.score")) + ' ' + figure(o.wynik) + '</span><span>' +
        esc(T("oc.comments")) + ' ' + figure(o.komentarze) + '</span></div><div class="acts"><span class="lead"><button class="primary" data-reply="' + i +
        '">' + esc(T("oc.write")) + '</button></span><span class="end">' + originBtn(i, o.url) + '</span></div></div>';
    }
    return pageOf(inner + tail, i, kind);
  }

  // A draft that answers nobody on the list: an own post, or a reply under a thread nobody saved.
  function draftPage(d, i) {
    var x = d.platforma === "X";
    return pageOf('<div class="meta"><div class="cmeta"><span class="badge">' + (x ? "X" : "R") + '</span>' +
      '<span class="where">' + S(d.rodzaj === "OWN_POST" ? "dc.postOn" : "dc.replyOn", { p: x ? "X" : "Reddit" }) + '</span></div>' +
      '<span class="statetxt" data-s="SZKIC">' + esc(T(manual ? "state.ready" : "state.waiting")) + '</span></div>' + region(d, i), i, "orphan");
  }

  // Dots and "1 of 4" on the left, the two chevrons on the right. With one person there is nothing to page.
  function pager() {
    if (total < 2) return "";
    var dots = "";
    if (total <= 9) {
      for (var k = 0; k < total; k++) {
        dots += '<button class="dot" data-go="' + k + '" aria-label="' + esc(P("nav.go", { i: k + 1, n: total })) + '"></button>';
      }
    }
    return '<nav class="pager" aria-label="' + esc(T("nav.label")) + '">' +
      '<button class="nav" data-nav="-1" aria-label="' + esc(T("nav.prev")) + '">' + ic("chevL") + '</button>' +
      '<div class="dots">' + dots + '</div>' +
      '<button class="nav" data-nav="1" aria-label="' + esc(T("nav.next")) + '">' + ic("chevR") + '</button>' +
      '<span class="count" aria-live="polite"></span></nav>';
  }

  function fold(w) {
    w.removeAttribute("data-open");
    var m = w.querySelector(".more"); if (m) m.textContent = T("pv.more");
    var box = w.closest("[data-box]"); if (box) delete expanded[box.getAttribute("data-box")];
  }
  // Show the page `cur`, hide the others, and tell the pager. A page the person leaves is folded
  // again (its "Why?", its open reply), so the card is as tall as it was.
  function mark() {
    var pages = document.querySelectorAll("[data-panel]"), k;
    for (k = 0; k < pages.length; k++) {
      var p = pages[k];
      if (k === cur) {
        p.setAttribute("data-active", "1"); p.removeAttribute("aria-hidden"); p.removeAttribute("inert");
        continue;
      }
      p.removeAttribute("data-active"); p.setAttribute("aria-hidden", "true"); p.setAttribute("inert", "");
      var starts = opensWhy(p.getAttribute("data-kind"));
      if (starts) p.setAttribute("data-why", "1"); else p.removeAttribute("data-why");
      var wt = p.querySelector(".whyt"); if (wt) wt.setAttribute("aria-expanded", starts ? "true" : "false");
      var open = p.querySelectorAll(".ptw[data-open]");
      for (var m = 0; m < open.length; m++) fold(open[m]);
    }
    var dots = document.querySelectorAll(".dot");
    for (k = 0; k < dots.length; k++) {
      if (k === cur) dots[k].setAttribute("aria-current", "true"); else dots[k].removeAttribute("aria-current");
    }
    var count = document.querySelector(".count"); if (count) count.textContent = P("nav.of", { i: cur + 1, n: total });
    var prev = document.querySelector('[data-nav="-1"]'), next = document.querySelector('[data-nav="1"]');
    if (prev) prev.disabled = cur <= 0;
    if (next) next.disabled = cur >= total - 1;
    if (items[cur]) curKey = entryKey(items[cur]);
  }
  // Move to page `i`. False when there is no such page (or it is already the one shown).
  function show(i) {
    if (!(i >= 0 && i < total) || i === cur) return false;
    cur = i; mark(); resize();
    return true;
  }

  function render(data) {
    var app = document.getElementById("app");
    drawn = true; lastData = data;
    setLang(data && data.jezyk);
    if (data && data.stan && data.stan !== "WYKONANE") {
      items = []; total = 0;
      app.innerHTML = top(true) + '<h1>' + T("rd.errH") + '</h1>' +
        '<div class="empty">' + esc(data.powod || T("rd.errDefault")) + '</div>';
      resize(); return;
    }
    manual = !!(data && data.wysylka === "reczna");
    var list = (data && data.okazje) || [];
    var drafts = (data && data.szkice) || [];
    var results = (data && data.wyniki && data.wyniki.pozycje) || [];
    room = (data && (data.pokoj_adres)) || null;
    if (!list.length && !drafts.length) {
      items = []; total = 0;
      app.innerHTML = top(true) + '<h1>' + T("rd.noneH") + '</h1>' +
        '<div class="empty">' + esc(T("rd.noneBody")) + '</div>';
      resize(); return;
    }
    var open = list.filter(function (o) { return o.stan === "NOWA" || o.stan === "SZKIC"; });
    var ages = open.map(function (o) { return minutesSince(o.utworzono); }).filter(function (m) { return m !== null; });
    var freshest = ages.length ? Math.min.apply(null, ages) : null;
    var n = open.length;
    var headline = n
      ? PL(n, freshest !== null && freshest <= 48 * 60 ? "rd.headNow" : "rd.headRecent", { n: n })
      : T("rd.allDone");
    var newest = open.slice().sort(function (a, b) { return (minutesSince(a.utworzono) || 0) - (minutesSince(b.utworzono) || 0); })[0];

    // The draft of an opportunity is the one it points at (`szkic`); a result is the one that says it answered it.
    var byRevision = {}; drafts.forEach(function (d) { byRevision[d.revision_id] = d; });
    var byOpportunity = {}; list.forEach(function (o) { byOpportunity[o.id] = true; });
    var resultOf = {}; results.forEach(function (r) { if (r.okazja_id && !resultOf[r.okazja_id]) resultOf[r.okazja_id] = r; });
    var entries = [];
    list.forEach(function (o) {
      var d = o.stan === "SZKIC" && o.szkic ? byRevision[o.szkic] || null : null;
      entries.push({ rank: o.stan === "ODPOWIEDZIANO" ? 3 : d ? 0 : 2, opp: o, draft: d, result: resultOf[o.id] || null });
    });
    drafts.forEach(function (d) {
      if (d.okazja_id && byOpportunity[d.okazja_id]) return;
      entries.push({ rank: 1, draft: d });
    });
    var order = entries.map(function (e, k) { return [e, k]; }).sort(function (a, b) { return a[0].rank - b[0].rank || a[1] - b[1]; })
      .map(function (p) { return p[0]; });
    items = order; total = order.length;
    // The same person stays on screen when the card is drawn again (another language, a new reading).
    cur = 0;
    if (curKey) order.forEach(function (e, k) { if (entryKey(e) === curKey) cur = k; });
    var pages = order.map(function (e, i) {
      return e.opp ? oppPage(e.opp, i, e.draft, e.result) : draftPage(e.draft, i);
    }).join("");
    var waiting = drafts.filter(function (d) { var ex = expiry(d.wygasa); return !(ex && ex.expired); }).length;
    var bits = [];
    if (newest && ago(newest.utworzono)) bits.push('<span>' + S("rd.newest", { ago: ago(newest.utworzono) }) + '</span>');
    if (waiting) bits.push('<span>' + PL(waiting, manual ? "rd.waitingManual" : "rd.waiting", { n: waiting }) + '</span>');
    app.innerHTML = top(true) + '<h1>' + headline + '</h1>' + (bits.length ? '<div class="sum">' + bits.join(" ") + '</div>' : '') +
      '<section class="stage" role="group" aria-roledescription="carousel" aria-label="' + esc(T("nav.label")) + '">' + pages + '</section>' +
      '<div class="bar">' + pager() + '<div class="foot"><span>' + esc(T(manual ? "rd.footManual" : "rd.foot")) + '</span>' +
      (web(room) ? '<button class="quiet" data-room="1">' + esc(T("oc.openRoom")) + '</button>' : '') + '</div></div>';
    mark(); fit(); resize();
  }

  // The bar on top. While loading it has no clock and no words, so it needs no language yet.
  function top(read) {
    return '<div class="top"><img alt="" src="' + MARK + '"><span class="brand">Wenext</span>' +
      (read ? '<span class="asof">' + S("rd.asOf", { time: clock() }) + '</span>' : '') + '</div>';
  }

  // A long reply is cut to a few lines with a way to read the rest; a short one needs no button.
  function fit() {
    var all = document.querySelectorAll(".ptw");
    for (var k = 0; k < all.length; k++) {
      var w = all[k];
      if (w.hasAttribute("data-open")) continue;
      var t = w.querySelector(".pt");
      if (t && t.scrollHeight > t.clientHeight + 1) w.setAttribute("data-over", "1"); else w.removeAttribute("data-over");
    }
  }

  // THE HEIGHT OF THE CARD, told to the host: what the content needs (the card itself, not the
  // document, which is never shorter than the frame the host gave it), so the host sizes the frame to
  // the content and there is nothing to scroll.
  function resize() {
    var app = document.getElementById("app");
    var box = app.getBoundingClientRect();
    var h = Math.ceil(box.height), w = Math.ceil(box.width);
    if (!h) return;
    notify("ui/notifications/size-changed", { width: w, height: h });
    try { if (window.openai && window.openai.notifyIntrinsicHeight) window.openai.notifyIntrinsicHeight(h); } catch (e) {}
  }
  // Whatever changes the card's size (a width, a font, a fold, a transition) is told, once per frame.
  var settling = 0;
  function settle() {
    if (settling) return;
    var frame = typeof requestAnimationFrame === "function" ? requestAnimationFrame : function (fn) { return setTimeout(fn, 16); };
    settling = frame(function () { settling = 0; fit(); resize(); });
  }

  function ask(text) {
    if (window.openai && window.openai.sendFollowUpMessage) {
      window.openai.sendFollowUpMessage({ prompt: text }); return;
    }
    request("ui/message", { role: "user", content: [{ type: "text", text: text }] });
  }
  function openLink(url) {
    if (!web(url)) return;
    if (window.openai && window.openai.openExternal) { window.openai.openExternal({ href: url }); return; }
    request("ui/open-link", { url: url }).then(function (r) { if (r === null) window.open(url, "_blank", "noopener"); });
  }

  document.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest("button"); if (!b) return;
    // Only the page the person is looking at acts: a button of a hidden page is not a click of theirs.
    var pg = b.closest("[data-panel]");
    if (pg && !pg.hasAttribute("data-active")) return;
    if (b.hasAttribute("data-room")) { openLink(room); return; }
    if (b.hasAttribute("data-nav")) { show(cur + Number(b.getAttribute("data-nav"))); return; }
    if (b.hasAttribute("data-go")) { show(Number(b.getAttribute("data-go"))); return; }
    if (b.hasAttribute("data-why")) {
      var shown = pg && pg.hasAttribute("data-why");
      if (pg) { if (shown) pg.removeAttribute("data-why"); else pg.setAttribute("data-why", "1"); }
      b.setAttribute("aria-expanded", shown ? "false" : "true");
      resize(); return;
    }
    // Only a click in this frame gets here: nothing the model writes is a DOM event of the card.
    if (b.hasAttribute("data-act")) { sendAction(b.getAttribute("data-act"), Number(b.getAttribute("data-i"))); return; }
    if (b.hasAttribute("data-man")) { manualAction(b.getAttribute("data-man"), Number(b.getAttribute("data-i"))); return; }
    if (b.hasAttribute("data-more")) {
      var w = b.closest(".ptw"), holder = b.closest("[data-box]");
      if (!w) return;
      if (w.hasAttribute("data-open")) fold(w);
      else {
        w.setAttribute("data-open", "1"); b.textContent = T("pv.less");
        if (holder) expanded[holder.getAttribute("data-box")] = true;
      }
      resize(); return;
    }
    var e = items[Number(b.getAttribute("data-reply") || b.getAttribute("data-open") || b.getAttribute("data-approve") ||
      b.getAttribute("data-fix") || b.getAttribute("data-result"))]; if (!e) return;
    var o = e.opp || null, d = e.draft || null;
    var url = originOf(e);
    var who = (o && o.autor) || (d && d.w_odpowiedzi_na && d.w_odpowiedzi_na.autor) || T("ask.who");
    if (b.hasAttribute("data-open")) { openLink(url); return; }
    if (b.hasAttribute("data-result")) { openLink(e.result && e.result.url); return; }
    // The approval is the founder's click on the Pokój page, at the draft: the card only takes them there.
    if (b.hasAttribute("data-approve")) { if (d && room) openLink(room.replace(/#.*$/, "") + "#szkic-" + d.revision_id); return; }
    // What the card asks the agent is the person's own message in their thread, so it follows the card's language.
    if (b.hasAttribute("data-fix")) {
      var own = d && d.rodzaj === "OWN_POST", tail = P(manual ? "ask.fixTailManual" : "ask.fixTail");
      ask(own
        ? P("ask.fixOwn", { where: d.spolecznosc && d.platforma !== "X" ? P("ask.inCommunity", { c: d.spolecznosc }) : "", tail: tail })
        : P("ask.fixReply", { who: who, thread: url ? P("ask.inThread", { url: url }) : "", tail: tail }));
      return;
    }
    ask(P("ask.reply", { who: who, url: url }));
  });

  // The arrow keys page, whatever has the focus in the card (a key never sends: only a click does).
  document.addEventListener("keydown", function (ev) {
    if (ev.defaultPrevented || ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) return;
    // Typing a link (or moving in a selected reply) is not paging.
    if (ev.target && /^(INPUT|TEXTAREA)$/.test(String(ev.target.tagName))) return;
    if (ev.key === "ArrowRight") { if (show(cur + 1)) ev.preventDefault(); }
    else if (ev.key === "ArrowLeft") { if (show(cur - 1)) ev.preventDefault(); }
  });

  window.addEventListener("message", function (ev) {
    var m = ev.data; if (!m || m.jsonrpc !== "2.0") return;
    if (m.id != null && pending[m.id]) { var r = pending[m.id]; delete pending[m.id]; r(m); return; }
    if (m.method === "ui/notifications/tool-result") {
      var p = m.params || {};
      if (p.structuredContent) render(p.structuredContent);
    } else if (m.method === "ui/notifications/host-context-changed") {
      var c = m.params || {};
      if (typeof c.locale === "string" && c.locale && c.locale !== hostLocale) {
        hostLocale = c.locale; if (drawn && lastData) render(lastData);
      }
    }
  });

  // ChatGPT and older Codex builds put the result on window.openai instead of sending it.
  function fromOpenai() {
    try {
      if (window.openai && window.openai.toolOutput) {
        via = "openai"; canSend = typeof window.openai.callTool === "function";
        if (typeof window.openai.locale === "string" && window.openai.locale) hostLocale = window.openai.locale;
        render(window.openai.toolOutput); return true;
      }
    } catch (e) {}
    return false;
  }
  window.addEventListener("openai:set_globals", fromOpenai);

  // Nothing to say before the data: a bar and a pulse. The words come with the language.
  document.getElementById("app").innerHTML = top(false) + '<div class="empty load"><span class="wait"></span></div>';
  if (!fromOpenai()) {
    request("ui/initialize", {
      protocolVersion: "2026-01-26",
      appInfo: { name: "wenext-pokoj", version: "8" },
      appCapabilities: {}
    }).then(function (r) {
      notify("ui/notifications/initialized", {});
      // A host that answers with capabilities and no `serverTools` cannot carry a tool call, so
      // there is no send button (the Pokój link stays). Silence is not "no": the host is tried.
      var caps = r && r.hostCapabilities, again = false;
      if (caps && typeof caps === "object" && !caps.serverTools) { canSend = false; again = true; }
      // The host says its locale here (`hostContext.locale`), before the result arrives.
      var ctx = r && r.hostContext;
      if (ctx && typeof ctx.locale === "string" && ctx.locale && ctx.locale !== hostLocale) { hostLocale = ctx.locale; again = true; }
      if (again && drawn && lastData) render(lastData);
    });
  }
  window.addEventListener("resize", settle);
  if (typeof ResizeObserver === "function") { try { new ResizeObserver(settle).observe(document.getElementById("app")); } catch (e) {} }
  // A host that never delivers the result (a failed call carries none) must not leave the pulse forever.
  setTimeout(function () {
    if (drawn) return;
    setLang(null);
    document.getElementById("app").innerHTML = top(true) + '<h1>' + T("rd.noDataH") + '</h1>' +
      '<div class="empty">' + esc(T("rd.noDataBody")) + '</div>';
    resize();
  }, 10000);
})();
</script>
</body>
</html>
""".replace("__MARK__", _MARK_PNG_BASE64).replace("__STRINGS__", _strings_json())
