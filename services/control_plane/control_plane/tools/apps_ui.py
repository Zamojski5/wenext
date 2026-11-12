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
adds nothing of its own to that data: it draws it. (It asks the server for one read of its own, `room.show`
again, and only after a repeated inline save, which the server answers with "already done" and no draft;
a view-initiated call to a tool that carries a card is unverified against a live Codex.)

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
chevrons, the arrow keys) and one footer line. THE RESULTS OF THE LAST DAYS ARE NOT A LINE OF THE SUMMARY (the
founder's decision, 11.10: v7 first carried them there): the numbers of a reply that went out are on
its own page, and since v9 also in the collapsed "Sent (N)" disclosure under the pager. The
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

NO DASHBOARD IN THE DEFAULT FLOW (v9, 11.10, the founder: "the user should not need our web dashboard
at all"). Everything a person did on the Pokój page they now do here, in the card or in the chat, and
each of the new things is one more thing the card can do ONLY BEHIND A CLICK of the person's:

  * SKIPPED (N): a quiet disclosure under the pager lists the conversations the agent looked at and
    decided not to answer (`pominiete` in `room.show`: platform, community, title, link and its one
    line reason, `powod`; one a person skipped on the dashboard says so instead of giving a reason).
    The founder said it publicly ("it lists the threads it skipped and why"), so the card keeps that
    promise from the database and never from the agent's say-so.
  * EDIT INLINE: "Edit" turns the reply into a textarea with Save and Cancel. Save calls the app-only
    `room.revise_draft` through the host (`tools/card_actions.py`): a NEW revision of the same draft,
    never a publication, and the card repaints with the new digest, so a send token minted for the old
    text is dead and the next "Send" is about the new text. "Ask the agent to rewrite" stays, as the
    secondary path (and the only one where the host cannot carry a tool call, or in the hosted card).
  * SENT (N): a disclosure with the replies that went out: a link to the live post, its visibility
    and the latest votes and replies of `wyniki.pozycje`. A number nobody measured is "not measured",
    never 0 (`figure`).
  * LIMITS AND PAUSE (local install only, `zakres` in `room.show`): the footer says the scope in one
    line ("2 accounts · 4/h · 20/day · until Oct 25") and carries "Pause Wenext". Pause, Resume and
    "Limits" (the panel that SIGNS a new REVIEW scope: accounts, actions, and FOR EACH ACCOUNT its own
    per hour, per day and own posts a day, a 7 or 14 day term; v11, 13.10: a pace per account, replies and
    own posts counted apart, X no longer waits behind Reddit) are three app-only tools behind the same one-time token as the send
    (`room.scope_token` asks, `room.pause`, `room.resume` and `room.sign_scope` redeem it; the card
    never sends accounts or limits to the redeeming call, the server signs exactly what the person
    was shown). When a send is refused for a reason a scope change fixes (the hour or day budget, an
    account outside an autonomous scope, a paused scope), the refusal box offers that action inline
    ("Raise limits", "Resume Wenext") and opens the same panel prefilled. The machine is `scoper`
    between the SCOPE markers; `room.pause`, `room.resume` and `room.sign_scope` are each named in one
    place, reached from the "Yes" button of the confirmation and from nothing else.
  * LINKS: "Approve in Room" and "Open Room" are gone from the default flow. What stays is one small
    "Open full view" link in the footer, for a local install that knows the dashboard, never hosted.

MOTION (v9, the founder: "much, much better"): own CSS and a few lines of script, no library, in the
studio palette. A press scales a button to 0.97 and lets it back with a small overshoot; a label that
changes ("Send", "Checking the draft", "Yes, send", "Sending to the publisher", "Posted") crossfades with a
short blur while the width settles (`morph`); the primary button shines once across when it first becomes
actionable (`shine`); a thin beam runs once around the panel when a confirmation or a result appears
(`beam`, about 1.5 s); the check of "Posted" draws itself. THE FIRST DRAWING OF A SESSION opens with a
reveal under about a second: "Searching threads" becomes "Read N threads on r/SaaS, r/SideProject, X" with
a stack of round icons while small tiles (the people found, and "+N skipped") fill in one by one from a
dim skeleton, then the headline and the panel fade in. It is a layer over content that is already in the
page and already holds its height, so nothing moves; any click or key ends it; once per session. Under
`prefers-reduced-motion` there is no scale, no shine, no beam, no reveal: plain fades only.

ACCOUNTS FROM THE CARD (v10, 12.10, the founder: "I hate the dashboard. I don't know what to click, and it's
my own product. Everything should show in the chat"). His X session in the agent's browser was lost overnight
and the only fix was a conversation card on the dashboard. Now the footer of a local install shows each
assigned account with a dot (`konta_stan` in `room.show`): green, the browser is signed in as it; amber, the
session was lost; hollow, never connected; and where a click can improve it, "Reconnect @account" right there.
The press runs `tools/card_connect.py`: the card asks `room.connect_token` and acts with it,
`room.connect_account`, in the same handler (one click), the server opens the agent's browser on the platform's
login page and answers with the address of the window in `_meta` only, and the card opens it
(`ui/open-link`). The person signs in themselves; nothing is typed for them. The platform CANNOT be read while
they are at the window (the browser has no DevTools endpoint then, `api/x_platform.py`), so the card says so
and asks every 4 seconds for up to 3 minutes (`room.account_status`, which asks the capsule for its state and
makes no platform request while the window is open) in case it is closed by other means; the person's
"I'm signed in" calls `room.connect_done`, which closes the window, reads the session once, records it, and
the dot turns green. The machine is `makeConnector` between the CONNECT markers, pure and driven in node:
`room.connect_token` and `room.connect_account` are named in one place, reached from the press on
"Reconnect" and its repeats and from nothing else; `room.connect_done` only from "I'm signed in" and
"Cancel". The hosted card has no accounts of the person's and shows none of this.

SEND ALL (v11, 13.10, the founder: "I don't want to click twenty times a day"). The product keeps the human in
the loop (X removed 42 thousand accounts for AI replies with no human, and the public promise is "Nothing posts
without your click"), so the answer is ONE click per REVIEWED batch. The pager row carries "Send all (N)", N
being the drafts in view that could go (waiting for approval, not expired, a digest in hand, the account in the
live signed scope, the action in it, not being edited or sent). It opens a panel in the place of the pages (the
sheet, like Limits): every such draft with its platform, who it answers and the first line of the reply, a
tick for each (all ticked), the schedule the server will use ("1 now, then one every 15 min, done by 14:45")
and the limits, and "Yes, send these N". The machine is `makeBatcher` between the BATCH markers, pure and driven
in node: `room.batch_token` (the question, `tools/card_batch.py`) is asked for exactly the ticked set, and every
tick or untick asks it AGAIN (the schedule is the server's plan for that very set, and the earlier confirmation
dies); `room.send_batch` is named in ONE place, `go`, reached by the "Yes" button and by nothing else, and takes
the token and nothing else. A refusal that heals (a stale confirmation, a batch that changed) asks again once and
ends at the confirmation, which waits for a click. After the send the panel shows a compact "Scheduled" list
(time and live status) that follows each request with `room.send_status` (a request whose moment is near every
5 seconds, a far one every minute), the same list stays under the pager as a disclosure, and each scheduled
draft's page says so instead of offering Send. The publisher looks at every thread again at its moment
(`pilot/send_checks.py`): gone, locked, already answered or too old and the request is skipped with a reason the
list shows, a session that is down and it waits for the next try; the text never changes. A batch signs nothing:
a draft whose account the scope lacks is shown unticked with the reason.

THE POST, DRAWN AS THE PLATFORM DRAWS IT (v11, 13.10, the founder: "render it like real X or Reddit"; "1 of 8", not "3 of
47"). The server sends at most EIGHT pages, the best first (`opportunities/room.py`: freshness and growth, times how well
the account fits), and `ukryte`, how many more are behind (a note in the Skipped drawer). The card shows them in the
server's order: the drafts first, each with the conversation it answers. A reply page is ONE BLOCK that reads as a thread:
the post (author's photo, name, verified mark, @handle, age, the text once, the first picture, the counters with X's own
icons; for Reddit the alien, the community, the title and text, a pill of points and one of comments) with a thread line down
to OUR reply, which carries the founder's own picture. An own post on X that ENDS with the address of a post is a QUOTE: our
words (without the address) and the quoted post in a bordered box, labelled "Quote on X"; the editor keeps the whole text,
because the address is what makes it a quote. The pictures are never requested by the card: an `img` has no source until the
server answers `room.pictures` (an app-only tool, asked once for the addresses on screen) with small inline images; a host
that cannot call it leaves the initials. QUEUED and POSTED are one quiet line under the post, not a panel. The footer is one
quiet line ("Sent 9 · Skipped 18 · Limits" and a small menu with Pause and the full view); only what needs a person (an account
whose session is lost, a paused or expired scope) is spelled out, and the scope in a line and every account's session are in
the Limits panel. The card is as tall as the page on screen. "Nothing posts without your click" is said in the confirmation.

The URI is a cache key: a host may keep the HTML it read, so a breaking change to the card gets a
new version in the URI rather than an edit under the old one. v11 sends a reviewed batch with one click; v10 connects an account from the card; v9 takes
the dashboard out of the flow; v8 adds manual sending; v7 is one person at a time; v6 heals
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

from control_plane.opportunities.avatars import PICTURE_HOSTS

OPPORTUNITIES_URI = "ui://wenext/okazje-v11.html"
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
                        # EMPTY, but for the pictures of a post (13.10): the card gets them as inline images from
                        # `room.pictures` and never needs this; it is the fallback for a host that cannot call that tool,
                        # and it names only the platforms' own image hosts (`opportunities/avatars.py`).
                        "csp": {
                            "connectDomains": [],
                            "resourceDomains": [f"https://{host}" for host in PICTURE_HOSTS],
                        },
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
#: angle, and the "Why?" that opens them), nav (the pager), ac (accounts: health and Reconnect, v10), pub, oc, dc,
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
        "pv.labelPost": "Twój post, tak jak wyjdzie",
        "pv.labelReply": "Twoja odpowiedź, tak jak wyjdzie",
        "pv.more": "Pokaż całość",
        "pv.less": "Zwiń",
        "ap.goes": "Wyjdzie z konta {h}, po Twoim kliknięciu.",
        "ap.goesProject": "Wyjdzie z konta przypisanego do projektu, po Twoim kliknięciu.",
        "ap.inRoom": " Zatwierdź w pełnym widoku (link poniżej).",
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
        "c.small": "To kliknięcie podpisuje zakres: konta tego projektu, każdy tekst czeka na Twoje kliknięcie{until}{limits}. Zmienisz go w Limitach albo wstrzymasz w każdej chwili.",
        "c.smallWiden": "To kliknięcie podpisuje rozszerzony zakres: każdy tekst nadal czeka na Twoje kliknięcie{until}{limits}. Zmienisz go w Limitach albo wstrzymasz w każdej chwili.",
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
        "sp.queuedSlow": "Trwa dłużej niż zwykle. Sprawdź za chwilę.",
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
        "e.noAnswer": "Agent nie przekazał karcie odpowiedzi. Spróbuj jeszcze raz albo poproś agenta.",
        "e.noConnect": "Nie udało się połączyć z Wenext przez agenta. Spróbuj jeszcze raz albo poproś agenta.",
        "e.noToken": "Ten agent nie przekazał karcie potwierdzenia, więc wysyłka z karty nie jest możliwa. Użyj pełnego widoku albo poproś agenta.",
        "e.unsure": "Nie wiem, czy to poszło: agent nie odpowiedział. Poproś agenta o pokazanie karty jeszcze raz, zanim klikniesz ponownie.",
        "e.unknown": "Nie wiadomo, czy odpowiedź wyszła. Sprawdź na platformie, zanim wyślesz jeszcze raz.",
        "w.BLOCKED": "Publikator zablokował wniosek.",
        "w.SKIPPED": "Publikator pominął wniosek.",
        "w.STOPPED": "Publikator zatrzymał wniosek.",
        "ref.STAN": "Ta propozycja nie czeka na zgodę, więc nie da się jej zatwierdzić. Poproś agenta o pokazanie karty jeszcze raz.",
        "ref.OKNO": "Okno zgody tej propozycji minęło. Poproś agenta o nową wersję.",
        "ref.ODCISK": "Ta wersja szkicu nie jest już tą, którą widziałeś: ktoś ją w międzyczasie zmienił. Poproś agenta o pokazanie karty jeszcze raz i zdecyduj o najnowszej.",
        "ref.ZAKRES": "Projekt nie ma podpisanego zakresu, w którym ta publikacja miałaby się odbyć. Ustaw limity poniżej i wyślij ponownie.",
        "ref.BUDZET": "Limit wysyłki jest na razie wyczerpany. Podnieś go poniżej albo poczekaj.",
        "ref.ZGODA": "Zgoda nie przeszła. Spróbuj jeszcze raz.",
        "ref.NIEAKTUALNY": "Ta wersja szkicu nie czeka już na zgodę: ktoś ją w międzyczasie zatwierdził albo poprawił. Poproś agenta o pokazanie karty jeszcze raz i zdecyduj o najnowszej.",
        "ref.ZAKRES_WSTRZYMANY": "Zakres tego projektu jest wstrzymany. Wznów go przyciskiem poniżej: wstrzymanie to Twój stop i samo wysłanie go nie zdejmuje.",
        "ref.ZAKRES_INNY_TRYB": "Zakres tego projektu działa w innym trybie i nie obejmuje tego konta. Zmień zakres poniżej: przy wysyłce dodaję konta tylko do zakresu, w którym każdy tekst czeka na Twoje kliknięcie.",
        "ref.ZAKRES_KOLEJKA": "Poprzednia odpowiedź jeszcze czeka w kolejce publikatora, a nowy podpis zatrzymałby ją. Poczekaj, aż wyjdzie, i spróbuj ponownie.",
        "ref.NIE_ZNALEZIONO": "Nie ma takiej propozycji ani takiego wniosku w tym projekcie.",
        "ref.POTWIERDZENIE": "Brak ważnego potwierdzenia kliknięciem: nieznane, zużyte albo wygasłe. Spróbuj ponownie.",
        "ref.ZAKRES_ZMIENIONY": "Zakres zmienił się od chwili, gdy pokazano potwierdzenie. Spróbuj ponownie.",
        "ref.PODPIS": "Nie udało się podpisać zakresu. Spróbuj jeszcze raz.",
        "ref.DZIALANIE": "Tego działania nie wyślę z karty. Poproś o nie agenta.",
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
        "pub.older": "Wyników starszych niż 7 dni nie pokazuję.",
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
        "rd.foot": "Nic nie wyjdzie bez Twojego kliknięcia.",
        "rd.asOf": "Odczyt o {time}",
        "rd.noDataH": "Nie mam <strong>danych Pokoju</strong>.",
        "rd.noDataBody": "Poproś agenta, żeby pokazał kartę jeszcze raz.",
        "ask.who": "tej osoby",
        "ask.inThread": " w wątku {url}",
        "ask.inCommunity": " w {c}",
        "ask.fixOwn": "Popraw szkic mojego posta{where}. {tail}",
        "ask.fixReply": "Popraw odpowiedź dla {who}{thread}. {tail}",
        "ask.fixTail": "Szkic już czeka na moją zgodę, więc napisz nową wersję innym tekstem: przeczytaj szkic i wątek, i zapisz ją w Wenext jako szkic. Jeśli nie wiesz, co mi w nim nie pasuje, zapytaj mnie jednym zdaniem. Na końcu pokaż mi kartę jeszcze raz.",
        "ask.reply": "Napisz odpowiedź dla {who} w wątku {url}. Przeczytaj wątek, odpowiedz pomocnie, w moim głosie, i zapisz szkic w Wenext. Na końcu pokaż mi kartę.",
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
        "ft.full": "Pełny widok ↗",
        "sk.label": "Pominięte ({n})",
        "sk.byYou": "Ten wątek pominąłeś Ty.",
        "sk.noReason": "Powód nie został zapisany.",
        "sk.more": "i jeszcze {n}",
        "sk.open": "Otwórz wątek",
        "sn.label": "Wysłane ({n})",
        "sn.open": "Otwórz opublikowany post",
        "pub.likes": "Polubienia",
        "ed.labelReply": "Edytuj odpowiedź",
        "ed.labelPost": "Edytuj post",
        "ed.save": "Zapisz",
        "ed.cancel": "Anuluj",
        "ed.saving": "Zapisuję",
        "ed.count": "{n} / {max}",
        "ed.ask": "Poproś zamiast tego agenta o przepisanie",
        "ed.saved": "Zapisane jako nowa wersja. Wyślij, gdy będzie gotowa.",
        "ed.failed": "Nie udało się zapisać zmiany.",
        "ref.PUSTY": "Tekst jest pusty.",
        "ref.BEZ_ZMIAN": "W tekście nic się nie zmieniło.",
        "ref.ZA_DLUGI": "Ten tekst jest za długi, żeby go zapisać.",
        "ref.DLUGA_PAUZA": "Zamiast długiej pauzy użyj kropki, przecinka albo dwukropka.",
        "ref.DLUGOSC_X": "Ten tekst liczy {waga} na X, a X pozwala na {limit}. Skróć go.",
        "sc.accounts": [
            "{n} konto",
            "{n} konta",
            "{n} kont",
        ],
        "sc.perHour": "{h}/h",
        "sc.perDay": "{d}/dobę",
        "sc.until": "do {until}",
        "sc.setLimits": "Ustaw limity",
        "sc.pause": "Wstrzymaj",
        "sc.pausedMsg": "Wstrzymane.",
        "sc.resume": "Wznów",
        "sc.expiredMsg": "Zakres wygasł.",
        "sc.renew": "Odnów na 7 dni",
        "sc.title": "Limity",
        "sc.paceLabel": "Tempo",
        "sc.accountsLabel": "Konta",
        "sc.actionsLabel": "Działania",
        "sc.act.REPLY": "Odpowiedzi",
        "sc.act.OWN_POST": "Własne posty",
        "sc.act.RESEARCH": "Research",
        "sc.hour": "Odpowiedzi na godzinę",
        "sc.day": "Odpowiedzi na dobę",
        "sc.own": "Własne posty na dobę",
        "sc.term": "Na",
        "sc.days": "{d} dni",
        "sc.safe": "Nowe konto? Bezpieczne tempo to jedna odpowiedź na godzinę i sześć na dobę. Zwiększaj je, gdy konto zbuduje historię.",
        "sc.save": "Zapisz",
        "sc.cancel": "Anuluj",
        "sc.errAccount": "Wybierz co najmniej jedno konto.",
        "sc.errAction": "Wybierz co najmniej jedno działanie.",
        "sc.checking": "Sprawdzam",
        "sc.working": "Zapisuję",
        "sc.cPause": "Wstrzymać Wenext w tym projekcie? Nic nie wyjdzie ani nie powstanie, dopóki go nie wznowisz.",
        "sc.cResume": "Wznowić Wenext? Znów może pisać z {accounts}. Każdy tekst nadal czeka na Twoje kliknięcie{until}.",
        "sc.cResumeAuto": "Wznowić Wenext? Ten zakres jest autonomiczny: będzie publikował z {accounts} bez pytania Cię o zgodę{until}.",
        "sc.cSign": "Podpisać ten zakres? Wenext może pisać z {accounts} ({actions}), do {h} na godzinę i {d} na dobę, {own}{until}. Każdy tekst nadal czeka na Twoje kliknięcie.",
        "sc.cWasAuto": "Zastępuje Twój zakres autonomiczny: od teraz każdy tekst czeka na Twoje kliknięcie.",
        "sc.yesPause": "Tak, wstrzymaj",
        "sc.yesResume": "Tak, wznów",
        "sc.yesSign": "Tak, podpisuję",
        "sc.back": "Wróć",
        "sc.donePause": "Wstrzymane. Nic nie wyjdzie, dopóki nie wznowisz.",
        "sc.doneResume": "Wznowione. Każdy tekst nadal czeka na Twoje kliknięcie.",
        "sc.doneSign": "Zapisane. Każdy tekst nadal czeka na Twoje kliknięcie.",
        "sc.done": "Gotowe",
        "sc.refusedDefault": "Nie udało się zmienić zakresu.",
        "sc.close": "Zamknij",
        "sc.retry": "Spróbuj ponownie",
        "sp.raise": "Podnieś limity",
        "sp.review": "Zmień zakres",
        "sp.resume": "Wznów Wenext",
        "ref.ZAKRES_PUSTY": "Zakres potrzebuje co najmniej jednego konta i jednego działania.",
        "ref.BRAK_ZAKRESU": "Ten projekt nie ma jeszcze podpisanego zakresu.",
        "ref.NIE_AKTYWNY": "Zakres nie jest aktywny, więc nie ma czego wstrzymywać.",
        "ref.NIE_WSTRZYMANY": "Zakres nie jest wstrzymany.",
        "ref.ZAKRES_WYGASL": "Zakres wygasł. Ustaw nowe limity, żeby go odnowić.",
        "ref.KONTO_NIEZNANE": "Jedno z kont nie należy do tego projektu.",
        "ref.LIMIT_POZA_GRANICA": "Ten limit jest wyższy, niż karta podpisuje. Wybierz niższą liczbę.",
        "rv.searching": "Szukam wątków",
        "rv.read": [
            "Przeczytano {n} wątek: {where}",
            "Przeczytano {n} wątki: {where}",
            "Przeczytano {n} wątków: {where}",
        ],
        "rv.skip": [
            "+{n} pominięty",
            "+{n} pominięte",
            "+{n} pominiętych",
        ],
        "rv.more": "+{n} więcej",
        "rv.moreWhere": "+{n}",
        "ref.ARGUMENT": "Coś w tym żądaniu było niepoprawne. Spróbuj jeszcze raz.",
        "sc.ownN": [
            "{n} własny post na dobę",
            "{n} własne posty na dobę",
            "{n} własnych postów na dobę",
        ],
        "sc.waitingNote": "Gdy wyjdzie, pokażę Ci potwierdzenie. Nic nie jest podpisywane bez Twojego kliknięcia.",
        "sc.paceNote": "Każde konto ma własne tempo. Odpowiedzi i własne posty liczą się osobno.",
        "sc.eachOwn": "tempo osobno dla kont",
        "sc.cSignAcct": "Podpisać ten zakres? Wenext może pisać z {accounts} ({actions}). Każde konto ma własne tempo{until}. Każdy tekst nadal czeka na Twoje kliknięcie.",
        "sc.paceRow": "{who}: do {h} na godzinę i {d} na dobę, {own}",
        "c.limitsAcct": ", z własnym tempem dla każdego konta: {pace}",
        "ba.limitsAcct": "Limity: {pace}. Każdy odstęp ma kilka minut losowej zwłoki.",
        "ac.stOk": "połączone",
        "ac.stLost": "sesja utracona",
        "ac.stNever": "jeszcze niepołączone",
        "ac.stOther": "zalogowane inne konto",
        "ac.stHeld": "wstrzymane",
        "ac.stUnknown": "nie mogę teraz sprawdzić",
        "ac.stAwaiting": "czeka na Twoje logowanie",
        "ac.reconnect": "Połącz ponownie {who}",
        "ac.connect": "Połącz {who}",
        "ac.finish": "Dokończ logowanie {who}",
        "ac.opening": "Otwieram okno przeglądarki agenta",
        "ac.waiting": "Zaloguj się na {platform} w oknie, które się otworzyło. Poczekam.",
        "ac.waitingNote": "Gdy skończysz, kliknij „Zalogowałem się”. Niczego nie wpisuję ani nie wysyłam za Ciebie.",
        "ac.done": "Zalogowałem się",
        "ac.again": "Otwórz okno ponownie",
        "ac.cancel": "Anuluj",
        "ac.checking": "Sprawdzam {platform}",
        "ac.okNow": "{who} jest połączone",
        "ac.slow": "Wciąż czekam na logowanie. Kliknij „Zalogowałem się”, gdy skończysz.",
        "ac.retry": "Spróbuj ponownie",
        "ac.close": "Zamknij",
        "ac.noWindow": "Okno się otworzyło, ale agent nie przekazał jego adresu. Spróbuj ponownie.",
        "ac.noToken": "Ten agent nie przekazał karcie potwierdzenia, więc nie otworzę okna logowania stąd. Poproś agenta albo użyj dashboardu.",
        "ac.unsure": "Nie udało się sprawdzić logowania: agent nie odpowiedział. Spróbuj jeszcze raz.",
        "ref.ZALOGUJ": "Nie widzę jeszcze zalogowanej sesji. Dokończ logowanie w oknie przeglądarki agenta i kliknij „Zalogowałem się”; jeśli okno się zamknęło, otwórz je ponownie.",
        "ref.INNE_KONTO": "Przeglądarka agenta jest zalogowana na inne konto niż to z projektu. Wyloguj się w oknie i zaloguj właściwe.",
        "ref.NIECZYTELNE": "Nie udało się teraz odczytać sesji. Spróbuj sprawdzić jeszcze raz za chwilę.",
        "ref.KAPSULA": "Przeglądarka agenta nie jest dostępna, więc nie otworzę okna logowania. Sprawdź, czy działa, i spróbuj ponownie.",
        "ref.BRAK_KONTA": "Ten projekt nie ma przypisanego konta tej platformy albo jest ono wstrzymane.",
        "ref.OPERATOR": "Nie umiem ustalić, kto ma się zalogować, bo organizacja ma więcej niż jednego operatora. Otwórz okno w dashboardzie.",
        "ba.open": "Wyślij wszystkie ({n})",
        "ba.title": "Wyślij wszystkie",
        "ba.intro": "Sprawdź listę. Odznacz to, co ma zostać.",
        "ba.pick": "Wyślij: {what}",
        "ba.ownPost": "Własny post",
        "ba.reply": "Odpowiedź",
        "ba.planning": "Układam plan",
        "ba.nothing": "Nic nie jest zaznaczone. Zaznacz co najmniej jeden szkic.",
        "ba.sch": "{now} teraz, potem po jednej co ok. {gap} min, koniec o {end}.",
        "ba.schNow": "Wszystkie wyjdą teraz, koniec o {end}.",
        "ba.schLater": "Pierwsza o {first}, potem po jednej co ok. {gap} min, koniec o {end}.",
        "ba.limits": "Limity: {h} na godzinę, {d} na dobę. Każdy odstęp ma kilka minut losowej zwłoki.",
        "ba.safe": "Nic nie wyjdzie bez tego kliknięcia. Każdy tekst wyjdzie dokładnie taki, jak go widzisz, a tuż przed wysłaniem sprawdzę wątek jeszcze raz.",
        "ba.yes": ["Tak, wyślij {n} szkic", "Tak, wyślij te {n}", "Tak, wyślij te {n}"],
        "ba.cancel": "Anuluj",
        "ba.sending": "Wysyłam do publikatora",
        "ba.changed": "Coś się zmieniło, więc nic nie wysłałem. To jest nowy plan.",
        "ba.retry": "Spróbuj ponownie",
        "ba.close": "Zamknij",
        "ba.done": "Gotowe",
        "ba.okLine": "Zaplanowano {n}.",
        "ba.noLine": "Nie weszło do kolejki: {n}.",
        "ba.sched": "Zaplanowane ({n})",
        "ba.schedTitle": "Zaplanowane",
        "ba.schedNote": "Każdy wątek sprawdzę jeszcze raz tuż przed wysłaniem. Tekst się nie zmienia: wyjdzie dokładnie taki, jak go zatwierdziłeś, albo nie wyjdzie wcale.",
        "ba.recheck": "Sprawdź jeszcze raz",
        "ba.rested": "Przestałem pytać o te wnioski. Kliknij, żeby sprawdzić ich stan jeszcze raz.",
        "ba.at": "o {time}",
        "ba.next": "następna próba {time}",
        "ba.view": "Zobacz ↗",
        "ba.pageHead": "Zaplanowane na {time}.",
        "ba.pageNote": "Sprawdzę wątek jeszcze raz tuż przed wysłaniem.",
        "ba.pageWait": "Czeka na kolejną próbę.",
        "ba.noReason": "Publikator nie podał powodu.",
        "ba.st.QUEUED": "w kolejce",
        "ba.st.PUBLISHING": "publikuję",
        "ba.st.PUBLISHED": "opublikowano",
        "ba.st.SKIPPED": "pominięto",
        "ba.st.BLOCKED": "zablokowano",
        "ba.st.STOPPED": "zatrzymano",
        "ba.st.UNKNOWN": "nie wiadomo",
        "ba.st.waiting": "czeka na kolejną próbę",
        "rs.ZA_STARY": "Wątek zdążył się zestarzeć, więc odpowiedź została pominięta. Tekst się nie zmienił.",
        "rs.ZNIKNAL": "Wątku albo posta już nie ma, więc odpowiedź została pominięta.",
        "rs.ZABLOKOWANY": "Wątek jest zablokowany albo zarchiwizowany, więc odpowiedź została pominięta.",
        "rs.JUZ_ODPOWIEDZIANO": "Na tym wątku jest już odpowiedź z tego konta, więc ta została pominięta.",
        "rs.SESJA": "Przeglądarka agenta nie ma teraz sesji tego konta. Połącz konto ponownie, a spróbuję znów.",
        "rs.SESJA_NIEPEWNA": "Nie udało się sprawdzić sesji konta. To nie jest wylogowanie, spróbuję znów.",
        "rs.INNE_KONTO": "Przeglądarka agenta jest zalogowana na inne konto, więc kolejka została zatrzymana.",
        "rs.TEMPO": "Limit na godzinę albo na dobę jest chwilowo wyczerpany, spróbuję znów.",
        "rs.KAPSULA": "Przeglądarka agenta jest teraz niedostępna, spróbuję znów.",
        "ref.PACZKA_PUSTA": "Nie ma żadnego szkicu do wysłania.",
        "ref.PACZKA_ZA_DUZA": "Paczka może mieć najwyżej 25 szkiców.",
        "ref.PACZKA_ZMIENIONA": "Coś w paczce zmieniło się od chwili, gdy ją pokazano, więc nic nie wysłałem. Sprawdź listę jeszcze raz.",
        "ref.POZA_ZAKRESEM": "Konto tego szkicu nie jest w podpisanym zakresie. Wyślij ten szkic osobno albo ustaw Limity: jedno kliknięcie w paczce niczego nie podpisuje.",
        "ref.POZA_LIMITEM": "Nie mieści się w limitach zakresu w najbliższych godzinach. Zostaje na liście na następną paczkę.",
        "ref.LIMIT_WLASNYCH": "Limit własnych postów na dobę jest już zajęty. Szkic zostaje na liście.",
        "ref.OKNO_ZA_KROTKIE": "Okno zgody tego szkicu zamknęłoby się, zanim przyjdzie jego kolej.",
        "ref.ZAKRES_WYGASA": "Podpisany zakres wygasłby, zanim przyjdzie kolej tego szkicu.",
        "ref.PLAN_PRZESUNIETY": "Termin tego szkicu przesunął się o kilkanaście minut od planu, który widziałeś.",
        "ref.BRAK_LIMITU": "Zakres nie dopuszcza ani jednej publikacji na godzinę albo na dobę.",
        "tg.views": ["{n} wyświetlenie", "{n} wyświetlenia", "{n} wyświetleń"],
        "tg.likes": ["{n} polubienie", "{n} polubienia", "{n} polubień"],
        "tg.replies": ["{n} odpowiedź", "{n} odpowiedzi", "{n} odpowiedzi"],
        "tg.reposts": ["{n} udostępnienie", "{n} udostępnienia", "{n} udostępnień"],
        "tg.points": ["{n} punkt", "{n} punkty", "{n} punktów"],
        "tg.comments": ["{n} komentarz", "{n} komentarze", "{n} komentarzy"],
        "tg.trending": "Na topie",
        "tg.gone": "Tego posta już nie ma.",
        "ql.sent": "Wysłane {n}",
        "ql.skipped": "Pominięte {n}",
        "ql.scheduled": "Zaplanowane {n}",
        "ql.behind": "Jeszcze {n}",
        "ql.limits": "Limity",
        "ql.more": "Więcej",
        "hid.note": "{n} kolejnych czeka za tymi. Pojawią się, gdy wyślesz albo pominiesz któryś z nich.",
        "mt.reply": "Odpowiedź",
        "tp.verified": "Zweryfikowane",
        "tp.video": "Wideo",
        "pv.labelQuote": "Twój cytat, tak jak wyjdzie",
        "pv.quotePost": "Post na X",
        "dc.quoteOn": "Cytat na {p}",
        "sc.sessionsLabel": "Sesje",
        "sc.nowLabel": "Teraz",
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
        "pv.labelPost": "Your post, as it will appear",
        "pv.labelReply": "Your reply, as it will appear",
        "pv.more": "Show all",
        "pv.less": "Show less",
        "ap.goes": "Goes out from {h} after your click.",
        "ap.goesProject": "Goes out from the account assigned to the project, after your click.",
        "ap.inRoom": " Approve it in the full view (link below).",
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
        "c.small": "This click signs a scope: the accounts of this project, and every text waits for your click{until}{limits}. You can change it under Limits, or pause it, at any time.",
        "c.smallWiden": "This click signs the widened scope: every text still waits for your click{until}{limits}. You can change it under Limits, or pause it, at any time.",
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
        "sp.queuedSlow": "This is taking longer than usual. Check again in a moment.",
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
        "e.noAnswer": "The agent did not give the card an answer. Try again, or ask the agent.",
        "e.noConnect": "Could not reach Wenext through the agent. Try again, or ask the agent.",
        "e.noToken": "This agent did not pass the card a confirmation, so sending from the card is not possible. Use the full view, or ask the agent.",
        "e.unsure": "I can't tell whether this went out: the agent did not answer. Ask the agent to show the card again before you click again.",
        "e.unknown": "It is not known whether the reply went out. Check the platform before you send again.",
        "w.BLOCKED": "The publisher blocked the request.",
        "w.SKIPPED": "The publisher skipped the request.",
        "w.STOPPED": "The publisher stopped the request.",
        "ref.STAN": "This draft is not waiting for approval, so it can't be approved. Ask the agent to show the card again.",
        "ref.OKNO": "The approval window for this draft has closed. Ask the agent for a new version.",
        "ref.ODCISK": "This draft was changed after you saw it. Ask the agent to show the card again and decide on the latest version.",
        "ref.ZAKRES": "This project has no signed scope to post in. Set limits below, then send again.",
        "ref.BUDZET": "The posting limit is used up for now. Raise it below, or wait.",
        "ref.ZGODA": "The approval did not go through. Try again.",
        "ref.NIEAKTUALNY": "This draft is no longer waiting for approval: it was approved or revised in the meantime. Ask the agent to show the card again and decide on the latest version.",
        "ref.ZAKRES_WSTRZYMANY": "This project's scope is paused. Resume it below: a pause is your stop, and sending does not lift it.",
        "ref.ZAKRES_INNY_TRYB": "This project's scope runs in a different mode and doesn't cover this account. Change the scope below: from a send I only add accounts to a scope where every text waits for your click.",
        "ref.ZAKRES_KOLEJKA": "The previous reply is still in the publisher's queue, and a new signature would stop it. Wait until it goes out, then try again.",
        "ref.NIE_ZNALEZIONO": "That draft or request isn't in this project.",
        "ref.POTWIERDZENIE": "No valid confirmation from your click: it is unknown, expired or already used. Try again.",
        "ref.ZAKRES_ZMIENIONY": "The scope changed after the confirmation was shown. Try again.",
        "ref.PODPIS": "The scope could not be signed. Try again.",
        "ref.DZIALANIE": "I can't send this kind of action from the card. Ask the agent about it.",
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
        "pub.older": "Results older than 7 days are not listed.",
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
        "rd.noDataBody": "Ask your agent to show the card again.",
        "ask.who": "this person",
        "ask.inThread": " in the thread {url}",
        "ask.inCommunity": " in {c}",
        "ask.fixOwn": "Revise the draft of my post{where}. {tail}",
        "ask.fixReply": "Revise the reply to {who}{thread}. {tail}",
        "ask.fixTail": "The draft is already waiting for my OK, so write a new version with different wording: read the draft and the thread, and save it in Wenext as a draft. If you don't know what I don't like about it, ask me in one sentence. At the end, show me the card again.",
        "ask.reply": "Write a reply to {who} in the thread {url}. Read the thread, answer helpfully in my voice, and save the draft in Wenext. At the end, show me the card.",
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
        "ft.full": "Open full view ↗",
        "sk.label": "Skipped ({n})",
        "sk.byYou": "You skipped this thread.",
        "sk.noReason": "No reason was recorded.",
        "sk.more": "and {n} more",
        "sk.open": "Open the thread",
        "sn.label": "Sent ({n})",
        "sn.open": "Open the live post",
        "pub.likes": "Likes",
        "ed.labelReply": "Edit your reply",
        "ed.labelPost": "Edit your post",
        "ed.save": "Save",
        "ed.cancel": "Cancel",
        "ed.saving": "Saving",
        "ed.count": "{n} / {max}",
        "ed.ask": "Ask the agent to rewrite instead",
        "ed.saved": "Saved as a new version. Send it when you are ready.",
        "ed.failed": "Could not save the change.",
        "ref.PUSTY": "The text is empty.",
        "ref.BEZ_ZMIAN": "Nothing changed in the text.",
        "ref.ZA_DLUGI": "This text is too long to save.",
        "ref.DLUGA_PAUZA": "Use a period, comma or colon instead of the long dash.",
        "ref.DLUGOSC_X": "This text counts {waga} on X, which allows {limit}. Shorten it.",
        "sc.accounts": [
            "{n} account",
            "{n} accounts",
        ],
        "sc.perHour": "{h}/h",
        "sc.perDay": "{d}/day",
        "sc.until": "until {until}",
        "sc.setLimits": "Set limits",
        "sc.pause": "Pause Wenext",
        "sc.pausedMsg": "Paused.",
        "sc.resume": "Resume",
        "sc.expiredMsg": "Scope expired.",
        "sc.renew": "Renew for 7 days",
        "sc.title": "Limits",
        "sc.paceLabel": "Pace",
        "sc.accountsLabel": "Accounts",
        "sc.actionsLabel": "Actions",
        "sc.act.REPLY": "Replies",
        "sc.act.OWN_POST": "Own posts",
        "sc.act.RESEARCH": "Research",
        "sc.hour": "Replies per hour",
        "sc.day": "Replies per day",
        "sc.own": "Own posts a day",
        "sc.term": "For",
        "sc.days": "{d} days",
        "sc.safe": "New account? One an hour and six a day is the safe pace. Raise it as the account builds history.",
        "sc.save": "Save",
        "sc.cancel": "Cancel",
        "sc.errAccount": "Pick at least one account.",
        "sc.errAction": "Pick at least one action.",
        "sc.checking": "Checking",
        "sc.working": "Saving",
        "sc.cPause": "Pause Wenext in this project? Nothing is sent or drafted until you resume.",
        "sc.cResume": "Resume Wenext? It may write again from {accounts}. Every text still waits for your click{until}.",
        "sc.cResumeAuto": "Resume Wenext? This scope is autonomous: it will post from {accounts} without asking you first{until}.",
        "sc.cSign": "Sign this scope? Wenext can write from {accounts} ({actions}), up to {h} an hour and {d} a day, {own}{until}. Every text still waits for your click.",
        "sc.cWasAuto": "It replaces your autonomous scope: from now on every text waits for your click.",
        "sc.yesPause": "Yes, pause",
        "sc.yesResume": "Yes, resume",
        "sc.yesSign": "Yes, sign",
        "sc.back": "Back",
        "sc.donePause": "Paused. Nothing goes out until you resume.",
        "sc.doneResume": "Resumed. Every text still waits for your click.",
        "sc.doneSign": "Saved. Every text still waits for your click.",
        "sc.done": "Done",
        "sc.refusedDefault": "Could not change the scope.",
        "sc.close": "Close",
        "sc.retry": "Try again",
        "sp.raise": "Raise limits",
        "sp.review": "Change scope",
        "sp.resume": "Resume Wenext",
        "ref.ZAKRES_PUSTY": "A scope needs at least one account and one action.",
        "ref.BRAK_ZAKRESU": "This project has no signed scope yet.",
        "ref.NIE_AKTYWNY": "The scope is not active, so there is nothing to pause.",
        "ref.NIE_WSTRZYMANY": "The scope is not paused.",
        "ref.ZAKRES_WYGASL": "The scope has expired. Set new limits to renew it.",
        "ref.KONTO_NIEZNANE": "One of the accounts does not belong to this project.",
        "ref.LIMIT_POZA_GRANICA": "That limit is above what the card signs. Pick a lower number.",
        "rv.searching": "Searching threads",
        "rv.read": [
            "Read {n} thread on {where}",
            "Read {n} threads on {where}",
        ],
        "rv.skip": [
            "+{n} skipped",
            "+{n} skipped",
        ],
        "rv.more": "+{n} more",
        "rv.moreWhere": "+{n}",
        "ref.ARGUMENT": "Something in that request was not valid. Try again.",
        "sc.ownN": [
            "{n} own post a day",
            "{n} own posts a day",
        ],
        "sc.waitingNote": "As soon as it has, I'll show you the confirmation. Nothing is signed without your click.",
        "sc.paceNote": "Each account has its own pace. Replies and own posts are counted separately.",
        "sc.eachOwn": "pace set per account",
        "sc.cSignAcct": "Sign this scope? Wenext can write from {accounts} ({actions}). Each account has its own pace{until}. Every text still waits for your click.",
        "sc.paceRow": "{who}: up to {h} an hour and {d} a day, {own}",
        "c.limitsAcct": ", with its own pace for each account: {pace}",
        "ba.limitsAcct": "Limits: {pace}. Every gap has a few minutes of random delay.",
        "ac.stOk": "connected",
        "ac.stLost": "session lost",
        "ac.stNever": "not connected yet",
        "ac.stOther": "signed in as someone else",
        "ac.stHeld": "paused",
        "ac.stUnknown": "can't check right now",
        "ac.stAwaiting": "waiting for your sign-in",
        "ac.reconnect": "Reconnect {who}",
        "ac.connect": "Connect {who}",
        "ac.finish": "Finish signing in {who}",
        "ac.opening": "Opening the agent's browser window",
        "ac.waiting": "Sign in to {platform} in the window that opened. I'll wait.",
        "ac.waitingNote": "When you're done, press \"I'm signed in\". I never type or send anything for you.",
        "ac.done": "I'm signed in",
        "ac.again": "Open the window again",
        "ac.cancel": "Cancel",
        "ac.checking": "Checking {platform}",
        "ac.okNow": "{who} is connected",
        "ac.slow": "Still waiting for the sign-in. Press \"I'm signed in\" when you're done.",
        "ac.retry": "Try again",
        "ac.close": "Close",
        "ac.noWindow": "The window opened, but the agent did not pass its address. Try again.",
        "ac.noToken": "This agent did not pass the card a confirmation, so I can't open the sign-in window from here. Ask the agent, or use the dashboard.",
        "ac.unsure": "I couldn't check the sign-in: the agent did not answer. Try again.",
        "ref.ZALOGUJ": "I don't see a signed-in session yet. Finish signing in in the agent's browser window and press \"I'm signed in\"; if the window closed, open it again.",
        "ref.INNE_KONTO": "The agent's browser is signed in to a different account than this project's. Sign out in the window and sign in to the right one.",
        "ref.NIECZYTELNE": "I couldn't read the session just now. Try checking again in a moment.",
        "ref.KAPSULA": "The agent's browser is not available, so I can't open the sign-in window. Check that it is running, then try again.",
        "ref.BRAK_KONTA": "This project has no account on that platform, or it is paused.",
        "ref.OPERATOR": "I can't tell who should sign in, because the organization has more than one operator. Open the window in the dashboard.",
        "ba.open": "Send all ({n})",
        "ba.title": "Send all",
        "ba.intro": "Check the list. Untick anything you want to keep back.",
        "ba.pick": "Send: {what}",
        "ba.ownPost": "Own post",
        "ba.reply": "Reply",
        "ba.planning": "Planning the schedule",
        "ba.nothing": "Nothing is ticked. Tick at least one draft.",
        "ba.sch": "{now} now, then one every {gap} min or so, done by {end}.",
        "ba.schNow": "All of them go out now, done by {end}.",
        "ba.schLater": "The first at {first}, then one every {gap} min or so, done by {end}.",
        "ba.limits": "Limits: {h} an hour, {d} a day. Every gap has a few minutes of random delay.",
        "ba.safe": "Nothing goes out without this click. Each text goes out exactly as you see it, and I check the thread again right before posting.",
        "ba.yes": ["Yes, send {n} draft", "Yes, send these {n}"],
        "ba.cancel": "Cancel",
        "ba.sending": "Sending to the publisher",
        "ba.changed": "Something changed, so I sent nothing. This is the new plan.",
        "ba.retry": "Try again",
        "ba.close": "Close",
        "ba.done": "Done",
        "ba.okLine": "Scheduled {n}.",
        "ba.noLine": "Did not make it into the queue: {n}.",
        "ba.sched": "Scheduled ({n})",
        "ba.schedTitle": "Scheduled",
        "ba.schedNote": "I check each thread again right before it goes out. The text never changes: it goes out exactly as you approved it, or not at all.",
        "ba.recheck": "Check again",
        "ba.rested": "I stopped asking about these requests. Press to check where they stand.",
        "ba.at": "at {time}",
        "ba.next": "next try {time}",
        "ba.view": "View ↗",
        "ba.pageHead": "Scheduled for {time}.",
        "ba.pageNote": "I'll check the thread again right before it goes out.",
        "ba.pageWait": "Waiting for the next try.",
        "ba.noReason": "The publisher gave no reason.",
        "ba.st.QUEUED": "queued",
        "ba.st.PUBLISHING": "posting",
        "ba.st.PUBLISHED": "posted",
        "ba.st.SKIPPED": "skipped",
        "ba.st.BLOCKED": "blocked",
        "ba.st.STOPPED": "stopped",
        "ba.st.UNKNOWN": "unknown",
        "ba.st.waiting": "waiting for the next try",
        "rs.ZA_STARY": "The thread went stale, so this reply was skipped. The text did not change.",
        "rs.ZNIKNAL": "The thread or post is gone, so this reply was skipped.",
        "rs.ZABLOKOWANY": "The thread is locked or archived, so this reply was skipped.",
        "rs.JUZ_ODPOWIEDZIANO": "This account already answered in that thread, so this reply was skipped.",
        "rs.SESJA": "The agent's browser has no session for this account right now. Reconnect the account and I'll try again.",
        "rs.SESJA_NIEPEWNA": "I couldn't check the account's session. That is not a sign-out, I'll try again.",
        "rs.INNE_KONTO": "The agent's browser is signed in to another account, so the queue was stopped.",
        "rs.TEMPO": "The hourly or daily limit is spent for now, I'll try again.",
        "rs.KAPSULA": "The agent's browser is unavailable right now, I'll try again.",
        "ref.PACZKA_PUSTA": "There is no draft to send.",
        "ref.PACZKA_ZA_DUZA": "A batch can hold 25 drafts at most.",
        "ref.PACZKA_ZMIENIONA": "Something in the batch changed after it was shown, so I sent nothing. Check the list again.",
        "ref.POZA_ZAKRESEM": "This draft's account is not in the signed scope. Send this draft on its own or set Limits: one click in a batch signs nothing.",
        "ref.POZA_LIMITEM": "It doesn't fit the scope's limits in the next hours. It stays in the list for the next batch.",
        "ref.LIMIT_WLASNYCH": "The daily limit of own posts is already used. The draft stays in the list.",
        "ref.OKNO_ZA_KROTKIE": "This draft's approval window would close before its turn.",
        "ref.ZAKRES_WYGASA": "The signed scope would expire before this draft's turn.",
        "ref.PLAN_PRZESUNIETY": "This draft's moment moved by more than ten minutes from the plan you saw.",
        "ref.BRAK_LIMITU": "The scope allows no publication per hour or per day.",
        "tg.views": ["{n} view", "{n} views"],
        "tg.likes": ["{n} like", "{n} likes"],
        "tg.replies": ["{n} reply", "{n} replies"],
        "tg.reposts": ["{n} repost", "{n} reposts"],
        "tg.points": ["{n} point", "{n} points"],
        "tg.comments": ["{n} comment", "{n} comments"],
        "tg.trending": "Trending",
        "tg.gone": "This post is gone.",
        "ql.sent": "Sent {n}",
        "ql.skipped": "Skipped {n}",
        "ql.scheduled": "Scheduled {n}",
        "ql.behind": "{n} more",
        "ql.limits": "Limits",
        "ql.more": "More",
        "hid.note": "{n} more are waiting behind these. They come up as you send or skip one of these.",
        "mt.reply": "Reply",
        "tp.verified": "Verified",
        "tp.video": "Video",
        "pv.labelQuote": "Your quote, as it will appear",
        "pv.quotePost": "Post on X",
        "dc.quoteOn": "Quote on {p}",
        "sc.sessionsLabel": "Sessions",
        "sc.nowLabel": "Now",
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
.sum { margin: 6px 0 0; font-size: 12px; line-height: 1.6; color: var(--ink-3); }
.sum::first-letter { text-transform: uppercase; }
.sum > span:not(:last-child)::after { content: "\00b7"; margin: 0 3px 0 7px; }

/* One person: the pages share one grid cell; the active one is the only one that shows. */
.stage { display: grid; margin-top: 12px; background: var(--surface); border-radius: 18px; overflow: hidden; }
.page { grid-area: 1 / 1; min-width: 0; display: flex; flex-direction: column; padding: 14px 20px 14px;
  opacity: 0; visibility: hidden; transform: translateX(-22px);
  transition: opacity 150ms ease, transform 320ms var(--ease), visibility 0s linear 320ms; }
.page[data-active] ~ .page { transform: translateX(22px); }
/* v11: the card is as tall as the page on screen, not as the tallest page: with a picture on one page the others would be a big
   empty panel. A page that is not shown takes no height (its content still lays out, so its text can be measured). */
.page:not([data-active]) { height: 0; overflow: hidden; padding-top: 0; padding-bottom: 0; }
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
.pv.join .pvb { padding-top: 0; border-radius: 0 0 14px 14px; box-shadow: inset 0 -1px 0 var(--pvline), inset 1px 0 0 var(--pvline), inset -1px 0 0 var(--pvline); }
.pv.join .pv-label { display: none; }
.region.joined { margin-top: 0; padding-top: 0; }
.pv .pv-label { margin: 0 0 3px; }
.pv-head { flex-wrap: wrap; }
.pv .ptw { margin-top: 2px; }
.pv .pt { font-size: 15px; line-height: 1.4; color: #e7e9ea; max-height: calc(4 * 1.4em); }
.pv .ptw[data-lines="3"] .pt { max-height: calc(3 * 1.4em); }
.pv .ptw[data-lines="2"] .pt { max-height: calc(2 * 1.4em); }
.pv .ptw[data-open] .pt { max-height: calc(14 * 1.4em); }
.pv .more { line-height: 1.4em; font-size: 14px; }
.pv-label { margin-bottom: 7px; font-size: 10.5px; letter-spacing: 0.07em; text-transform: uppercase; color: var(--ink-3); }
.pvb { padding: 12px 14px 12px; border-radius: 14px; background: var(--pvbg); box-shadow: inset 0 0 0 1px var(--pvline);
  color: #dcdedf; }
.pv-head { display: flex; align-items: center; gap: 6px; min-height: 20px; font-size: 14px; color: #71767b; }
.pv-head b { font-weight: 700; color: #e7e9ea; overflow-wrap: anywhere; }
.av { display: grid; place-items: center; width: 22px; height: 22px; flex: none; margin-right: 2px; border-radius: 50%;
  background: #2a2f33; color: #d5d9db; font-size: 9px; font-weight: 600; letter-spacing: 0.02em; }
.chrome { margin-left: auto; display: inline-flex; align-items: center; gap: 12px; padding-left: 12px; opacity: 0.7; font-size: 11.5px; }
.chrome > span { display: inline-flex; align-items: center; gap: 4px; }
.chrome svg { width: 13px; height: 13px; }
.re { margin-top: 1px; font-size: 13.5px; color: #71767b; } .re span { color: #1d9bf0; }
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
/* Buttons, after the feel of a spring (v9, own CSS): a press scales to 0.97 and comes back with a
   small overshoot; a label that changes crossfades with a short blur while the width settles
   (`morph`); the primary one shines once when it first becomes actionable (`shine`). */
.primary, .soft, .pill { position: relative; min-height: 34px; padding: 0 17px; font-weight: 500; overflow: hidden; white-space: nowrap;
  transition: background-color 240ms var(--ease), color 240ms var(--ease), opacity 240ms var(--ease), box-shadow 240ms var(--ease),
    transform 320ms cubic-bezier(0.34, 1.56, 0.64, 1); }
.primary:active:not([disabled]), .soft:active:not([disabled]) { transform: scale(0.97); transition-duration: 240ms, 240ms, 240ms, 240ms, 90ms; }
.nav:active:not([disabled]) { transform: scale(0.92); }
.nav { transition: background-color 240ms var(--ease), color 240ms var(--ease), opacity 240ms var(--ease), transform 320ms cubic-bezier(0.34, 1.56, 0.64, 1); }
.primary { background: #eaeae2; color: #101010; }
.primary:hover:not([disabled]) { background: #fff; }
.primary[disabled] { opacity: 0.45; }
.soft { background: var(--raised); color: var(--ink); box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.07); }
.soft:hover:not([disabled]) { background: #272725; }
.soft[disabled] { background: #161616; color: var(--ink-3); }
/* A state that was a button a moment ago (Checking, Sending, Posted): the same pill, not pressable. */
.pill { display: inline-flex; align-items: center; justify-content: center; vertical-align: middle; margin-right: 8px; border-radius: 999px; background: #eaeae2; color: #101010; opacity: 0.5; cursor: default; white-space: nowrap; }
.pill.done { opacity: 1; background: rgba(211, 228, 175, 0.14); color: var(--accent); }
.pill .ok { margin: 0 8px 0 -4px; vertical-align: 0; }
.pill .wait::after { background: #101010; }
.lbl { display: inline-block; }
.ghost { position: absolute; inset: 0; display: grid; place-items: center; pointer-events: none; }
[data-morph] { transition: width 440ms cubic-bezier(0.2, 0.9, 0.3, 1), background-color 240ms var(--ease), opacity 240ms var(--ease); }
[data-morph] .lbl { animation: lbl-in 340ms var(--ease) both; }
[data-morph] .ghost { animation: lbl-out 260ms var(--ease) both; }
@keyframes lbl-in { from { opacity: 0; filter: blur(4px); transform: translateY(3px); } to { opacity: 1; filter: blur(0); transform: none; } }
@keyframes lbl-out { from { opacity: 1; filter: blur(0); } to { opacity: 0; filter: blur(4px); transform: translateY(-3px); } }
/* One pass of light across the primary button when it first becomes actionable. Not a loop. */
.primary[data-shine]::before { content: ""; position: absolute; inset: 0; pointer-events: none;
  background: linear-gradient(105deg, transparent 30%, rgba(255, 255, 255, 0.75) 50%, transparent 70%);
  transform: translateX(-120%); animation: shine 900ms cubic-bezier(0.3, 0.1, 0.2, 1) 120ms 1 both; }
@keyframes shine { from { transform: translateX(-120%); } to { transform: translateX(120%); } }
/* A thin beam of light runs once around the panel the state just changed in (about 1.5 s), then it is still. */
@property --beam { syntax: "<angle>"; inherits: false; initial-value: 0deg; }
.stage, .sheet { position: relative; }
.stage[data-beam]::after, .sheet[data-beam]::after { content: ""; position: absolute; inset: 0; border-radius: inherit; padding: 1.5px; pointer-events: none;
  background: conic-gradient(from var(--beam), transparent 0deg, transparent 250deg, rgba(211, 228, 175, 0.1) 290deg, rgba(211, 228, 175, 0.95) 345deg, transparent 360deg);
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor;
  mask: linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0); mask-composite: exclude;
  animation: beam 1500ms cubic-bezier(0.4, 0, 0.2, 1) 1 both; }
@keyframes beam { 0% { --beam: 0deg; opacity: 1; } 75% { opacity: 1; } 100% { --beam: 360deg; opacity: 0; } }
/* The check draws itself. */
.ok svg path { stroke-dasharray: 1; stroke-dashoffset: 1; animation: draw 460ms var(--ease) 140ms forwards; }
@keyframes draw { to { stroke-dashoffset: 0; } }
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
/* v11: QUEUED and POSTED are one quiet line under the post, not a panel: a tick or a dot, the words, the first words of the
   reply, and the button where there is one. */
.send.cl { display: flex; align-items: center; gap: 4px 12px; flex-wrap: wrap; margin-top: 0; padding: 11px 14px; border-radius: 0 0 14px 14px; background: var(--pvbg, #0e1113);
  box-shadow: inset 0 -1px 0 rgba(255, 255, 255, 0.07), inset 1px 0 0 rgba(255, 255, 255, 0.07), inset -1px 0 0 rgba(255, 255, 255, 0.07); font-size: 13.5px; }
.send.cl.solo { border-radius: 14px; box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.07); }
.send.cl .msg { display: flex; align-items: baseline; gap: 4px 8px; min-width: 0; flex: 1 1 auto; }
.send.cl .ex { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink-3); }
.send.cl .send-a { margin: 0; flex: none; }
.send.cl .primary, .send.cl .quiet, .send.cl .soft { min-height: 28px; }
.region[data-phase="queued"], .region[data-phase="published"] { margin-top: 0; padding-top: 0; }
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
.bar { margin-top: 4px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 2px 24px; }
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
/* The quiet row on the right of the pager (v9): the two disclosures, Skipped and Sent. */
.dis { margin-left: auto; display: flex; align-items: center; gap: 0 14px; }
/* v11: ONE QUIET LINE under the pager: "Sent 9 · Skipped 18 · Limits" and a small "..." menu (Pause, full view). */
.ql { margin-top: 2px; display: flex; align-items: center; flex-wrap: wrap; gap: 0 4px; min-height: 26px; font-size: 12px; color: var(--ink-3); }
.ql .qb { display: inline-flex; align-items: center; gap: 3px; padding: 4px 2px; font-size: 12px; color: var(--ink-3); white-space: nowrap; }
.ql .qb:hover, .ql .qb[aria-expanded="true"] { color: var(--ink); }
.ql .sep { color: var(--ink-3); opacity: 0.6; }
.ql .gap { flex: 1 1 auto; }
.ql .mn { padding: 3px 8px; font-size: 16px; line-height: 1; letter-spacing: 0.08em; }
.menu { display: none; margin-top: 2px; padding: 4px; border-radius: 12px; background: var(--raised); box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.07); width: max-content; max-width: 100%; margin-left: auto; }
.menu[data-open] { display: grid; }
.menu button { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 8px; text-align: left; font-size: 12.5px; color: var(--ink-2); white-space: nowrap; }
.menu button:hover { background: rgba(255, 255, 255, 0.06); color: var(--ink); }
.menu svg { width: 13px; height: 13px; }
.rnote { padding: 10px 0 6px; font-size: 12px; line-height: 1.5; color: var(--ink-3); }
.dtog { display: inline-flex; align-items: center; gap: 3px; padding: 4px 0; font-size: 12px; color: var(--ink-3); white-space: nowrap; }
.dtog:hover, .dtog[aria-expanded="true"] { color: var(--ink); }
.dtog svg { width: 13px; height: 13px; transition: transform 320ms var(--ease); }
.dtog[aria-expanded="true"] svg { transform: rotate(180deg); }
.drawer { display: grid; grid-template-rows: 0fr; visibility: hidden;
  transition: grid-template-rows 340ms var(--ease), visibility 0s linear 340ms; }
.drawer[data-open] { grid-template-rows: 1fr; visibility: visible; transition-delay: 0s; }
.drawer > div { min-height: 0; overflow: hidden; }
.dr { display: none; }
.drawer[data-open="skipped"] .dr[data-dr="skipped"], .drawer[data-open="sent"] .dr[data-dr="sent"],
.drawer[data-open="scheduled"] .dr[data-dr="scheduled"] { display: block; }
.rows { margin: 8px 0 0; padding: 2px 14px; list-style: none; max-height: 188px; overflow-y: auto; border-radius: 14px; background: var(--surface); }
.rows::-webkit-scrollbar { width: 6px; }
.rows::-webkit-scrollbar-thumb { border-radius: 3px; background: #3a3a37; }
.rw { display: grid; grid-template-columns: 22px minmax(0, 1fr) auto; gap: 0 10px; align-items: start; padding: 9px 0; }
.rw + .rw { border-top: 1px solid var(--line); }
.rw .badge { margin-top: 1px; }
.rw .rb { min-width: 0; }
.rw .t { font-size: 13px; line-height: 1.4; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rw .s { font-size: 11.5px; line-height: 1.45; color: var(--ink-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rw .w1 { margin-top: 2px; font-size: 12.5px; line-height: 1.45; color: var(--body); overflow-wrap: anywhere;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }
.rw .nums { margin-top: 3px; display: flex; flex-wrap: wrap; gap: 0 14px; font-size: 12px; color: var(--ink-3); }
.rw .nums b { font-weight: 400; color: var(--ink); font-variant-numeric: tabular-nums; }
.rw .nums i { font-style: normal; color: var(--warn); }
.lnk { display: grid; place-items: center; width: 26px; height: 26px; margin: -2px -6px 0 0; background: transparent; color: var(--ink-3); }
.lnk:hover { background: var(--raised); color: var(--ink); }
.rmore { padding: 8px 0 6px; font-size: 11.5px; color: var(--ink-3); }

/* The footer line: the promise, the scope in one line, Pause, and the full view. */
.foot { display: flex; flex-wrap: wrap; align-items: center; gap: 0 16px; min-height: 24px; font-size: 11.5px; color: var(--ink-3); }
.foot .promise { margin-right: auto; }
.foot .quiet { display: inline-flex; align-items: center; gap: 5px; padding: 4px 0; white-space: nowrap; }
.foot .quiet svg { width: 12px; height: 12px; }
.foot .warn { display: inline-flex; align-items: center; gap: 6px; color: var(--warn); }
.pdot { width: 5px; height: 5px; border-radius: 50%; background: var(--warn); }

/* v10: the health of the assigned accounts, in the footer, and the window that signs one in. A dot says it: green,
   the agent's browser is signed in as this account; amber, the session was lost (or it is somebody else's); hollow,
   never connected, paused or not checkable. Where a click helps, the chip is a quiet button ("Reconnect @account"). */
.acc { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; color: var(--ink-3); }
.foot .acc.quiet { gap: 6px; }
.adot { width: 6px; height: 6px; flex: none; border-radius: 50%; background: var(--ink-3); }
.acc[data-s="polaczone"] { color: var(--ink-2); }
.acc[data-s="polaczone"] .adot { background: var(--accent); box-shadow: 0 0 0 3px rgba(211, 228, 175, 0.13); }
.acc[data-s="utracona"] .adot, .acc[data-s="inne_konto"] .adot { background: var(--warn); box-shadow: 0 0 0 3px rgba(216, 192, 138, 0.13); }
.acc[data-s="utracona"], .acc[data-s="inne_konto"] { color: var(--warn); }
.acc[data-s="czeka_na_ciebie"] { color: var(--warn); }
.acc[data-s="czeka_na_ciebie"] .adot { background: var(--warn); animation: pulse 1.1s ease-in-out infinite; }
.acc[data-s="nigdy"] .adot, .acc[data-s="wstrzymane"] .adot, .acc[data-s="nieznana"] .adot { background: transparent; box-shadow: inset 0 0 0 1.5px var(--ink-3); }
button.acc:hover:not([disabled]) { color: var(--ink); }
.conn { margin-top: 12px; padding: 12px 16px; border-radius: 14px; background: var(--surface); font-size: 13px; line-height: 1.5; color: var(--body); }
.conn:focus { outline: none; }
.conn small { display: block; margin-top: 4px; font-size: 12px; line-height: 1.55; color: var(--ink-3); }
.conn .send-a { margin-top: 10px; }
.conn[data-p="refused"] .msg::before, .conn[data-p="notyet"] .msg::before { content: ""; display: inline-block; width: 6px; height: 6px;
  margin: 0 8px 2px 0; border-radius: 50%; background: var(--warn); }

/* The first drawing of the card (v9): a short reveal, once per session. A layer on top of the headline and the
   person panel reads "Searching threads", then "Read 5 threads on r/SaaS, r/SideProject, X" with a stack of
   round icons, while small tiles fill in one by one from a dim skeleton; then it lets go and the headline and
   the panel fade in. The real content is in the page from the start and holds its height, so nothing moves.
   Any click or key ends it at once; reduced motion skips it. */
.main { position: relative; }
.main > h1, .main > .sum, .main > .stage { transition: opacity 420ms ease; }
.main[data-reveal] > :not(.rv) { opacity: 0; }
.rv { position: absolute; inset: 0; z-index: 3; display: flex; flex-direction: column; gap: 14px; padding-top: 16px; background: var(--ground); transition: opacity 300ms ease; }
.rv[data-out] { opacity: 0; pointer-events: none; }
.rv-head { position: relative; display: grid; align-items: center; min-height: 28px; font-size: 13px; color: var(--ink-2); }
.rv-a, .rv-b { grid-area: 1 / 1; display: flex; align-items: center; gap: 10px; transition: opacity 300ms ease, filter 300ms ease, transform 300ms var(--ease); }
.rv-b { opacity: 0; filter: blur(4px); transform: translateY(3px); }
.rv-head[data-read] .rv-a { opacity: 0; filter: blur(4px); transform: translateY(-3px); }
.rv-head[data-read] .rv-b { opacity: 1; filter: none; transform: none; }
.spin { width: 14px; height: 14px; border-radius: 50%; border: 1.5px solid rgba(255, 255, 255, 0.14); border-top-color: var(--accent); animation: spin 640ms linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.stack { display: inline-flex; flex: none; }
.stack .av2 { display: grid; place-items: center; width: 22px; height: 22px; margin-left: -7px; border-radius: 50%; border: 2px solid var(--ground);
  background: var(--raised); color: var(--ink-2); font-size: 8.5px; font-weight: 600; letter-spacing: 0.02em;
  opacity: 0; transform: scale(0.6); transition: opacity 260ms ease, transform 360ms cubic-bezier(0.34, 1.56, 0.64, 1); }
.stack .av2:first-child { margin-left: 0; }
.stack .av2[data-p="REDDIT"] { background: #2b2420; color: #e6c3b0; }
.rv-head[data-read] .stack .av2 { opacity: 1; transform: none; }
.rv-head[data-read] .stack .av2:nth-child(2) { transition-delay: 60ms; }
.rv-head[data-read] .stack .av2:nth-child(3) { transition-delay: 120ms; }
.rv-head[data-read] .stack .av2:nth-child(4) { transition-delay: 180ms; }
.rv-tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.tile { position: relative; min-width: 0; height: 66px; padding: 9px 11px; border-radius: 12px; background: rgba(255, 255, 255, 0.03); overflow: hidden;
  transition: background-color 360ms ease; }
.tile:not(.on)::before { content: ""; position: absolute; inset: 0; background: linear-gradient(100deg, transparent 20%, rgba(255, 255, 255, 0.05) 50%, transparent 80%);
  transform: translateX(-100%); animation: sk 900ms ease-in-out infinite; }
@keyframes sk { to { transform: translateX(100%); } }
.tile.on { background: var(--surface); }
.tin { opacity: 0; transform: translateY(3px); transition: opacity 320ms ease, transform 420ms var(--ease); }
.tile.on .tin { opacity: 1; transform: none; }
.tt { font-size: 12px; line-height: 1.3; color: var(--ink); display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; overflow-wrap: anywhere; }
.tm { margin-top: 5px; display: flex; align-items: center; gap: 5px; min-width: 0; font-size: 10.5px; color: var(--ink-3); white-space: nowrap; }
.tm .where { overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.tm .tc { margin-left: auto; display: inline-flex; align-items: center; gap: 3px; flex: none; }
.tm svg { width: 10px; height: 10px; }
.tm .av2 { width: 14px; height: 14px; font-size: 6.5px; margin: 0; border: 0; opacity: 1; transform: none; }
.tile.rest .tt { color: var(--ink-2); }
.tile.rest .stack .av2 { width: 16px; height: 16px; margin-left: -5px; font-size: 7px; border-color: var(--surface); opacity: 1; transform: none; }
.tile.rest { cursor: pointer; }

/* The sheet (v9): the Limits panel and the confirmations of Pause, Resume and Limits. It takes the
   place of the pages, the pager and the footer while it is open, and keeps the height they had. */
.sheet { display: none; margin-top: 14px; padding: 16px 20px 18px; border-radius: 18px; background: var(--surface); }
#app[data-sheet] .sheet { display: block; }
#app[data-sheet="1"] .sheet.batch, #app[data-sheet="batch"] .sheet:not(.batch) { display: none; }
#app[data-sheet] .stage, #app[data-sheet] .bar, #app[data-sheet] .ql, #app[data-sheet] .menu, #app[data-sheet] .drawer, #app[data-sheet] .botx { display: none; }
.sh-title { font-size: 10.5px; letter-spacing: 0.07em; text-transform: uppercase; color: var(--ink-3); }
.sf { margin-top: 12px; display: grid; grid-template-columns: 78px minmax(0, 1fr); gap: 8px 12px; align-items: center; }
.sf > .lab { font-size: 12px; color: var(--ink-3); }
.sf > .ctl { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; min-width: 0; }
.chip { position: relative; display: inline-flex; }
.chip input { position: absolute; inset: 0; width: 100%; height: 100%; margin: 0; opacity: 0; cursor: pointer; }
.chip span { display: inline-flex; align-items: center; min-height: 28px; padding: 0 13px; border-radius: 999px; background: var(--raised); color: var(--ink-2); font-size: 12.5px;
  transition: background-color 240ms var(--ease), color 240ms var(--ease); }
.chip input:checked + span { background: #eaeae2; color: #101010; }
.chip input:disabled + span { opacity: 0.4; }
.chip input:focus-visible + span { outline: 2px solid rgba(211, 228, 175, 0.55); outline-offset: 2px; }
.nf { display: inline-flex; align-items: center; gap: 7px; font-size: 12px; color: var(--ink-3); }
/* One block per account in the Limits panel: its name, then its own three numbers (v11). */
.sf > .ctl.pace { flex-direction: column; align-items: stretch; gap: 8px; }
.pa { display: grid; gap: 6px; padding: 9px 11px 10px; border-radius: 12px; background: var(--well); box-shadow: inset 0 0 0 1px var(--line); }
.pa.off { opacity: 0.45; }
.pa-who { font-size: 12.5px; font-weight: 500; color: var(--ink-2); }
.pa-n { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 14px; min-width: 0; }
.pl { margin: 8px 0 0; padding: 0; list-style: none; display: grid; gap: 4px; font-size: 12.5px; line-height: 1.45; color: var(--ink-2); }
input.num { width: 56px; height: 30px; padding: 0 8px; border: 0; border-radius: 10px; background: var(--well); color: var(--ink); font: inherit; font-size: 13px;
  font-variant-numeric: tabular-nums; text-align: center; box-shadow: inset 0 0 0 1px var(--line); }
input.num:focus-visible { outline: 2px solid rgba(211, 228, 175, 0.55); outline-offset: 2px; }
.sh-note { margin: 12px 0 0; font-size: 11.5px; line-height: 1.5; color: var(--ink-3); }
.sh-err { margin: 8px 0 0; font-size: 12px; color: var(--warn); }
.sh-a { margin-top: 14px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; }
.sh-msg { margin: 12px 0 0; font-size: 14px; line-height: 1.5; color: var(--body); }
.sh-msg b { font-weight: 500; color: var(--ink); }
.sh-msg small { display: block; margin-top: 6px; font-size: 12px; line-height: 1.55; color: var(--ink-3); }

/* A POST DRAWN AS THE PLATFORM DRAWS IT (v11, 13.10): the author's photo in a column on the left, a thread line down that
   column to our reply, the text once, the first picture, a row of counters with the platform's own icons. The post and
   our reply are one rounded block (`.tp.open` and `.pv.join` share its colour), so it reads as a thread. */
.rw2 { display: grid; grid-template-columns: 40px minmax(0, 1fr); column-gap: 12px; min-width: 0; }
.avc { display: flex; flex-direction: column; align-items: center; min-height: 40px; }
.av40 { position: relative; display: grid; place-items: center; width: 40px; height: 40px; flex: none; border-radius: 50%; overflow: hidden;
  background: var(--raised); color: var(--ink-2); font-size: 13px; font-weight: 600; letter-spacing: 0.02em; }
.av40.sm { width: 20px; height: 20px; font-size: 8.5px; }
.av40 .pic { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; }
.av40 .pic:not([src]) { opacity: 0; }
.av40.sn { background: #ff4500; color: #fff; }
.av40.sn svg { width: 74%; height: 74%; fill: #fff; stroke: none; }
.tl { flex: 1 1 auto; width: 2px; min-height: 6px; margin-top: 4px; border-radius: 1px; background: var(--thread, #2f3336); }
.bd { min-width: 0; padding-bottom: 12px; }
.pv .bd { padding-bottom: 0; }
.ph { display: flex; align-items: center; flex-wrap: wrap; gap: 0 4px; min-height: 20px; font-size: 14px; line-height: 1.3; color: var(--ink-3); min-width: 0; }
.ph b { font-weight: 700; color: #e7e9ea; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
.ph .hd { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
.ph .dt::before { content: "\00b7"; margin-right: 4px; }
.vf { width: 17px; height: 17px; flex: none; fill: currentColor; stroke: none; color: #1d9bf0; margin-left: -1px; }
.tp { --pvbg: #000; --thread: #2f3336; margin-top: 10px; padding: 12px 14px 0; border-radius: 14px; background: var(--pvbg); box-shadow: inset 0 0 0 1px #2f3336; color: #e7e9ea; }
.tp[data-p="REDDIT"] { --pvbg: #0e1113; --thread: #343b40; box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.06); }
.tp.open { border-radius: 14px 14px 0 0; box-shadow: inset 0 1px 0 #2f3336, inset 1px 0 0 #2f3336, inset -1px 0 0 #2f3336; }
.tp.open[data-p="REDDIT"] { box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06), inset 1px 0 0 rgba(255, 255, 255, 0.06), inset -1px 0 0 rgba(255, 255, 255, 0.06); }
.tp:not(.open) .bd { padding-bottom: 12px; }
.tp .ptw { margin-top: 2px; }
.tp .pt { font-size: 15px; line-height: 1.4; color: #e7e9ea; max-height: calc(9 * 1.4em); }
.tp .ptw[data-lines="12"] .pt { max-height: calc(12 * 1.4em); }
.tp .more { line-height: 1.4em; font-size: 14px; background: linear-gradient(90deg, transparent, var(--pvbg) 26px); }
.tp .ps { font-size: 12.5px; color: var(--ink-3); margin-top: 1px; }
.tp .rt { margin: 3px 0 0; font-size: 16.5px; font-weight: 600; line-height: 1.3; color: #e7e9ea; overflow-wrap: anywhere; }
.tg-gone { margin: 6px 0 0; font-size: 13px; color: var(--warn); }
.tg-trend { margin-left: auto; flex: none; display: inline-flex; align-items: center; gap: 5px; padding: 2px 9px; border-radius: 999px;
  background: rgba(211, 228, 175, 0.14); color: var(--accent); font-size: 11px; font-weight: 500; white-space: nowrap; }
.tg-trend::before { content: ""; width: 0; height: 0; border-left: 3.5px solid transparent; border-right: 3.5px solid transparent; border-bottom: 6px solid currentColor; }
/* The first picture of a post, shown as a wide band (its ratio is kept between 2.1 and 2.8, so a tall picture is cropped, not stretched
   down the card), with a skeleton until the inline image arrives. */
.xm { position: relative; margin-top: 10px; border-radius: 16px; overflow: hidden; background: #16181c; box-shadow: inset 0 0 0 1px #2f3336; aspect-ratio: 2.4; }
.xm .pic { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; }
.xm .pic:not([src]) { opacity: 0; }
.xm:not(:has(.pic[src])) { background: linear-gradient(110deg, #16181c 30%, #1d2024 50%, #16181c 70%); }
.xm .play { position: absolute; left: 50%; top: 50%; width: 46px; height: 46px; margin: -23px 0 0 -23px; display: grid; place-items: center; border-radius: 50%;
  background: rgba(0, 0, 0, 0.62); box-shadow: inset 0 0 0 1.5px rgba(255, 255, 255, 0.85); }
.xm .play svg { width: 18px; height: 18px; fill: #fff; stroke: none; margin-left: 2px; }
/* The counters, with X's own icons; a counter nobody read is not drawn (never 0). Reddit has pills. */
.mx { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px 14px; max-width: 400px; margin-top: 10px; color: #71767b; font-size: 13px; font-variant-numeric: tabular-nums; }
.mx .mi { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
svg.fi { width: 17px; height: 17px; fill: currentColor; stroke: none; }
.mx.rd { justify-content: flex-start; gap: 8px; max-width: none; }
.mx .rpill { display: inline-flex; align-items: center; gap: 6px; padding: 0 11px; height: 28px; border-radius: 999px; background: rgba(255, 255, 255, 0.06); color: #c9cdd0; font-weight: 600; }
.mx .rpill svg { width: 15px; height: 15px; }
/* The post a draft QUOTES: a bordered box under our words, as X draws it. */
.qbx { margin-top: 10px; padding: 10px 12px 11px; border-radius: 16px; box-shadow: inset 0 0 0 1px #2f3336; overflow: hidden; }
.qbx .ph { gap: 0 4px; font-size: 13.5px; }
.qbx .ph .av40 { margin-right: 5px; }
.qbx .ptw { margin-top: 3px; }
.qbx .pt { font-size: 14px; line-height: 1.4; max-height: calc(6 * 1.4em); }
.qbx .qn { margin: 4px 0 0; font-size: 13px; color: var(--ink-3); }
.qbx .xm { border-radius: 12px; }
.qbx .mx { margin-top: 8px; }

/* Send all (v11): the button in the pager row, the confirm panel (a sheet) and the list of what was scheduled. */
.bar .sall { display: inline-flex; align-items: center; min-height: 28px; margin-left: 10px; padding: 0 13px; border-radius: 999px; font-size: 12.5px; font-weight: 500;
  background: var(--raised); color: var(--ink); box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.07); white-space: nowrap; }
.bar .sall:hover { background: #272725; }
.bl { margin: 10px 0 0; padding: 0; list-style: none; max-height: 232px; overflow-y: auto; border-radius: 12px; background: var(--well); }
.bl::-webkit-scrollbar { width: 6px; }
.bl::-webkit-scrollbar-thumb { border-radius: 3px; background: #3a3a37; }
.bl > li + li { border-top: 1px solid var(--line); }
.br { display: grid; grid-template-columns: 18px 22px minmax(0, 1fr) auto; gap: 0 10px; align-items: center; padding: 8px 12px; cursor: pointer; }
.br input { width: 16px; height: 16px; margin: 0; accent-color: #eaeae2; cursor: pointer; }
.br input:focus-visible { outline: 2px solid rgba(211, 228, 175, 0.55); outline-offset: 2px; }
.br .bt { min-width: 0; display: grid; gap: 1px; }
.br .bh { font-size: 12.5px; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.br .bh b { font-weight: 500; }
.br .bh .bp { margin-left: 6px; color: var(--ink-3); }
.br .b1 { font-size: 12.5px; line-height: 1.4; color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.br .bk { font-size: 12px; color: var(--ink-3); font-variant-numeric: tabular-nums; white-space: nowrap; }
.br[data-on="0"] .bt, .br[data-on="0"] .badge { opacity: 0.45; }
.br[data-on="x"] { cursor: default; }
.br[data-on="x"] input { cursor: default; opacity: 0.35; }
.br[data-on="x"] .bt { opacity: 0.85; }
.br .b1.bx { color: var(--warn); white-space: normal; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.bs { margin: 12px 0 0; font-size: 13.5px; line-height: 1.5; color: var(--body); }
.bs small { display: block; margin-top: 4px; font-size: 11.5px; line-height: 1.5; color: var(--ink-3); }
.sheet.batch .bchg { color: var(--warn); }
.sheet.batch .sh-msg small { display: block; margin-top: 6px; font-size: 12px; line-height: 1.55; color: var(--ink-3); }
.sheet.batch .sh-msg small.bchg { margin-top: 3px; color: var(--warn); }
.sheet.batch .sh-title { margin-top: 14px; }
.sheet.batch > .sh-title:first-child { margin-top: 0; }
.rows.bsl { max-height: 288px; margin-top: 10px; background: var(--well); }

/* Inline edit (v9): the reply becomes a textarea. */
.ed .pv-title.edt { margin: 0 0 6px; padding: 0 2px; color: var(--ink-2); }
textarea.edit { display: block; width: 100%; min-height: 92px; max-height: 220px; padding: 10px 14px; border: 0; border-radius: 12px; resize: vertical;
  background: #0e1113; color: #dcdedf; font: inherit; font-size: 13.5px; line-height: 1.5; box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.14); }
textarea.edit:focus-visible { outline: 2px solid rgba(211, 228, 175, 0.55); outline-offset: 2px; }
.edrow { display: flex; justify-content: space-between; gap: 12px; min-height: 20px; margin-top: 5px; font-size: 11.5px; color: var(--ink-3); }
.edcount { font-variant-numeric: tabular-nums; white-space: nowrap; }
.edcount[data-over] { color: var(--warn); }
.edmsg { color: var(--warn); text-align: right; }
.ed .send-a { margin-top: 8px; }
.ed .send-a .ask { margin-left: auto; }

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
@media (max-width: 560px) {
  .rv-tiles { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .rv-tiles .tile:nth-child(n + 4) { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .rv { display: none; }
  .main[data-reveal] > :not(.rv) { opacity: 1; }
  .page, .why, .dot, .dot::before, .whyt svg, .wait::after { animation: none; }
  * { transition: opacity 120ms linear !important; }
  .primary:active, .soft:active, .nav:active { transform: none !important; }
  .primary[data-shine]::before, .stage[data-beam]::after, .sheet[data-beam]::after, [data-morph] .lbl, [data-morph] .ghost { animation: none !important; }
  .stage[data-beam]::after, .sheet[data-beam]::after, .primary[data-shine]::before { display: none; }
  .ok svg path { animation: none; stroke-dashoffset: 0; }
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
  // v9: what the card knows besides the people. `zakres` is the signed scope (local installs only),
  // `skipped` the threads the agent decided not to answer, `sent` the replies that went out.
  var zakres = null;
  var skipped = [], skippedTotal = 0, sent = [], oppById = {};
  var drawer = null;     // which disclosure is open: "skipped", "sent" or null
  var edits = {};        // revision_id -> the state of the inline editor
  var saved = {};        // revision_id -> true right after an inline save
  var scForm = null;     // the Limits panel's form, kept across redraws
  var hidden = 0;        // drafts and new conversations the server left out of the (at most eight) pages
  var menuOpen = false;  // the small menu behind the three dots
  var scErr = null;      // a client-side refusal of the form ("account" or "action")
  var accounts = [];     // v10: the health of the assigned accounts (`konta_stan`, local installs only)
  var batched = {};      // v11: revision_id -> the scheduled request ("Send all"), shared with the machine's list

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
    check: '<path pathLength="1" d="m5 12.5 4.5 4.5L19 7.5"/>',
    out: '<path d="M7 17 17 7"/><path d="M8 7h9v9"/>',
    pause: '<path d="M8 5v14"/><path d="M16 5v14"/>',
    sliders: '<path d="M4 7h9"/><path d="M17 7h3"/><circle cx="15" cy="7" r="2"/><path d="M4 17h3"/><path d="M11 17h9"/><circle cx="9" cy="17" r="2"/>'
  };
  function ic(name) { return '<svg viewBox="0 0 24 24" aria-hidden="true">' + IC[name] + '</svg>'; }
  // X's own counter icons (filled, 24 px) and its verified mark: the post is drawn as X draws it.
  var FI = {
    reply: "M1.751 10c0-4.42 3.584-8 8.005-8h4.366c4.49 0 8.129 3.64 8.129 8.13 0 2.96-1.607 5.68-4.196 7.11l-8.054 4.46v-3.69h-.067c-4.49.1-8.183-3.51-8.183-8.01zm8.005-6c-3.317 0-6.005 2.69-6.005 6 0 3.37 2.77 6.08 6.138 6.01l.351-.01h1.761v2.3l5.087-2.81c1.951-1.08 3.163-3.13 3.163-5.36 0-3.39-2.744-6.13-6.129-6.13H9.756z",
    repost: "M4.5 3.88l4.432 4.14-1.364 1.46L5.5 7.55V16c0 1.1.896 2 2 2H13v2H7.5c-2.209 0-4-1.79-4-4V7.55L1.432 9.48.068 8.02 4.5 3.88zM16.5 6H11V4h5.5c2.209 0 4 1.79 4 4v8.45l2.068-1.93 1.364 1.46-4.432 4.14-4.432-4.14 1.364-1.46 2.068 1.93V8c0-1.1-.896-2-2-2z",
    like: "M16.697 5.5c-1.222-.06-2.679.51-3.89 2.16l-.805 1.09-.806-1.09C9.984 6.01 8.526 5.44 7.304 5.5c-1.243.07-2.349.78-2.91 1.91-.552 1.12-.633 2.78.479 4.82 1.074 1.97 3.257 4.27 7.129 6.61 3.87-2.34 6.052-4.64 7.126-6.61 1.111-2.04 1.03-3.7.477-4.82-.561-1.13-1.666-1.84-2.908-1.91zm4.187 7.69c-1.351 2.48-4.001 5.12-8.379 7.67l-.503.3-.504-.3c-4.379-2.55-7.029-5.19-8.382-7.67-1.36-2.5-1.41-4.86-.514-6.67.887-1.79 2.647-2.91 4.601-3.01 1.651-.09 3.368.56 4.798 2.01 1.429-1.45 3.146-2.1 4.796-2.01 1.954.1 3.714 1.22 4.601 3.01.896 1.81.846 4.17-.514 6.67z",
    views: "M8.75 21V3h2v18h-2zM18.75 21V8.5h2V21h-2zM13.25 21v-9h2v9h-2zM3.25 21v-5h2v5h-2z",
    play: "M8 5v14l11-7z"
  };
  function fic(name) { return '<svg class="fi" viewBox="0 0 24 24" aria-hidden="true"><path d="' + FI[name] + '"/></svg>'; }
  function vfBadge() {
    return '<svg class="vf" viewBox="0 0 22 22" role="img" aria-label="' + esc(T("tp.verified")) + '"><path d="M20.396 11c-.018-.646-.215-1.275-.57-1.816-.354-.54-.852-.972-1.438-1.246.223-.607.27-1.264.14-1.897-.131-.634-.437-1.218-.882-1.687-.47-.445-1.053-.75-1.687-.882-.633-.13-1.29-.083-1.897.14-.273-.587-.704-1.086-1.245-1.44S11.647 1.62 11 1.604c-.646.017-1.273.213-1.813.568s-.969.854-1.24 1.44c-.608-.223-1.267-.272-1.902-.14-.635.13-1.22.436-1.69.882-.445.47-.749 1.055-.878 1.688-.13.633-.08 1.29.144 1.896-.587.274-1.087.705-1.443 1.245-.356.54-.555 1.17-.574 1.817.02.647.218 1.276.574 1.817.356.54.856.972 1.443 1.245-.224.606-.274 1.263-.144 1.896.13.634.433 1.218.877 1.688.47.443 1.054.747 1.687.878.633.132 1.29.084 1.897-.136.274.586.705 1.084 1.246 1.439.54.354 1.17.551 1.816.569.647-.016 1.276-.213 1.817-.567s.972-.854 1.245-1.44c.604.239 1.266.296 1.903.164.636-.132 1.22-.447 1.68-.907.46-.46.776-1.044.908-1.681s.075-1.299-.165-1.903c.586-.274 1.084-.705 1.439-1.246.354-.54.551-1.17.569-1.816zM9.662 14.85l-3.429-3.428 1.293-1.302 2.072 2.072 4.4-4.794 1.347 1.246z"/></svg>';
  }
  // Reddit's alien, for a community that has no icon of its own to show.
  var SNOO = '<svg viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="12" cy="14" rx="7" ry="5.2"/><circle cx="5.6" cy="10.8" r="1.9"/><circle cx="18.4" cy="10.8" r="1.9"/><circle cx="17.6" cy="5.2" r="1.4"/>' +
    '<path d="M12.3 9.4 13.2 4.4l3.9 1" fill="none" stroke="#fff" stroke-width="1" stroke-linecap="round"/><circle cx="9.5" cy="13.2" r="1.2" fill="#ff4500"/><circle cx="14.5" cy="13.2" r="1.2" fill="#ff4500"/>' +
    '<path d="M9.4 16.3c1.5 1.1 3.7 1.1 5.2 0" fill="none" stroke="#ff4500" stroke-width="1" stroke-linecap="round"/></svg>';

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

  // PICTURES (v11). The card has an empty content security policy and a remote image may not load in the host that draws it,
  // so it never points an image at the internet. A picture is an `img` WITHOUT a source, marked with the address the server
  // gave (`data-pic`); once per new set of addresses the card asks `room.pictures` (an app-only tool) and the server answers
  // with small inline images, which are put in. A picture that never comes leaves the initials (the card never shows a broken
  // image). The hosts are the platforms' own image CDNs, `PICTURE_HOSTS` here pinned equal to the server's list by a test.
  var PICTURE_HOSTS = ["pbs.twimg.com", "abs.twimg.com", "styles.redditmedia.com", "www.redditstatic.com", "i.redd.it", "preview.redd.it",
    "external-preview.redd.it", "a.thumbs.redditmedia.com", "b.thumbs.redditmedia.com"];
  function pictureUrl(u) {
    if (typeof u !== "string" || u.length > 1000 || !/^https:\/\//i.test(u)) return null;
    var host = u.replace(/^https:\/\//i, "").split(/[\/?#]/)[0].toLowerCase();
    return host.indexOf("@") < 0 && PICTURE_HOSTS.indexOf(host) >= 0 ? u : null;
  }
  var pics = {}, askedPics = {};
  function picImg(u) {
    var url = pictureUrl(u); if (!url) return "";
    return '<img class="pic" data-pic="' + esc(url) + '" alt="" referrerpolicy="no-referrer"' + (pics[url] ? ' src="' + esc(pics[url]) + '"' : '') + '>';
  }
  // A round avatar: the picture over the initials; `kind` "sn" is Reddit's alien, "sm" the small one of a quote box.
  function av40(u, label, kind) {
    if (kind === "sn") return '<span class="av40 sn" aria-hidden="true">' + SNOO + '</span>';
    return '<span class="av40' + (kind ? ' ' + kind : '') + '" aria-hidden="true">' + esc(initials(label || "?")) + picImg(u) + '</span>';
  }
  function putPictures() {
    var els = document.querySelectorAll("img[data-pic]"), k, u;
    for (k = 0; k < els.length; k++) {
      u = els[k].getAttribute("data-pic");
      if (pics[u] && els[k].getAttribute("src") !== pics[u]) els[k].setAttribute("src", pics[u]);
    }
  }
  function wantPictures() {
    var els = document.querySelectorAll("img[data-pic]"), want = [], k, u;
    for (k = 0; k < els.length; k++) {
      u = els[k].getAttribute("data-pic");
      if (!pics[u] && !askedPics[u] && want.indexOf(u) < 0) want.push(u);
    }
    putPictures();
    if (!want.length) return;
    want = want.slice(0, 24);
    want.forEach(function (x) { askedPics[x] = true; });
    // A host that cannot carry a tool call (or a server without the tool): the address itself, which loads only where the host's
    // policy lets it (the card declares the picture hosts), and the initials stay where it does not.
    function direct() { want.forEach(function (x) { pics[x] = x; }); putPictures(); }
    if (!canSend) { direct(); return; }
    callTool("room.pictures", { adresy: want }).then(function (res) {
      var data = dataOfResult(res), got = data && data.obrazy;
      if (!res || res.error || !got || typeof got !== "object") { direct(); return; }
      want.forEach(function (x) { if (typeof got[x] === "string" && /^data:image\/(jpeg|png|gif|webp);base64,/.test(got[x])) pics[x] = got[x]; });
      putPictures();
    });
  }
  // A picture that does not load leaves the initials.
  document.addEventListener("error", function (ev) {
    var t = ev.target;
    if (t && t.tagName === "IMG" && t.className === "pic" && t.parentNode) t.parentNode.removeChild(t);
  }, true);

  // The first picture of a post, in its own proportions (kept between 0.8 and 2.2), with a skeleton until the image is there.
  function mediaBlock(media) {
    if (!media || !pictureUrl(media.url)) return "";
    var ratio = media.szer && media.wys ? Math.max(2.1, Math.min(2.8, media.szer / media.wys)) : 2.4;
    return '<div class="xm" data-t="' + esc(media.typ) + '"' + ' style="aspect-ratio: ' + ratio.toFixed(3) + '"' + '>' + picImg(media.url) +
      (media.typ !== "photo" ? '<span class="play" aria-label="' + esc(T("tp.video")) + '">' + fic("play") + '</span>' : '') + '</div>';
  }
  // The counters of a post. X: its own icons and numbers; Reddit: a pill of points and one of comments. A counter nobody read is
  // not drawn: never a 0.
  function counted(n) { return typeof n === "number" && n >= 0; }
  function xMetrics(m) {
    var rows = [["reply", m.odpowiedzi, "tg.replies"], ["repost", m.udostepnienia, "tg.reposts"], ["like", m.polubienia, "tg.likes"], ["views", m.wyswietlenia, "tg.views"]];
    var out = rows.filter(function (r) { return counted(r[1]); }).map(function (r) {
      return '<span class="mi" title="' + esc(countWord(r[1], r[2])) + '">' + fic(r[0]) + '<span>' + esc(compact(r[1])) + '</span></span>';
    }).join("");
    return out ? '<div class="mx">' + out + '</div>' : "";
  }
  function rMetrics(m) {
    var out = (counted(m.polubienia) ? '<span class="rpill" title="' + esc(countWord(m.polubienia, "tg.points")) + '">' + ic("up") + '<span>' + esc(compact(m.polubienia)) + '</span>' + ic("down") + '</span>' : "") +
      (counted(m.odpowiedzi) ? '<span class="rpill" title="' + esc(countWord(m.odpowiedzi, "tg.comments")) + '">' + ic("chat") + '<span>' + esc(compact(m.odpowiedzi)) + '</span></span>' : "");
    return out ? '<div class="mx rd">' + out + '</div>' : "";
  }
  function textBox(t, lines) {
    return '<div class="ptw" data-lines="' + lines + '"><div class="pt">' + esc(t) + '</div><button class="more" data-more="1">' + esc(T("pv.more")) + '</button></div>';
  }
  function trendBadge(o) { return o && o.cel && o.cel.trending ? '<span class="tg-trend">' + esc(T("tg.trending")) + '</span>' : ""; }

  // The text of a draft without the status address it ends with: X turns that address into a quote box, so the address itself is
  // not shown; the box is (`cytat`, from the server). The editor keeps the whole text.
  function stripQuote(d) {
    var t = String(d.tekst == null ? "" : d.tekst), q = d.cytat && d.cytat.url ? String(d.cytat.url) : "";
    t = t.replace(/\s+$/, "");
    return q && t.slice(-q.length) === q ? t.slice(0, -q.length).replace(/\s+$/, "") : t;
  }
  // The post a draft QUOTES, as X draws it under our words: a bordered box with the author, the text, the first picture, the counters.
  function quoteBox(q) {
    var handle = q.autor ? "@" + String(q.autor).replace(/^@/, "") : "", has = !!(q.tekst || q.nazwa), when = ago(q.utworzono);
    var head = '<div class="ph">' + av40(q.awatar, handle || q.nazwa, "sm") + '<b>' + esc(q.nazwa || handle || "X") + '</b>' + (q.zweryfikowany ? vfBadge() : '') +
      (q.nazwa && handle ? '<span class="hd">' + esc(handle) + '</span>' : '') + (when ? '<span class="dt">' + esc(when) + '</span>' : '') + '</div>';
    var body = q.zniknal ? '<p class="tg-gone">' + esc(T("tg.gone")) + '</p>'
      : has ? (q.tekst ? textBox(q.tekst, 6) : '') + mediaBlock(q.media) + xMetrics(q.metryki || {}) : '<p class="qn">' + esc(T("pv.quotePost")) + '</p>';
    return '<div class="qbx" data-quote="1">' + head + body + '</div>';
  }

  // The reply as the platform will show it: a row of the thread, with OUR account's picture, the text exactly as it will go out
  // (never trimmed or softened, only cut to the box, with a way to read the rest: "Show all"). With the post above it (`joined`)
  // it is the second row of one block, under the thread line; an own post stands alone, and a quote carries its box.
  function preview(d, withThread, compact, joined) {
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
    var q = own && x && d.cytat && typeof d.cytat === "object" ? d.cytat : null;
    var text = q ? stripQuote(d) : d.tekst;
    var open = !!expanded[d.revision_id];
    var lines = compact ? 2 : title ? 3 : 4;
    var label = compact || joined ? "" : '<div class="pv-label">' + esc(T(own ? (q ? "pv.labelQuote" : "pv.labelPost") : "pv.labelReply")) + '</div>';
    return '<div class="pv' + (joined ? ' join' : '') + '" data-p="' + esc(x ? "X" : "REDDIT") + '">' + quote +
      '<div class="pvb"><div class="rw2"><div class="avc">' + av40(x ? d.konto_awatar : null, handle) + '</div><div class="bd">' + label +
      '<div class="pv-head"><b>' + esc(handle) + '</b><span>·</span><span>' + esc(T("pv.now")) + '</span></div>' + replying + where + title +
      (text ? '<div class="ptw" data-lines="' + lines + '"' + (open ? ' data-open="1" data-over="1"' : '') + '><div class="pt">' + esc(text) +
        '</div><button class="more" data-more="1">' + esc(T(open ? "pv.less" : "pv.more")) + '</button></div>' : '') +
      (q && !compact ? quoteBox(q) : '') + '</div></div></div></div>';
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
  // "Edit": an inline editor when the host can carry a tool call (the Save is `room.revise_draft`), else
  // what it always was: a message asking the agent to rewrite. Hosted (manual) sending never edits inline.
  function editBtn(i) {
    return canSend && !manual
      ? '<button class="soft" data-edit="' + i + '">' + esc(T("ap.edit")) + '</button>'
      : '<button class="soft" data-fix="' + i + '">' + esc(T("ap.edit")) + '</button>';
  }

  // Under the preview: which account it leaves from, how long the person has, and the buttons.
  function approval(d, i) {
    var e = items[i], url = e ? originOf(e) : null;
    var handle = handleOf(d.platforma, d.konto), s = draftState(d);
    var note = (saved[d.revision_id] ? esc(T("ed.saved")) + ' ' : '') +
      (d.konto ? S("ap.goes", { h: handle }) : S("ap.goesProject")) +
      (s.ex ? ' <span' + (s.expired ? ' data-expired="1"' : '') + '>' + esc(s.ex.text) + '.</span>' : "") +
      (!s.expired && !s.able ? S("ap.inRoom") : "");
    var lead = s.able ? act("send", i, T("ap.send"), "primary") : "";
    return preview(d, !(e && e.opp), false, !!(e && e.opp)) + '<div class="acts"><span class="lead">' + lead + editBtn(i) +
      '</span><span class="hint">' + note + '</span><span class="end">' + originBtn(i, url) + '</span></div>';
  }
  // Whether the page has the post above the reply (an opportunity): then the reply is the second row of one block.
  function joinedAt(i) { var e = items[i]; return !!(e && e.opp); }
  // What the region of a draft holds in the phase of its send. Idle: the reply, who sends it, the buttons. While a send is under
  // way the reply stays (shorter) and the box under it, where the buttons were, says what the click agrees to. QUEUED and POSTED
  // are one quiet line instead of a panel (v11): nothing else is left to decide.
  function regionInner(d, i) {
    var joined = joinedAt(i);
    // v11: a draft that "Send all" scheduled says so, and offers no Send: it is in the publisher's queue.
    if (batched[d.revision_id]) return batchedBox(batched[d.revision_id], joined);
    var ed = edits[d.revision_id];
    if (ed) { ed.i = i; return editorHtml(d, i, ed); }
    if (manual) {
      var m = mOf(d); m.i = i;
      if (m.phase === "idle") return manualApproval(d, i);
      // While the reply sits in the box to copy by hand, the box IS the reply: it is not drawn twice.
      return (m.phase === "opened" && m.kind !== "x" && m.copied === false ? "" : preview(d, false, true, joined)) + manualPanel(d, m);
    }
    var st = sendOf(d); st.i = i;
    if (st.phase === "idle") return approval(d, i);
    if (st.phase === "queued" || st.phase === "published") return sendPanel(d, st, joined);
    return preview(d, false, true, joined) + sendPanel(d, st, joined);
  }
  function region(d, i) {
    return '<div class="region' + (joinedAt(i) ? ' joined' : '') + '" data-box="' + esc(d.revision_id) + '" data-phase="' + esc(batched[d.revision_id] ? "queued" : edits[d.revision_id] ? "edit" : manual ? mOf(d).phase : sendOf(d).phase) + '">' +
      regionInner(d, i) + '</div>';
  }

  // ---- Motion (v9). All of it is skipped under `prefers-reduced-motion`, and none of it moves the layout:
  // a label swap keeps the button where it is, a shine and a beam are paint only.
  function calm() {
    try { return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches); } catch (e) { return false; }
  }
  // The primary button (or pill) of a container, as it is now: its words and its width.
  function grab(root) {
    var b = root && root.querySelector(".primary, .pill");
    return b ? { text: b.textContent.trim(), w: b.getBoundingClientRect().width } : null;
  }
  // The same place after a redraw: the old words fade out with a short blur, the new ones fade in,
  // and the width settles from the old one to the new one.
  function morph(prev, root) {
    if (!prev || !prev.w || calm()) return;
    var b = root.querySelector(".primary, .pill"); if (!b) return;
    var now = b.textContent.trim(); if (!now || now === prev.text) return;
    var w1 = b.getBoundingClientRect().width; if (!w1) return;
    if (!b.querySelector(".lbl")) {
      var lab = document.createElement("span"); lab.className = "lbl";
      while (b.firstChild) lab.appendChild(b.firstChild);
      b.appendChild(lab);
    }
    var ghost = document.createElement("span"); ghost.className = "ghost"; ghost.setAttribute("aria-hidden", "true"); ghost.textContent = prev.text;
    b.appendChild(ghost);
    b.style.width = prev.w + "px"; b.setAttribute("data-morph", "1");
    void b.offsetWidth;
    b.style.width = w1 + "px";
    setTimeout(function () { b.style.width = ""; b.removeAttribute("data-morph"); if (ghost.parentNode) ghost.parentNode.removeChild(ghost); }, 480);
  }
  // One pass of light across a primary button that has just become actionable.
  function shine(root) {
    if (calm() || !root) return;
    var all = root.querySelectorAll(".primary:not([disabled])"), k;
    for (k = 0; k < all.length; k++) all[k].setAttribute("data-shine", "1");
  }
  // A beam around the panel a state just changed in, for about a second and a half.
  function beam(el) {
    if (calm() || !el) return;
    el.removeAttribute("data-beam"); void el.offsetWidth;
    el.setAttribute("data-beam", "1");
    setTimeout(function () { el.removeAttribute("data-beam"); }, 1600);
  }
  var shone = {};       // the pages whose Send has shone already: once per page and session

  // ---- The reveal of the first drawing (see the CSS). Under 1.2 s, ended by any click or key.
  var revealed = false, revealTimers = [], revealOn = false;
  function cancelReveal() {
    var k; for (k = 0; k < revealTimers.length; k++) clearTimeout(revealTimers[k]);
    revealTimers = []; revealOn = false;
  }
  function initialsOf(handle) { return esc(initials(handle)); }
  function avatarOf(platform, author) {
    var h = author ? whoOf(platform, author) : null;
    return '<span class="av2" data-p="' + esc(platform) + '">' + (h ? initialsOf(h) : platform === "REDDIT" ? "R" : "X") + '</span>';
  }
  // The layer: a header that reads "Searching" and then "Read N threads on ...", and tiles for the people found
  // plus one for what was skipped. Everything is there from the start, dim, and fills in by classes.
  function revealLayer(order, list) {
    var wide = window.innerWidth >= 561, cap = wide ? 4 : 3;
    var skippedN = skippedTotal, people = order.length;
    var lastTile = skippedN > 0 ? "skip" : people > cap ? "more" : null;
    var shown = Math.min(people, lastTile ? cap - 1 : cap);
    var tiles = "", k, e, o, d, title, where, author, platform;
    for (k = 0; k < shown; k++) {
      e = order[k]; o = e.opp; d = e.draft;
      platform = (o ? o.platforma : d && d.platforma) === "X" ? "X" : "REDDIT";
      title = o ? postWords(o).title : T(d && d.rodzaj === "OWN_POST" ? "pv.labelPost" : "pv.labelReply");
      where = o ? whereOf(o) : (d ? communityOf(platform, d.spolecznosc) : "");
      author = o ? o.autor : d && d.w_odpowiedzi_na ? d.w_odpowiedzi_na.autor : null;
      tiles += '<div class="tile"><div class="tin"><div class="tt">' + esc(title) + '</div><div class="tm">' + avatarOf(platform, author) +
        '<span class="where">' + esc(where) + '</span>' +
        (o && o.komentarze != null ? '<span class="tc">' + ic("chat") + esc(num(o.komentarze)) + '</span>' : '') + '</div></div></div>';
    }
    if (lastTile) {
      var n = lastTile === "skip" ? skippedN : people - shown;
      var glyphs = skipped.slice(0, 3).map(function (x) { return avatarOf(x.platforma, null); }).join("");
      tiles += '<div class="tile rest' + '" data-rvskip="' + (lastTile === "skip" ? "1" : "") + '"><div class="tin"><div class="tt">' +
        esc(lastTile === "skip" ? PL(n, "rv.skip", { n: n }) : P("rv.more", { n: n })) + '</div><div class="tm">' +
        (lastTile === "skip" && glyphs ? '<span class="stack">' + glyphs + '</span>' : '') + '</div></div></div>';
    }
    // The header: where the threads were read, and a stack of round icons (the people, or the platform's letter).
    var places = [], seen = {};
    list.forEach(function (x) { var w = whereOf(x); if (!seen[w]) { seen[w] = 1; places.push(w); } });
    skipped.forEach(function (x) { var w = whereOf(x); if (!seen[w]) { seen[w] = 1; places.push(w); } });
    var where3 = places.slice(0, 3).join(", ") + (places.length > 3 ? " " + P("rv.moreWhere", { n: places.length - 3 }) : "");
    var total = list.length + skippedN;
    var icons = order.slice(0, 4).map(function (x) {
      var o = x.opp, d = x.draft, p = (o ? o.platforma : d && d.platforma) === "X" ? "X" : "REDDIT";
      return avatarOf(p, o ? o.autor : d && d.w_odpowiedzi_na ? d.w_odpowiedzi_na.autor : null);
    }).join("");
    return '<div class="rv" aria-hidden="true"><div class="rv-head"><span class="rv-a"><i class="spin"></i>' + esc(T("rv.searching")) + '</span>' +
      '<span class="rv-b"><span class="stack">' + icons + '</span><span>' + esc(PL(Math.max(total, 1), "rv.read", { n: total, where: where3 })) + '</span></span></div>' +
      '<div class="rv-tiles">' + tiles + '</div></div>';
  }
  function finishReveal() {
    if (!revealOn) return;
    cancelReveal();
    var main = document.querySelector(".main"), layer = document.querySelector(".rv");
    if (main) main.removeAttribute("data-reveal");
    // The panel fades in over about 400 ms; the Send shines once it is there to be seen.
    revealTimers.push(setTimeout(shineActive, 380));
    if (layer) { layer.setAttribute("data-out", "1"); setTimeout(function () { if (layer.parentNode) layer.parentNode.removeChild(layer); }, 340); }
  }
  function startReveal() {
    var layer = document.querySelector(".rv"); if (!layer) return;
    revealOn = true;
    var head = layer.querySelector(".rv-head"), tiles = layer.querySelectorAll(".tile"), k;
    function at(ms, fn) { revealTimers.push(setTimeout(fn, ms)); }
    at(280, function () { if (head) head.setAttribute("data-read", "1"); });
    for (k = 0; k < tiles.length; k++) (function (t, i) { at(320 + i * 110, function () { t.classList.add("on"); }); })(tiles[k], k);
    at(900, finishReveal);
  }
  // Any click or key takes the reveal away at once; a click on the skipped tile also opens what was skipped.
  document.addEventListener("click", function (ev) {
    if (!revealOn) return;
    var skip = ev.target && ev.target.closest && ev.target.closest("[data-rvskip]");
    finishReveal();
    if (skip && skip.getAttribute("data-rvskip") && drawer !== "skipped") setDrawer("skipped");
  }, true);
  document.addEventListener("keydown", function () { finishReveal(); }, true);
  document.addEventListener("animationend", function (ev) {
    if (ev.animationName === "shine" && ev.target && ev.target.removeAttribute) ev.target.removeAttribute("data-shine");
  }, true);

  // ---- Send: two steps in the person's own agent, then follow the request (see the docstring).
  function sendOf(d) { return sender.of(d); }
  function box(html, phase, actions, extra) {
    return '<div class="send' + (extra ? ' ' + extra : '') + '" role="status" tabindex="-1" data-p="' + phase + '"><div class="msg">' + html + '</div>' +
      (actions ? '<div class="send-a">' + actions + '</div>' : '') + '</div>';
  }
  // A state shown as the button it replaced, not pressable: "Checking", "Sending", "Saving".
  function pill(key, cls) { return '<span class="pill"><span class="lbl' + (cls ? ' ' + cls : '') + '">' + esc(T(key)) + '</span></span>'; }
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
        limits: (plan.limity_kont || []).length ? P("c.limitsAcct", { pace: paceOf(plan.limity_kont) })
          : lim.godzina != null && lim.doba != null ? P("c.limits", { h: num(lim.godzina), d: num(lim.doba) }) : ""
      });
      return head + changed + '<small>' + small + '</small>';
    }
    return S(own ? "c.plainPost" : "c.plainReply", { h: handle }) + changed + '<small>' + esc(T("rd.foot")) + '</small>';
  }
  function sendPanel(d, st, joined) {
    var i = st.i, cl = "cl" + (joined ? "" : " solo");
    switch (st.phase) {
      case "checking": return box(pill("sp.checking", "wait"), "checking");
      case "waiting":
        return box('<span class="wait">' + esc(T("sp.waiting")) + '</span><small>' + esc(T("sp.waitingNote")) + '</small>',
          "waiting", act("cancel", i, T("sp.cancel"), "quiet"));
      case "confirm":
        return box(consent(d, st), "confirm", act("go", i, T("sp.yes"), "primary") + act("cancel", i, T("sp.cancel"), "quiet"));
      case "sending": return box(pill("sp.sending", "wait"), "sending");
      case "queued":
        return box('<b>' + esc(T("sp.queued")) + '</b> <span class="ex">' + esc(T(st.slow ? "sp.queuedSlow" : "sp.queuedSoon")) + '</span>' +
          (st.slow ? '' : '<span class="wait"></span>'), "queued",
          st.slow ? act("refresh", i, T("sp.recheck"), "soft") : "", cl);
      case "published":
        return box('<span class="pill done"><span class="ok">' + ic("check") + '</span><span class="lbl">' + esc(T("sp.posted")) + '</span></span>' + (st.seen ? ' <span class="ex">' + esc(P("sp.visibility", { seen: st.seen })) + '</span>' : ''), "published",
          (web(st.url) ? act("live", i, T("sp.view"), "primary") : "") + act("close", i, T("sp.done"), "quiet"), cl);
      case "failed":
        return box('<b>' + esc(T("sp.failed")) + '</b> ' + esc(st.reason || T("sp.failedDefault")) +
          (st.note ? '<small>' + esc(st.note) + '</small>' : ''), "failed", act("close", i, T("sp.close"), "quiet"));
      case "refused":
        // "Try again" is always there: a refusal is a state to leave, never a dead end. It asks the
        // server again from the start and ends, as the first press does, at a confirmation to click.
        var fix = scopeFix(st.code), lead = fix
          ? '<button class="primary" data-scope="' + fix.name + '">' + esc(T(fix.label)) + '</button>'
          : (web(st.presence) ? act("presence", i, T("sp.presence"), "primary") : "");
        return box(esc(st.reason || T("sp.refusedDefault")), "refused",
          lead + act("retry", i, T("sp.retry"), lead ? "soft" : "primary") + act("close", i, T("sp.close"), "quiet"));
      default: return "";
    }
  }
  function paintSend(d) {
    var st = sendOf(d), el = document.querySelector('[data-box="' + d.revision_id + '"]');
    if (!el) return;
    var had = !!(document.activeElement && el.contains(document.activeElement));
    // While a send is under way the region keeps the height it had when it was idle: the card does not
    // jump. A reply that was open is folded first, so the height kept is the one of the page as it was.
    if (st.phase === "idle" || st.phase === "queued" || st.phase === "published") el.style.minHeight = "";
    else if (!el.style.minHeight) {
      var wide = el.querySelector(".ptw[data-open]"); if (wide) fold(wide);
      el.style.minHeight = el.offsetHeight + "px";
    }
    el.setAttribute("data-phase", st.phase);
    var prev = grab(el);
    el.innerHTML = regionInner(d, st.i);
    morph(prev, el);
    if (st.phase === "confirm" || st.phase === "published") { shine(el); beam(el.closest(".stage")); }
    // The button that was pressed is gone: the focus goes to the box (which sends nothing), never to a
    // button, so a key held down cannot reach "Yes, send".
    var box = el.querySelector(".send");
    if (had && box) { try { box.focus({ preventScroll: true }); } catch (e) {} }
    syncBatchButton();
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
      set(d, { phase: "refused", reason: env.reason(data, why), presence: data && data.obecnosc_adres, code: data && data.kod });
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


  /*SCOPE-BEGIN*/
  // THE SCOPE MACHINE (v9): Pause, Resume and Limits. One sheet, one phase at a time:
  //
  //   idle -> panel -> checking -> confirm -> working -> done | refused        (Limits: the form first)
  //   idle ->          checking -> confirm -> working -> done | refused        (Pause, Resume)
  //                       |   ^
  //                       v   |
  //                    waiting          a request still waits in the publisher's queue (signing would
  //                                     stop it): ask again every 4 s for about a minute, never sign
  //
  // THE ONE RULE, the same as the send's: a scope changes in ONE place, `go`, reached by the "Yes" button
  // and by nothing else. Asking (`room.scope_token`) changes nothing: it validates what the form says
  // on the server and answers with the sentence the person is about to agree to, and a token. The
  // redeeming call (`room.pause`, `room.resume`, `room.sign_scope`) carries the token and NOTHING ELSE:
  // the server signs what it showed, so the card cannot alter a scope after the person read it.
  //
  // Pure: no DOM, no window. `env` brings `call`, `later` (a timer), `paint`, `T`, `reason` and `apply`
  // (the scope the server reports after a change).
  function makeScoper(env) {
    var st = { phase: "idle", run: 0 };
    var seq = 0;
    var WAIT_STEP = 4000, WAIT_LIMIT = 60000;
    function own(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
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
    function set(patch) {
      var run = st.run, key;
      for (key in st) if (own(st, key)) delete st[key];
      for (key in patch) if (own(patch, key)) st[key] = patch[key];
      if (st.run == null) st.run = run;
      env.paint(); return st;
    }
    function live(run, phase) { return st.run === run && st.phase === phase; }
    function refuse(kind, form, data, why) {
      set({ phase: "refused", kind: kind, form: form, reason: env.reason(data, why), code: data && data.kod });
    }
    // The question: what would this do, and may it? Sends nothing, signs nothing. `again` is a re-ask
    // of the same run while the publisher's queue is busy (`waited` is how long, in ms).
    function ask(kind, form, again, waited) {
      var run = again || ++seq;
      if (!again) set({ phase: "checking", run: run, kind: kind, form: form });
      var body = kind === "pause" ? { akcja: "wstrzymaj" } : kind === "resume" ? { akcja: "wznow" }
        : { akcja: "podpisz", konta: form.konta, dzialania: form.dzialania, limity: form.limity, dni: form.dni };
      if (kind === "sign" && form.limity_konta && Object.keys(form.limity_konta).length) body.limity_konta = form.limity_konta;
      env.call("room.scope_token", body).then(function (res) {
        if (!live(run, "checking") && !live(run, "waiting")) return;
        var data = dataOf(res), token = tokenOf(res);
        if (!res || res.error || !data) return refuse(kind, form, null, res && res.error ? env.T("e.noConnect") : null);
        if (data.stan !== "WYKONANE") {
          if (data.kod === "ZAKRES_KOLEJKA" && (waited || 0) < WAIT_LIMIT) {
            set({ phase: "waiting", run: run, kind: kind, form: form });
            env.later(function () {
              if (!live(run, "waiting")) return;
              ask(kind, form, run, (waited || 0) + WAIT_STEP);
            }, WAIT_STEP);
            return;
          }
          return refuse(kind, form, data);
        }
        if (!token) return refuse(kind, form, null, env.T("e.noToken"));
        set({ phase: "confirm", run: run, kind: kind, form: form, token: token, plan: data.zakres });
      });
    }
    // THE ONLY PLACE A SCOPE IS CHANGED. Reached by the "Yes" button and by nothing else.
    function go() {
      if (st.phase !== "confirm" || !st.token) return;
      var run = st.run, kind = st.kind, form = st.form, token = st.token;
      set({ phase: "working", run: run, kind: kind, form: form });
      var tool = kind === "pause" ? "room.pause" : kind === "resume" ? "room.resume" : "room.sign_scope";
      env.call(tool, { token: token }).then(function (res) {
        if (!live(run, "working")) return;
        var data = dataOf(res);
        if (!res || res.error || !data) return refuse(kind, form, null, env.T("e.unsure"));
        if (data.stan === "WYKONANE" && data.zakres) { env.apply(data.zakres); set({ phase: "done", run: run, kind: kind }); return; }
        refuse(kind, form, data);
      });
    }
    function dispatch(name, form) {
      if (name === "panel") set({ phase: "panel", run: ++seq, kind: "sign", form: form });
      else if (name === "pause") ask("pause", null);
      else if (name === "resume") ask("resume", null);
      else if (name === "save") { if (st.phase === "panel") ask("sign", form); }
      else if (name === "go") go();
      else if (name === "back") { if ((st.phase === "confirm" || st.phase === "refused") && st.kind === "sign") set({ phase: "panel", run: ++seq, kind: "sign", form: st.form }); }
      else if (name === "retry") {
        if (st.phase !== "refused") return;
        if (st.kind === "sign") set({ phase: "panel", run: ++seq, kind: "sign", form: st.form }); else ask(st.kind, null);
      } else if (name === "cancel" || name === "close" || name === "done") set({ phase: "idle", run: ++seq });
    }
    return { state: function () { return st; }, dispatch: dispatch };
  }
  /*SCOPE-END*/

  var scoper = makeScoper({
    call: callTool,
    later: function (fn, ms) { return setTimeout(fn, ms); },
    paint: function () { paintSheet(); },
    T: T,
    reason: reasonOf,
    apply: function (z) {
      zakres = z; if (lastData) lastData.zakres = z;
      // A refusal that this change fixes is cleared, so the person only has to press Send again.
      items.forEach(function (e) { if (e.draft && sender.of(e.draft).phase === "refused") sender.dispatch("close", e.draft); });
    }
  });

  /*BATCH-BEGIN*/
  // THE BATCH MACHINE (v11): "Send all". One panel, one phase at a time:
  //
  //   idle -> checking -> confirm -> sending -> done | refused
  //             ^   |        |  (a tick or an untick asks again: checking)
  //             |   v        v
  //             +--- confirm, with the plan of exactly the ticked set
  //
  // THE ONE RULE, the same as the send's and the scope's: a batch leaves this card in ONE place, `go`, reached by
  // the "Yes, send these N" button and by nothing else. Asking (`room.batch_token`) sends nothing and signs
  // nothing: it validates every ticked draft on the server and answers with the plan (the order and the moment
  // of each reply) and a one-time confirmation in `_meta`. THE CONFIRMATION BINDS THE EXACT SET, so a tick or an
  // untick is a new question for the new set (the earlier confirmation dies on the server, and the card drops
  // it), and the redeeming call (`room.send_batch`) carries the token and NOTHING ELSE: the server sends what
  // it showed. A refusal that heals (a stale confirmation, a batch that changed since it was shown) asks again
  // ONCE and ends at the confirmation, which waits for the click; a second refusal is shown.
  //
  // AFTER THE SEND the machine follows each scheduled request with `room.send_status`, and nothing else: a
  // request whose moment is near (or past) every STEP, a far one every SLOW-th round, until the publisher says.
  // It rests (and offers "Check again") a while after the last moment.
  //
  // Pure: no DOM, no window. `env` brings `call`, `later`, `now` (ms), `paint`, `paintTracked`, `apply` (the
  // scheduled requests, for the pages), `T`, `reason`.
  function makeBatcher(env) {
    var st = { phase: "idle", run: 0 };
    var seq = 0, pollId = 0, resting = false;
    var tracked = [];
    var STEP = 5000, SLOW = 12, SOON = 45000, LINGER = 1800000;
    var META = "wenext/potwierdzenie";
    var FINAL = /^(PUBLISHED|SKIPPED|BLOCKED|STOPPED|UNKNOWN)$/;
    function own(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
    function copyOf(o) { var c = {}, k; for (k in o) if (own(o, k)) c[k] = o[k]; return c; }
    function dataOf(res) {
      var r = res && res.result; if (!r) return null;
      if (r.structuredContent && typeof r.structuredContent === "object") return r.structuredContent;
      try { return JSON.parse(r.content[0].text); } catch (e) { return null; }
    }
    // The confirmation rides ONLY in `_meta`: never in text, never in structured content.
    function tokenOf(res) {
      var m = res && res.result && res.result._meta;
      var t = m && m[META];
      return typeof t === "string" && t ? t : null;
    }
    function set(patch) {
      var run = st.run, key;
      for (key in st) if (own(st, key)) delete st[key];
      for (key in patch) if (own(patch, key)) st[key] = patch[key];
      if (st.run == null) st.run = run;
      env.paint(); return st;
    }
    // A new state that keeps the list, the ticks and what the server turned down, unless `patch` says otherwise.
    function keep(patch) {
      var base = { cands: st.cands, picks: st.picks, excl: st.excl, changed: st.changed, reminted: st.reminted }, key;
      for (key in patch) if (own(patch, key)) base[key] = patch[key];
      return set(base);
    }
    function live(run, a, b) { return st.run === run && (st.phase === a || st.phase === b); }
    function ticked() {
      return (st.cands || []).filter(function (c) { return st.picks[c.rev] && !st.excl[c.rev]; });
    }
    function fail(run, data, why) {
      keep({ phase: "refused", run: run, token: null, plan: null, reason: env.reason(data, why), code: data && data.kod, presence: data && data.obecnosc_adres });
    }

    // The question: may these go, and when? Sends nothing. `how` says what the run has done so far: `changed`
    // (the batch changed since it was shown), `reminted` (the confirmation was asked for again once).
    function ask(how) {
      var run = ++seq, want = ticked();
      how = how || {};
      if (!want.length) { keep({ phase: "confirm", run: run, token: null, plan: null, changed: !!how.changed, reminted: !!how.reminted }); return; }
      keep({ phase: "checking", run: run, token: null, plan: null, changed: !!how.changed, reminted: !!how.reminted });
      env.call("room.batch_token", {
        revision_ids: want.map(function (c) { return c.rev; }),
        odciski: want.map(function (c) { return c.odcisk; })
      }).then(function (res) {
        if (!live(run, "checking")) return;
        var data = dataOf(res), token = tokenOf(res), excl = copyOf(st.excl);
        if (!res || res.error || !data) return fail(run, null, res && res.error ? env.T("e.noConnect") : null);
        (data.odrzucone || []).forEach(function (o) { excl[o.revision_id] = { code: o.kod, reason: o.powod }; });
        if (data.stan !== "WYKONANE") {
          keep({ phase: "refused", run: run, excl: excl, token: null, plan: null, reason: env.reason(data), code: data.kod, presence: data.obecnosc_adres });
          return;
        }
        if (!token) return fail(run, null, env.T("e.noToken"));
        keep({ phase: "confirm", run: run, excl: excl, token: token, plan: data.paczka });
      });
    }

    function open(cands) {
      var picks = {};
      if (st.phase !== "idle" || !cands || cands.length < 2) return;
      cands.forEach(function (c) { picks[c.rev] = true; });
      set({ phase: "checking", run: seq, cands: cands, picks: picks, excl: {}, changed: false, reminted: false });
      ask();
    }
    function toggle(rev) {
      if (st.phase !== "confirm" && st.phase !== "checking") return;
      if (st.excl[rev] || !(st.cands || []).some(function (c) { return c.rev === rev; })) return;
      var picks = copyOf(st.picks);
      picks[rev] = !picks[rev];
      keep({ picks: picks, run: st.run, phase: st.phase, token: null, plan: st.plan });
      ask();
    }

    // THE ONLY PLACE A BATCH IS SENT. Reached by the "Yes, send these N" button and by nothing else.
    function go() {
      if (st.phase !== "confirm" || !st.token || !st.plan || !(st.plan.pozycje || []).length) return;
      var run = st.run, token = st.token, reminted = st.reminted;
      keep({ phase: "sending", run: run, token: null });
      env.call("room.send_batch", { token: token }).then(function (res) {
        if (!live(run, "sending")) return;
        var data = dataOf(res);
        if (!res || res.error || !data) return fail(run, null, env.T("e.unsure"));
        if (data.stan === "OCZEKUJE" && data.zaplanowane && data.zaplanowane.length) {
          follow(data.zaplanowane);
          return keep({ phase: "done", run: run, token: null, sentN: data.zaplanowane.length, no: data.odmowy || [] });
        }
        // The confirmation went stale, or the batch is not what was shown: the server wrote NOTHING. Ask
        // again, ONCE: that ends at `confirm`, and the person clicks again. A second refusal is shown.
        if (!reminted && (data.kod === "POTWIERDZENIE" || data.kod === "ZAKRES_ZMIENIONY" || data.kod === "PACZKA_ZMIENIONA")) {
          return ask({ changed: data.kod !== "POTWIERDZENIE", reminted: true });
        }
        keep({ phase: "refused", run: run, token: null, plan: null, reason: env.reason(data), code: data.kod, presence: data.obecnosc_adres });
      });
    }

    // What the publisher did with each scheduled request, until it says. PUBLISHED is the only word that is
    // "posted": a request in the queue is not a post.
    function follow(rows) {
      var k, r;
      for (k = 0; k < rows.length; k++) {
        r = rows[k];
        tracked.push({ rev: r.revision_id, request: r.wniosek, at: r.kiedy, platform: r.platforma, account: r.konto,
          status: "QUEUED", reason: null, code: null, url: null, seen: null });
      }
      resting = false;
      env.apply(tracked);
      poll(++pollId, 0);
    }
    function read(t, res) {
      var data = dataOf(res), w = data && data.wniosek;
      if (!w) return;
      t.status = w.status; t.reason = w.powod || null; t.code = w.powod_kod || null; t.url = w.adres || null; t.seen = w.widocznosc || null;
      if (w.najwczesniej) t.at = w.najwczesniej;
    }
    // `force` is "Check again": one round that asks about everything not final, whatever the rest said.
    function poll(id, round, force) {
      env.later(function () {
        if (id !== pollId) return;
        var now = env.now(), open = tracked.filter(function (t) { return !FINAL.test(t.status); }), last = 0;
        if (!open.length) { env.paintTracked(); return; }
        open.forEach(function (t) { last = Math.max(last, Date.parse(t.at) || 0); });
        if (!force && last && now > last + LINGER) { resting = true; env.paintTracked(); return; }
        var due = open.filter(function (t) {
          var at = Date.parse(t.at);
          return force || isNaN(at) || at - now <= SOON || round % SLOW === 0;
        }), k = 0;
        (function next() {
          if (id !== pollId) return;
          if (k >= due.length) { env.paintTracked(); poll(id, round + 1, false); return; }
          var t = due[k++];
          env.call("room.send_status", { request_id: t.request }).then(function (res) {
            if (id !== pollId) return;
            read(t, res); next();
          });
        })();
      }, STEP);
    }

    // What a button of the panel does.
    function dispatch(name, arg) {
      if (name === "open") open(arg);
      else if (name === "toggle") toggle(arg);
      else if (name === "go") go();
      else if (name === "retry") { if (st.phase === "refused" && st.cands) ask(); }
      else if (name === "cancel" || name === "close" || name === "done") { if (st.phase !== "sending") set({ phase: "idle", run: ++seq }); }
      else if (name === "refresh") { resting = false; poll(++pollId, 0, true); env.paintTracked(); }
    }
    return {
      state: function () { return st; },
      tracked: function () { return tracked; },
      resting: function () { return resting; },
      dispatch: dispatch
    };
  }
  /*BATCH-END*/

  var batcher = makeBatcher({
    call: callTool,
    later: function (fn, ms) { return setTimeout(fn, ms); },
    now: function () { return Date.now(); },
    paint: function () { paintBatch(); },
    paintTracked: function () { paintTracked(); },
    apply: function (list) {
      list.forEach(function (t) { batched[t.rev] = t; });
      list.forEach(function (t) { paintBatched(t.rev); });
    },
    T: T,
    reason: reasonOf
  });

  /*CONNECT-BEGIN*/
  // THE CONNECT MACHINE (v10): "Reconnect" for the account of one platform. One state per platform, one phase
  // at a time:
  //
  //   idle -> asking -> opening -> waiting -> checking -> connected
  //             |          |        |   ^         |
  //             v          v        v   |         v
  //          refused    refused    slow +      notyet           "Open the window again" and "Try again" start over
  //
  // THE RULES, held in node: (1) the window is opened in ONE place, `begin`, reached only by the press on
  // "Reconnect" (and its repeats); it asks for the confirmation (`room.connect_token`) and acts with it
  // (`room.connect_account`) in the same chain, so the person clicks once and nothing else can open it;
  // (2) `room.connect_done` is called only by `finish`, reached only by "I'm signed in" and "Cancel";
  // (3) the poll calls `room.account_status` and nothing else: while the window is open the platform cannot be
  // read, so it only learns that somebody else closed it or that the account is connected; when it says
  // connected the dot turns green by itself; (4) the address of the window is read from `_meta` and from
  // nowhere else. The poll asks every 4 s for 3 minutes, then rests in `slow`, which keeps its buttons.
  //
  // Pure: no DOM, no window. `env` brings `call`, `later`, `paint(platform)`, `T`, `reason`, `open` (a link) and
  // `apply(platform, handle)` (the account is connected: draw the dot green).
  function makeConnector(env) {
    var conns = {};
    var seq = 0;
    var STEP = 4000, TRIES = 45;
    var META_TOKEN = "wenext/potwierdzenie", META_URL = "wenext/adres_logowania";
    function own(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
    function dataOf(res) {
      var r = res && res.result; if (!r) return null;
      if (r.structuredContent && typeof r.structuredContent === "object") return r.structuredContent;
      try { return JSON.parse(r.content[0].text); } catch (e) { return null; }
    }
    // The confirmation and the address ride ONLY in `_meta`: never in text, never in structured content.
    function metaOf(res, key) {
      var m = res && res.result && res.result._meta;
      var t = m && m[key];
      return typeof t === "string" && t ? t : null;
    }
    function of(p) { return conns[p] || (conns[p] = { phase: "idle", run: 0 }); }
    function set(p, patch) {
      var st = of(p), run = st.run, key;
      for (key in st) if (own(st, key)) delete st[key];
      for (key in patch) if (own(patch, key)) st[key] = patch[key];
      if (st.run == null) st.run = run;
      env.paint(p); return st;
    }
    function live(p, run, a, b) {
      var st = conns[p];
      return !!st && st.run === run && (st.phase === a || st.phase === b);
    }
    function refuse(p, run, handle, data, why) {
      set(p, { phase: "refused", run: run, handle: handle, code: data && data.kod, reason: env.reason(data, why) });
    }
    function connected(p, run, handle) {
      env.apply(p, handle);
      set(p, { phase: "connected", run: run, handle: handle });
      env.later(function () { if (live(p, run, "connected")) set(p, { phase: "idle", run: ++seq }); }, 5000);
    }

    // While the window is open: ask the status every 4 s. It reads nothing from the platform; it learns that the
    // account is connected (another way) or that the window was closed without a sign-in.
    function poll(p, run, n) {
      env.later(function () {
        if (!live(p, run, "waiting")) return;
        env.call("room.account_status", { platforma: p }).then(function (res) {
          if (!live(p, run, "waiting")) return;
          var data = dataOf(res), list = (data && data.konta) || [], mine = null, k;
          for (k = 0; k < list.length; k++) if (list[k].platforma === p) { mine = list[k]; break; }
          if (mine && mine.stan === "polaczone") return connected(p, run, mine.konto);
          if (n + 1 >= TRIES) { set(p, { phase: "slow", run: run, handle: of(p).handle, href: of(p).href }); return; }
          poll(p, run, n + 1);
        });
      }, STEP);
    }

    // THE ONLY PLACE THE WINDOW IS OPENED. Asking and acting are one chain: the press is the person's.
    function begin(p, handle) {
      var st = of(p), ph = st.phase;
      if (ph === "asking" || ph === "opening" || ph === "checking" || ph === "connected") return;
      var run = ++seq, who = handle || st.handle;
      set(p, { phase: "asking", run: run, handle: who });
      env.call("room.connect_token", { platforma: p }).then(function (res) {
        if (!live(p, run, "asking")) return;
        var data = dataOf(res), token = metaOf(res, META_TOKEN);
        if (!res || res.error || !data) return refuse(p, run, who, null, res && res.error ? env.T("e.noConnect") : null);
        if (data.stan !== "WYKONANE") return refuse(p, run, who, data);
        if (!token) return refuse(p, run, who, null, env.T("ac.noToken"));
        set(p, { phase: "opening", run: run, handle: who });
        env.call("room.connect_account", { platforma: p, token: token }).then(function (res2) {
          if (!live(p, run, "opening")) return;
          var d2 = dataOf(res2), href = metaOf(res2, META_URL);
          if (!res2 || res2.error || !d2) return refuse(p, run, who, null, res2 && res2.error ? env.T("e.noConnect") : null);
          if (d2.stan !== "WYKONANE") return refuse(p, run, who, d2);
          if (!href) return refuse(p, run, who, null, env.T("ac.noWindow"));
          set(p, { phase: "waiting", run: run, handle: who, href: href });
          env.open(href);
          poll(p, run, 0);
        });
      });
    }

    // "I'm signed in" (or "Cancel", which says nothing when the sign-in is not there): close the window and
    // read the session once. `room.connect_done` opens nothing, so it needs no confirmation.
    function finish(p, quiet) {
      var st = of(p);
      if (st.phase !== "waiting" && st.phase !== "slow") return;
      var run = ++seq, who = st.handle;
      set(p, { phase: "checking", run: run, handle: who });
      env.call("room.connect_done", { platforma: p }).then(function (res) {
        if (!live(p, run, "checking")) return;
        var data = dataOf(res);
        if (!res || res.error || !data) return refuse(p, run, who, null, env.T("ac.unsure"));
        if (data.stan !== "WYKONANE") return refuse(p, run, who, data);
        if (data.polaczone) return connected(p, run, data.konto || who);
        if (quiet) return set(p, { phase: "idle", run: ++seq });
        set(p, { phase: "notyet", run: run, handle: who, code: data.kod_odczytu,
          reason: env.reason({ kod: data.kod_odczytu, powod: data.detail }, null) });
      });
    }

    // The window is open already (the card was drawn again while it was): wait for it, without opening anything.
    function resume(p, handle) {
      if (of(p).phase !== "idle") return;
      var run = ++seq;
      set(p, { phase: "waiting", run: run, handle: handle });
      poll(p, run, 0);
    }

    function dispatch(name, p, handle) {
      if (name === "connect" || name === "again" || name === "retry") begin(p, handle);
      else if (name === "done") finish(p, false);
      else if (name === "cancel") {
        var ph = of(p).phase;
        if (ph === "waiting" || ph === "slow") finish(p, true); else set(p, { phase: "idle", run: ++seq });
      } else if (name === "close") set(p, { phase: "idle", run: ++seq });
      else if (name === "resume") resume(p, handle);
    }
    return { of: of, dispatch: dispatch };
  }
  /*CONNECT-END*/

  var connector = makeConnector({
    call: callTool,
    later: function (fn, ms) { return setTimeout(fn, ms); },
    paint: function () { paintFoot(); },
    T: T,
    reason: reasonOf,
    open: function (url) { openLink(url); },
    apply: function (platform, handle) {
      var want = String(handle || "").toLowerCase().replace(/^@/, "");
      accounts.forEach(function (a) {
        var mine = String(a.konto || "").toLowerCase().replace(/^@/, "");
        if (a.platforma === platform && (!want || mine === want)) { a.stan = "polaczone"; a.mozna_polaczyc = false; a.powod = null; }
      });
      if (lastData) lastData.konta_stan = accounts;
    }
  });
  // The health and the Reconnect buttons need a local install and a host that can carry a tool call.
  function accOk() { return !!(accounts.length && !manual); }
  function platformName(p) { return p === "X" ? "X" : "Reddit"; }
  var STATE_WORDS = { polaczone: "ac.stOk", utracona: "ac.stLost", nigdy: "ac.stNever", inne_konto: "ac.stOther",
    wstrzymane: "ac.stHeld", nieznana: "ac.stUnknown", czeka_na_ciebie: "ac.stAwaiting" };
  // One chip per account: a dot and the name; where a click improves it, the whole chip is the button.
  function accountChips() {
    if (!accOk()) return "";
    return accounts.map(function (a) {
      var p = a.platforma, st = connector.of(p), who = handleOf(p, a.konto), s = String(a.stan || "nieznana");
      // v11: only an account that needs a person is in the footer; the healthy ones are in the Limits panel (`sessionDots`).
      if (s === "polaczone" || s === "nieznana") return "";
      if (st.phase !== "idle") return "";    // the panel above the footer speaks for this platform
      var word = T(STATE_WORDS[s] || "ac.stUnknown"), dot = '<i class="adot" aria-hidden="true"></i>';
      var attrs = ' data-p="' + esc(p) + '" data-h="' + esc(a.konto) + '"';
      if (canSend && a.mozna_polaczyc) {
        var label = s === "nigdy" ? "ac.connect" : "ac.reconnect";
        return '<button class="quiet acc" data-s="' + esc(s) + '" data-conn="connect"' + attrs + ' title="' + esc(word) + '">' + dot + esc(P(label, { who: who })) + '</button>';
      }
      if (canSend && s === "czeka_na_ciebie") {
        return '<button class="quiet acc" data-s="' + esc(s) + '" data-conn="resume"' + attrs + ' title="' + esc(word) + '">' + dot + esc(P("ac.finish", { who: who })) + '</button>';
      }
      return '<span class="acc" data-s="' + esc(s) + '" title="' + esc(word) + '">' + dot + esc(who) + '</span>';
    }).join("");
  }
  // The health of every account, as quiet words with a dot, for the Limits panel (reading only: Reconnect is in the footer, where
  // an account that needs it says so).
  function sessionDots() {
    if (!accOk()) return "";
    return accounts.map(function (a) {
      var s = String(a.stan || "nieznana");
      return '<span class="acc" data-s="' + esc(s) + '"><i class="adot" aria-hidden="true"></i>' + esc(handleOf(a.platforma, a.konto)) + ' \u00b7 ' + esc(T(STATE_WORDS[s] || "ac.stUnknown")) + '</span>';
    }).join("");
  }
  // The panel that holds the sign-in while it is under way: in the footer, in the place of nothing, so the card
  // only grows by what it says.
  function connectPanel(p, st) {
    var who = st.handle ? handleOf(p, st.handle) : platformName(p);
    function b(name, label, cls) {
      return '<button class="' + cls + '" data-conn="' + name + '" data-p="' + esc(p) + '"' + (st.handle ? ' data-h="' + esc(st.handle) + '"' : '') + '>' + esc(label) + '</button>';
    }
    function panel(html, actions) {
      return '<div class="conn" role="status" tabindex="-1" data-p="' + esc(st.phase) + '" data-for="' + esc(p) + '"><div class="msg">' + html + '</div>' +
        (actions ? '<div class="send-a">' + actions + '</div>' : '') + '</div>';
    }
    switch (st.phase) {
      case "asking": case "opening": return panel(pill("ac.opening", "wait"));
      case "waiting":
        return panel('<span class="wait">' + S("ac.waiting", { platform: platformName(p) }) + '</span><small>' + esc(T("ac.waitingNote")) + '</small>',
          b("done", T("ac.done"), "primary") + b("again", T("ac.again"), "soft") + b("cancel", T("ac.cancel"), "quiet"));
      case "slow":
        return panel(esc(T("ac.slow")), b("done", T("ac.done"), "primary") + b("again", T("ac.again"), "soft") + b("cancel", T("ac.cancel"), "quiet"));
      case "checking": return panel('<span class="pill"><span class="lbl wait">' + esc(P("ac.checking", { platform: platformName(p) })) + '</span></span>');
      case "connected":
        return panel('<span class="pill done"><span class="ok">' + ic("check") + '</span><span class="lbl">' + esc(P("ac.okNow", { who: who })) + '</span></span>');
      case "notyet": case "refused":
        return panel(esc(st.reason || T("e.noAnswer")), b("retry", T("ac.retry"), "primary") + b("close", T("ac.close"), "quiet"));
      default: return "";
    }
  }
  function connectPanels() {
    if (!accOk()) return "";
    var out = "", seen = {};
    accounts.forEach(function (a) {
      var p = a.platforma;
      if (seen[p]) return;
      seen[p] = true;
      var st = connector.of(p);
      if (st.phase !== "idle") out += connectPanel(p, st);
    });
    return out;
  }
  // The footer area is redrawn on its own while a sign-in goes on: the pages, the pager and any open
  // editor stay exactly as they were.
  function paintFoot() {
    var el = document.querySelector(".botx");
    if (!el) return;
    var had = !!(document.activeElement && el.contains(document.activeElement));
    el.innerHTML = connectPanels() + footer();
    // The button that was pressed is gone: the focus goes to the panel (which does nothing), never to a button,
    // so a key held down cannot reach "I'm signed in".
    var panel = el.querySelector(".conn");
    if (had && panel) { try { panel.focus({ preventScroll: true }); } catch (e) {} }
    fit(); resize();
  }
  function connectAction(name, platform, handle) {
    if (!accOk() || !canSend || (platform !== "X" && platform !== "REDDIT")) return;
    connector.dispatch(name, platform, handle || "");
  }

  // The limits and the pause, in the footer, for a local install that knows its scope.
  function scopeOk() { return !!(zakres && !manual && canSend); }
  // Which action fixes a refusal of a send, by its code (never by its sentence), or null.
  function scopeFix(code) {
    if (!scopeOk()) return null;
    if (code === "BUDZET") return { name: "raise", label: "sp.raise" };
    if (code === "ZAKRES_INNY_TRYB" || code === "ZAKRES" || code === "POZA_ZAKRESEM") return { name: "review", label: "sp.review" };
    if (code === "ZAKRES_WSTRZYMANY") return { name: "resume", label: "sp.resume" };
    return null;
  }
  function shortDay(iso) {
    if (!iso) return null; var t = new Date(iso); if (isNaN(t.getTime())) return null;
    try { return t.toLocaleDateString(LOCALE, { day: "numeric", month: "short", timeZone: TZ }); } catch (e) { return null; }
  }
  function untilOf(iso) { var d = shortDay(iso); return d ? ", " + P("sc.until", { until: d }) : ""; }
  // The pace of each account, short: "X @jack 6/h, 30/day; Reddit u/me 2/h, 10/day" (v11: limits are per account).
  function paceOf(rows) {
    return (rows || []).map(function (r) {
      return handleOf(r.platforma, r.konto) + " " + P("sc.perHour", { h: num(r.godzina) }) + ", " + P("sc.perDay", { d: num(r.doba) });
    }).join("; ");
  }
  // "2 accounts · 4/h · 20/day · until Oct 25"; with a pace per account: "2 accounts · pace set per account · until Oct 25"
  function scopeLine(z) {
    var l = z.limity || {}, n = (z.konta || []).length, until = shortDay(z.wygasa);
    var bits = [PL(n, "sc.accounts", { n: n })];
    if (z.wedlug_kont) bits.push(T("sc.eachOwn"));
    else {
      if (l.godzina != null) bits.push(P("sc.perHour", { h: num(l.godzina) }));
      if (l.doba != null) bits.push(P("sc.perDay", { d: num(l.doba) }));
    }
    if (until) bits.push(P("sc.until", { until: until }));
    return bits.join(" · ");
  }
  // v11: THE FOOTER IS QUIET. What a person must see is an account that needs them (a lost session) and a scope that is paused or
  // expired, each with its one button; everything else (the scope in a line, the sessions that are fine) is in the Limits panel,
  // and Pause and the full view are behind the small menu of the quiet line. "Nothing posts without your click" is said where a
  // person is asked to click (the confirmation); a hosted session, which never posts, keeps its own sentence here.
  function footer() {
    var z = scopeOk() ? zakres : null, ctl = "";
    if (z) {
      if (z.stan === "WSTRZYMANY") {
        ctl = '<span class="warn"><i class="pdot"></i>' + esc(T("sc.pausedMsg")) + '</span>' +
          '<button class="quiet" data-scope="resume">' + esc(T("sc.resume")) + '</button>';
      } else if (z.stan === "WYGASL") {
        ctl = '<span class="warn"><i class="pdot"></i>' + esc(T("sc.expiredMsg")) + '</span>' +
          '<button class="quiet" data-scope="renew">' + esc(T("sc.renew")) + '</button>';
      }
    }
    var out = (manual ? '<span class="promise">' + esc(T("rd.footManual")) + '</span>' : '') + accountChips() + ctl;
    return out ? '<div class="foot">' + out + '</div>' : '';
  }
  // The small menu: what a person rarely needs.
  function menuItems() {
    var z = scopeOk() ? zakres : null, out = "";
    if (z && z.stan === "AKTYWNY") out += '<button data-scope="pause">' + ic("pause") + esc(T("sc.pause")) + '</button>';
    if (!manual && web(room)) out += '<button data-room="1">' + ic("out") + esc(T("ft.full")) + '</button>';
    return out;
  }
  // "Sent 9 · Skipped 18 · Limits" and the three dots: ONE quiet line under the pager.
  function quietLine() {
    var bits = [], z = scopeOk() ? zakres : null;
    function tog(name, label) {
      return '<button class="qb" data-tog="' + name + '" aria-expanded="' + (drawer === name ? "true" : "false") + '">' + esc(label) + '</button>';
    }
    if (sent.length > 0) bits.push(tog("sent", P("ql.sent", { n: sent.length })));
    if (skippedTotal > 0) bits.push(tog("skipped", P("ql.skipped", { n: skippedTotal })));
    else if (hidden > 0) bits.push(tog("skipped", P("ql.behind", { n: hidden })));
    if (batcher.tracked().length > 0) bits.push(tog("scheduled", P("ql.scheduled", { n: batcher.tracked().length })));
    if (z && (z.stan === "AKTYWNY" || z.stan === "BRAK")) {
      bits.push('<button class="qb" data-scope="panel">' + esc(T(z.stan === "AKTYWNY" ? "ql.limits" : "sc.setLimits")) + '</button>');
    }
    var items = menuItems();
    if (!bits.length && !items) return "";
    return '<div class="ql">' + bits.join('<span class="sep" aria-hidden="true">·</span>') + '<span class="gap"></span>' +
      (items ? '<button class="qb mn" data-menu="1" aria-label="' + esc(T("ql.more")) + '" aria-expanded="' + (menuOpen ? "true" : "false") + '">⋯</button>' : '') + '</div>' +
      (items ? '<div class="menu"' + (menuOpen ? ' data-open="1"' : '') + '>' + items + '</div>' : '');
  }
  function closeMenu() {
    if (!menuOpen) return;
    menuOpen = false;
    var m = document.querySelector(".menu"), t = document.querySelector("[data-menu]");
    if (m) m.removeAttribute("data-open");
    if (t) t.setAttribute("aria-expanded", "false");
    resize();
  }

  // The form of the Limits panel, prefilled from the scope the server reported. Never sent as such:
  // `room.scope_token` validates every id against the project's own accounts.
  var ACTS = ["REPLY", "OWN_POST", "RESEARCH"];
  function formFor(mode) {
    var z = zakres || {};
    var def = z.domyslne || { godzina: 2, doba: 10, wlasne_doba: 1, dni: 7 };
    var cap = z.granice || { godzina: 10, doba: 50, wlasne_doba: 10 };
    var cur = z.stan && z.stan !== "BRAK" && z.limity ? z.limity : null;
    var base = cur || def;
    var h = base.godzina, d = base.doba, o = base.wlasne_doba;
    // A refusal for the budget opens the panel with the pace raised, so "Save" is one honest step.
    function raise(n, top, step) { return Math.min(top, n + step); }
    if (mode === "raise") { h = raise(h, cap.godzina, 1); d = raise(d, cap.doba, Math.max(4, Math.ceil(d / 2))); }
    // v11: THE PACE IS PER ACCOUNT. Each account opens on what binds it now (the server's `limity` for an account in
    // the scope: its own number, its platform's or the project's), else on the card's defaults.
    var perAcc = {};
    (z.do_wyboru || []).forEach(function (a) {
      if (!(a.dzialania || []).length) return;
      var src = a.w_zakresie && a.limity && z.stan && z.stan !== "BRAK" ? a.limity : base;
      var ah = src.godzina, ad = src.doba, ao = src.wlasne_doba;
      if (mode === "raise") { ah = raise(ah, cap.godzina, 1); ad = raise(ad, cap.doba, Math.max(4, Math.ceil(ad / 2))); }
      perAcc[a.id] = { godzina: ah, doba: ad, wlasne_doba: ao };
    });
    return {
      konta: (z.do_wyboru || []).filter(function (a) { return (a.dzialania || []).length > 0; }).map(function (a) { return a.id; }),
      dzialania: ACTS.slice(),
      limity: { godzina: h, doba: d, wlasne_doba: o },
      limity_konta: perAcc,
      dni: 7
    };
  }
  function readForm() {
    var f = { konta: [], dzialania: [], limity: {}, limity_konta: {}, dni: 7 };
    var all = document.querySelectorAll("[data-sc]"), k, el;
    for (k = 0; k < all.length; k++) {
      el = all[k];
      var kind = el.getAttribute("data-sc");
      if (kind === "acc" && el.checked) f.konta.push(el.value);
      else if (kind === "act" && el.checked) f.dzialania.push(el.value);
      else if (kind === "dni" && el.checked) f.dni = Number(el.value);
      else if (kind === "godzina" || kind === "doba" || kind === "wlasne_doba") {
        var n = parseInt(el.value, 10), acc = el.getAttribute("data-acc");
        if (acc) { (f.limity_konta[acc] = f.limity_konta[acc] || {})[kind] = isNaN(n) ? 0 : n; }
        else f.limity[kind] = isNaN(n) ? 0 : n;
      }
    }
    // A number for an account that is not ticked is not asked about (the server refuses a limit for nobody).
    Object.keys(f.limity_konta).forEach(function (id) { if (f.konta.indexOf(id) < 0) delete f.limity_konta[id]; });
    return f;
  }
  function namesHtml(list) {
    var names = (list || []).map(function (a) { return '<b>' + esc(handleOf(a.platforma, a.konto)) + '</b>'; });
    if (!names.length) return "";
    return names.length === 1 ? names[0] : names.slice(0, -1).join(", ") + esc(T("c.and")) + names[names.length - 1];
  }
  function sheetBody(st) {
    var plan = st.plan || {}, i;
    switch (st.phase) {
      case "panel": {
        var z = zakres || {}, f = scForm || formFor("panel");
        var accs = (z.do_wyboru || []).map(function (a) {
          var ok = (a.dzialania || []).length > 0, on = ok && f.konta.indexOf(a.id) >= 0;
          return '<label class="chip"><input type="checkbox" data-sc="acc" value="' + esc(a.id) + '"' + (on ? " checked" : "") + (ok ? "" : " disabled") +
            '><span>' + esc(handleOf(a.platforma, a.konto)) + '</span></label>';
        }).join("");
        var acts = ACTS.map(function (a) {
          return '<label class="chip"><input type="checkbox" data-sc="act" value="' + a + '"' + (f.dzialania.indexOf(a) >= 0 ? " checked" : "") +
            '><span>' + esc(L("sc.act." + a, a)) + '</span></label>';
        }).join("");
        var cap = z.granice || { godzina: 10, doba: 50, wlasne_doba: 10 };
        // v11: one block per account that can publish, with ITS three numbers; an unticked account's block is dimmed
        // and its fields are off (the click handler keeps them in step with the ticks).
        var pace = (z.do_wyboru || []).filter(function (a) { return (a.dzialania || []).length > 0; }).map(function (a) {
          var mine = (f.limity_konta || {})[a.id] || f.limity || {}, on = f.konta.indexOf(a.id) >= 0;
          var nfa = function (key, label) {
            return '<label class="nf"><span>' + esc(T(label)) + '</span><input class="num" type="number" inputmode="numeric" min="1" max="' + cap[key] +
              '" data-sc="' + key + '" data-acc="' + esc(a.id) + '" value="' + esc(mine[key]) + '"' + (on ? "" : " disabled") + '></label>';
          };
          return '<div class="pa' + (on ? "" : " off") + '" data-pa="' + esc(a.id) + '"><span class="pa-who">' + esc(handleOf(a.platforma, a.konto)) + '</span>' +
            '<div class="pa-n">' + nfa("godzina", "sc.hour") + nfa("doba", "sc.day") + nfa("wlasne_doba", "sc.own") + '</div></div>';
        }).join("");
        var terms = [7, 14].map(function (d) {
          return '<label class="chip"><input type="radio" name="sc-dni" data-sc="dni" value="' + d + '"' + (f.dni === d ? " checked" : "") +
            '><span>' + esc(P("sc.days", { d: d })) + '</span></label>';
        }).join("");
        var nowLine = z.stan && z.stan !== "BRAK" ? '<span class="lab">' + esc(T("sc.nowLabel")) + '</span><div class="ctl"><span class="nowl">' + esc(scopeLine(z)) + '</span></div>' : '';
        var dots = sessionDots();
        return '<div class="sf">' + nowLine + (dots ? '<span class="lab">' + esc(T("sc.sessionsLabel")) + '</span><div class="ctl sess">' + dots + '</div>' : '') +
          '<span class="lab">' + esc(T("sc.accountsLabel")) + '</span><div class="ctl">' + accs + '</div>' +
          '<span class="lab">' + esc(T("sc.actionsLabel")) + '</span><div class="ctl">' + acts + '</div>' +
          '<span class="lab">' + esc(T("sc.paceLabel")) + '</span><div class="ctl pace">' + pace + '</div>' +
          '<span class="lab">' + esc(T("sc.term")) + '</span><div class="ctl">' + terms + '</div></div>' +
          '<p class="sh-note">' + esc(T("sc.paceNote")) + ' ' + esc(T("sc.safe")) + '</p>' +
          (scErr ? '<p class="sh-err" role="alert">' + esc(T(scErr === "account" ? "sc.errAccount" : "sc.errAction")) + '</p>' : '') +
          '<div class="sh-a"><button class="primary" data-scact="save">' + esc(T("sc.save")) + '</button>' +
          '<button class="quiet" data-scact="cancel">' + esc(T("sc.cancel")) + '</button></div>';
      }
      case "checking": return '<p class="sh-msg">' + pill("sc.checking", "wait") + '</p>';
      case "waiting":
        return '<p class="sh-msg"><span class="wait">' + esc(T("sp.waiting")) + '</span><small>' + esc(T("sc.waitingNote")) + '</small></p>' +
          '<div class="sh-a"><button class="quiet" data-scact="cancel">' + esc(T("sc.cancel")) + '</button></div>';
      case "working": return '<p class="sh-msg">' + pill("sc.working", "wait") + '</p>';
      case "confirm": {
        var msg;
        if (st.kind === "pause") msg = esc(T("sc.cPause"));
        else if (st.kind === "resume") msg = S(plan.tryb === "AUTONOMOUS" ? "sc.cResumeAuto" : "sc.cResume", { accounts: namesHtml(plan.konta), until: untilOf(plan.wygasa) }, ["accounts"]);
        else if ((plan.limity_kont || []).length) {
          // v11: a pace for EACH account, told account by account, so nothing is signed that was not on the screen.
          var own = function (n) { return fmt(plural(Number(n) || 0, T("sc.ownN")), { n: num(n) }, true); };
          msg = S("sc.cSignAcct", {
            accounts: namesHtml(plan.konta),
            actions: (plan.dzialania || []).map(function (a) { return L("sc.act." + a, a); }).join(", "),
            until: untilOf(plan.wygasa)
          }, ["accounts"]) + '<ul class="pl">' + plan.limity_kont.map(function (r) {
            return '<li>' + esc(P("sc.paceRow", { who: handleOf(r.platforma, r.konto), h: num(r.godzina), d: num(r.doba), own: own(r.wlasne_doba) })) + '</li>';
          }).join("") + '</ul>' + (plan.poprzedni_tryb === "AUTONOMOUS" ? '<small>' + esc(T("sc.cWasAuto")) + '</small>' : '');
        } else {
          var l = plan.limity || {};
          msg = S("sc.cSign", {
            accounts: namesHtml(plan.konta),
            actions: (plan.dzialania || []).map(function (a) { return L("sc.act." + a, a); }).join(", "),
            h: num(l.godzina), d: num(l.doba), until: untilOf(plan.wygasa),
            own: fmt(plural(Number(l.wlasne_doba) || 0, T("sc.ownN")), { n: num(l.wlasne_doba) }, true)
          }, ["accounts"]) + (plan.poprzedni_tryb === "AUTONOMOUS" ? '<small>' + esc(T("sc.cWasAuto")) + '</small>' : '');
        }
        var yes = st.kind === "pause" ? "sc.yesPause" : st.kind === "resume" ? "sc.yesResume" : "sc.yesSign";
        return '<p class="sh-msg">' + msg + '</p><div class="sh-a"><button class="primary" data-scact="go">' + esc(T(yes)) + '</button>' +
          (st.kind === "sign" ? '<button class="soft" data-scact="back">' + esc(T("sc.back")) + '</button>' : '') +
          '<button class="quiet" data-scact="cancel">' + esc(T("sc.cancel")) + '</button></div>';
      }
      case "done": {
        var said = st.kind === "pause" ? "sc.donePause" : st.kind === "resume" ? "sc.doneResume" : "sc.doneSign";
        return '<p class="sh-msg"><span class="pill done"><span class="ok">' + ic("check") + '</span><span class="lbl">' + esc(T(said)) + '</span></span></p>' +
          '<div class="sh-a"><button class="primary" data-scact="done">' + esc(T("sc.done")) + '</button></div>';
      }
      case "refused":
        return '<p class="sh-msg">' + esc(st.reason || T("sc.refusedDefault")) + '</p><div class="sh-a"><button class="primary" data-scact="retry">' + esc(T("sc.retry")) +
          '</button><button class="quiet" data-scact="close">' + esc(T("sc.close")) + '</button></div>';
      default: return "";
    }
  }
  function sheet() {
    var st = scoper.state();
    return '<section class="sheet" role="group" aria-label="' + esc(T("sc.title")) + '" data-p="' + esc(st.phase) + '">' +
      (st.phase === "idle" ? "" : '<div class="sh-title">' + esc(T("sc.title")) + '</div>' + sheetBody(st)) + '</section>';
  }
  // The sheet takes the place of the pages, the pager and the footer while it is open, and keeps
  // the height they had, so the card does not shrink and grow back.
  function sheetKind() { return scoper.state().phase !== "idle" ? "1" : batcher.state().phase !== "idle" ? "batch" : ""; }
  function syncSheet() {
    var app = document.getElementById("app"), kind = sheetKind(), all = app.querySelectorAll(".sheet"), k;
    var el = app.querySelector(kind === "batch" ? ".sheet.batch" : ".sheet:not(.batch)");
    if (!kind) { app.removeAttribute("data-sheet"); for (k = 0; k < all.length; k++) all[k].style.minHeight = ""; return; }
    if (!el) return;
    if (app.getAttribute("data-sheet") !== kind) {
      var before = app.offsetHeight;
      app.setAttribute("data-sheet", kind);
      var after = app.offsetHeight;
      if (before > after) el.style.minHeight = (el.offsetHeight + before - after) + "px";
    }
  }
  function paintSheet() {
    var app = document.getElementById("app"), el = app.querySelector(".sheet:not(.batch)"), st = scoper.state();
    if (!el) return;
    if (st.phase === "idle") { render(lastData); return; }   // the card draws again, with the scope as it is now
    var had = !!(document.activeElement && el.contains(document.activeElement));
    el.setAttribute("data-p", st.phase);
    var prev = grab(el);
    el.innerHTML = '<div class="sh-title">' + esc(T("sc.title")) + '</div>' + sheetBody(st);
    morph(prev, el);
    if (st.phase === "confirm" || st.phase === "done") { shine(el); beam(el); }
    syncSheet();
    // Focus goes to the sheet's first control or to the sheet itself: a key held down on the button that
    // was pressed must never reach "Yes".
    var first = st.phase === "panel" ? el.querySelector("input:not([disabled])") : null;
    try { if (first) first.focus({ preventScroll: true }); else if (had) { el.setAttribute("tabindex", "-1"); el.focus({ preventScroll: true }); } } catch (e) {}
    fit(); resize();
  }
  function scopeAction(name) {
    if (!scopeOk()) return;
    if (/^(checking|confirm|refused|done)$/.test(batcher.state().phase)) batcher.dispatch("close");   // the Limits panel takes the batch panel's place
    if (name === "pause" || name === "resume") { scoper.dispatch(name); return; }
    scForm = formFor(name); scErr = null;
    scoper.dispatch("panel", scForm);
  }
  function scopeButton(name) {
    var st = scoper.state();
    if (name === "save") {
      var f = readForm(); scForm = f;
      if (!f.konta.length) { scErr = "account"; paintSheet(); return; }
      if (!f.dzialania.length) { scErr = "action"; paintSheet(); return; }
      scErr = null; scoper.dispatch("save", f); return;
    }
    if (name === "back" || name === "retry") { scErr = null; }
    scoper.dispatch(name);
  }

  // ---- SEND ALL (v11): the button in the pager row, the confirm panel, the list of what was scheduled.
  function draftOf(rev) {
    var all = (lastData && lastData.szkice) || [], k;
    for (k = 0; k < all.length; k++) if (all[k].revision_id === rev) return all[k];
    return null;
  }
  // The drafts "Send all" may take, as the card sees them: waiting for approval, not expired, a digest in hand
  // (`draftState`), in a live signed scope that lists the account and the action, and not being edited, sent or
  // scheduled already. The server decides again from its rows; this only keeps the card from asking about what
  // cannot go.
  function batchCands() {
    if (!canSend || manual || !zakres || zakres.stan !== "AKTYWNY") return [];
    var inScope = {}, actions = {};
    (zakres.konta || []).forEach(function (a) { inScope[a.platforma + ":" + a.konto] = true; });
    (zakres.dzialania || []).forEach(function (a) { actions[a] = true; });
    return ((lastData && lastData.szkice) || []).filter(function (d) {
      return d.stan === "czeka_na_zgode" && draftState(d).able && !batched[d.revision_id] && !edits[d.revision_id] &&
        sendOf(d).phase === "idle" && inScope[d.platforma + ":" + d.konto] && actions[d.rodzaj === "OWN_POST" ? "OWN_POST" : "REPLY"];
    }).map(function (d) { return { rev: d.revision_id, odcisk: d.odcisk }; });
  }
  function batchButton() {
    var n = batchCands().length;
    return n >= 2 ? '<button class="sall" data-ba="open">' + esc(P("ba.open", { n: n })) + '</button>' : "";
  }
  // The count follows a draft that goes into an editor or a send of its own, and comes back: the button is
  // updated in place (or appears, or goes) without drawing the card again.
  function syncBatchButton() {
    var bar = document.querySelector(".bar"); if (!bar) return;
    var n = batchCands().length, b = bar.querySelector(".sall");
    if (n >= 2) {
      if (!b) {
        b = document.createElement("button"); b.className = "sall"; b.setAttribute("data-ba", "open");
        var dis = bar.querySelector(".dis"); if (dis) bar.insertBefore(b, dis); else bar.appendChild(b);
      }
      b.textContent = P("ba.open", { n: n });
    } else if (b) b.parentNode.removeChild(b);
  }
  function firstLine(text) {
    var lines = String(text || "").split(/\r?\n/), k;
    for (k = 0; k < lines.length; k++) if (lines[k].trim()) return lines[k].trim();
    return "";
  }
  // A moment, in Warsaw time like every deadline of the card: the time of day, with the date when it is not today.
  function timeOf(iso) {
    var t = new Date(iso); if (isNaN(t.getTime())) return "";
    var hour = LANG === "pl" ? "2-digit" : "numeric", today = false;
    try { today = t.toLocaleDateString("en-CA", { timeZone: TZ }) === new Date().toLocaleDateString("en-CA", { timeZone: TZ }); } catch (e) {}
    try {
      if (today) return t.toLocaleTimeString(LOCALE, { hour: hour, minute: "2-digit", timeZone: TZ });
      return t.toLocaleString(LOCALE, { day: "numeric", month: "short", hour: hour, minute: "2-digit", timeZone: TZ });
    } catch (e) {}
    return ("0" + t.getHours()).slice(-2) + ":" + ("0" + t.getMinutes()).slice(-2);
  }
  // "4 now, then one every 15 min, done by 14:45": the numbers are the server's, the sentence is the card's.
  function scheduleLine(plan) {
    var n = Number(plan.ile) || 0, now = Number(plan.teraz) || 0, gap = Number(plan.odstep_min) || 0;
    var end = plan.koniec ? timeOf(plan.koniec) : "", first = plan.pozycje && plan.pozycje[0] ? timeOf(plan.pozycje[0].kiedy) : "";
    if (now >= n) return P("ba.schNow", { end: end });
    if (now < 1) return P("ba.schLater", { first: first, gap: gap, end: end });
    return P("ba.sch", { now: now, gap: gap, end: end });
  }
  function whoLine(d) {
    var own = d.rodzaj === "OWN_POST";
    var author = !own && d.w_odpowiedzi_na && d.w_odpowiedzi_na.autor ? whoOf(d.platforma, d.w_odpowiedzi_na.autor) : null;
    return own ? T("ba.ownPost") : (author || T("ba.reply"));
  }
  function batchRows(st, at) {
    var rows = (st.cands || []).map(function (c) {
      var d = draftOf(c.rev); if (!d) return "";
      var ex = st.excl && st.excl[c.rev], on = !ex && !!st.picks[c.rev], who = whoLine(d), line = firstLine(d.tekst);
      var where = d.platforma === "X" ? "X" : communityOf("REDDIT", d.spolecznosc);
      var second = ex ? '<span class="b1 bx">' + esc(reasonOf({ kod: ex.code, powod: ex.reason }, null)) + '</span>' : '<span class="b1">' + esc(line) + '</span>';
      var right = !ex && on && at[c.rev] ? '<span class="bk">' + esc(timeOf(at[c.rev])) + '</span>' : '';
      return '<li><label class="br" data-on="' + (ex ? "x" : on ? "1" : "0") + '">' +
        '<input type="checkbox" data-ba="pick" value="' + esc(c.rev) + '"' + (on ? " checked" : "") + (ex ? " disabled" : "") +
        ' aria-label="' + esc(P("ba.pick", { what: who + ": " + line })) + '">' +
        '<span class="badge">' + (d.platforma === "X" ? "X" : "R") + '</span>' +
        '<span class="bt"><span class="bh"><b>' + esc(who) + '</b><span class="bp">' + esc(where) + '</span></span>' + second + '</span>' + right + '</label></li>';
    }).join("");
    return '<ul class="bl">' + rows + '</ul>';
  }
  // Why a scheduled request did not go out, or is waiting: Polish shows the publisher's own sentence, another
  // language the card's sentence for its code (and the Polish one when the code is not known).
  function trackedReason(t) {
    var mine = t.code ? L("rs." + t.code, null) : null, server = t.reason;
    return (LANG === "pl" ? server || mine : mine || server) || "";
  }
  function trackedState(t) {
    var status = String(t.status || "QUEUED"), word, reason = "", phase = "queued", time = timeOf(t.at), sub;
    if (status === "PUBLISHED") { word = T("ba.st.PUBLISHED"); phase = "published"; }
    else if (status === "QUEUED") { word = T(t.reason ? "ba.st.waiting" : "ba.st.QUEUED"); reason = t.reason ? trackedReason(t) : ""; }
    else if (status === "PUBLISHING") word = T("ba.st.PUBLISHING");
    else if (status === "UNKNOWN") { word = T("ba.st.UNKNOWN"); reason = T("e.unknown"); phase = "failed"; }
    else { word = L("ba.st." + status, status); reason = trackedReason(t) || T("ba.noReason"); phase = "failed"; }
    sub = esc(word) + (status === "QUEUED" && time ? " · " + esc(P(t.reason ? "ba.next" : "ba.at", { time: time })) : "");
    return { word: word, sub: sub, reason: reason, phase: phase, time: time, url: web(t.url) ? t.url : null };
  }
  function scheduledRows() {
    var rows = batcher.tracked().map(function (t) {
      var d = draftOf(t.rev), s = trackedState(t), line = d ? firstLine(d.tekst) : "";
      return '<li class="rw"><span class="badge">' + (t.platform === "X" ? "X" : "R") + '</span><div class="rb"><div class="t">' +
        esc((d ? whoLine(d) : T("ba.reply")) + (line ? " · " + line : "")) + '</div><div class="s">' + s.sub + '</div>' +
        (s.reason ? '<div class="w1" title="' + esc(s.reason) + '">' + esc(s.reason) + '</div>' : '') + '</div>' +
        (s.url ? '<button class="lnk" data-ba="live" data-rev="' + esc(t.rev) + '" aria-label="' + esc(T("ba.view")) + '">' + ic("out") + '</button>' : '') + '</li>';
    }).join("");
    return '<ul class="rows bsl">' + rows + '</ul>';
  }
  // What the page of a scheduled draft says, in the place of the buttons.
  function batchedBox(t, joined) {
    var s = trackedState(t), cl = "cl" + (joined ? "" : " solo");
    if (s.phase === "published") {
      return box('<span class="pill done"><span class="ok">' + ic("check") + '</span><span class="lbl">' + esc(T("sp.posted")) + '</span></span>', "published",
        s.url ? '<button class="primary" data-ba="live" data-rev="' + esc(t.rev) + '">' + esc(T("sp.view")) + '</button>' : "", cl);
    }
    if (s.phase === "failed") return box('<b>' + esc(T("sp.failed")) + '</b> <span class="ex">' + esc(s.reason) + '</span>', "failed", "", cl);
    return box('<b>' + esc(P("ba.pageHead", { time: s.time })) + '</b> <span class="ex">' + esc(T(t.reason ? "ba.pageWait" : "ba.pageNote")) +
      (t.reason ? ' ' + esc(s.reason) : '') + '</span>', "queued", "", cl);
  }

  function batchSheetBody(st) {
    var plan = st.plan || null, at = {}, k, p;
    if (plan) for (k = 0; k < (plan.pozycje || []).length; k++) { p = plan.pozycje[k]; at[p.revision_id] = p.kiedy; }
    switch (st.phase) {
      case "checking": case "confirm": {
        var shown = (st.cands || []).filter(function (c) { return st.picks[c.rev] && !st.excl[c.rev]; }).length;
        var n = plan ? (plan.pozycje || []).length : 0, ready = st.phase === "confirm" && !!st.token && n > 0, count = ready ? n : shown;
        var info = st.phase === "checking"
          ? '<p class="bs"><span class="wait">' + esc(T("ba.planning")) + '</span></p>'
          : plan
            ? '<p class="bs">' + esc(scheduleLine(plan)) + '<small>' + esc((plan.limity || {}).konta && plan.limity.konta.length
              ? P("ba.limitsAcct", { pace: paceOf(plan.limity.konta) })
              : P("ba.limits", { h: num((plan.limity || {}).godzina), d: num((plan.limity || {}).doba) })) + '</small>' +
              '<small>' + esc(T("ba.safe")) + '</small></p>'
            : '<p class="bs">' + esc(T("ba.nothing")) + '</p>';
        var yes = shown > 0 ? '<button class="primary" data-ba="go"' + (ready ? "" : " disabled") + '>' + esc(fmt(plural(count, T("ba.yes")), { n: count }, true)) + '</button>' : "";
        return '<p class="sh-msg">' + esc(T("ba.intro")) + '</p>' +
          (st.changed ? '<p class="sh-note bchg">' + esc(T("ba.changed")) + '</p>' : '') + batchRows(st, at) + info +
          '<div class="sh-a">' + yes + '<button class="quiet" data-ba="cancel">' + esc(T("ba.cancel")) + '</button></div>';
      }
      case "sending": return '<p class="sh-msg">' + pill("ba.sending", "wait") + '</p>';
      case "done": {
        var left = (st.no || []).map(function (o) {
          return '<small class="bchg">' + esc(reasonOf({ kod: o.kod, powod: o.powod }, null)) + '</small>';
        }).join("");
        return '<p class="sh-msg"><span class="pill done"><span class="ok">' + ic("check") + '</span><span class="lbl">' + esc(P("ba.okLine", { n: st.sentN })) + '</span></span>' +
          ((st.no || []).length ? ' ' + esc(P("ba.noLine", { n: st.no.length })) + left : '') + '<small>' + esc(T("ba.schedNote")) + '</small></p>' +
          '<div class="sh-title">' + esc(T("ba.schedTitle")) + '</div>' + scheduledRows() +
          (batcher.resting() ? '<p class="sh-note">' + esc(T("ba.rested")) + '</p>' : '') +
          '<div class="sh-a"><button class="primary" data-ba="done">' + esc(T("ba.done")) + '</button>' +
          (batcher.resting() ? '<button class="soft" data-ba="refresh">' + esc(T("ba.recheck")) + '</button>' : '') + '</div>';
      }
      case "refused": {
        var fix = scopeFix(st.code), lead = fix
          ? '<button class="primary" data-scope="' + fix.name + '">' + esc(T(fix.label)) + '</button>'
          : (web(st.presence) ? '<button class="primary" data-ba="presence">' + esc(T("sp.presence")) + '</button>' : "");
        var hasRows = st.cands && Object.keys(st.excl || {}).length > 0;
        return '<p class="sh-msg">' + esc(st.reason || T("sp.refusedDefault")) + '</p>' + (hasRows ? batchRows(st, {}) : '') +
          '<div class="sh-a">' + lead + (st.cands ? '<button class="' + (lead ? "soft" : "primary") + '" data-ba="retry">' + esc(T("ba.retry")) + '</button>' : '') +
          '<button class="quiet" data-ba="close">' + esc(T("ba.close")) + '</button></div>';
      }
      default: return "";
    }
  }
  function batchSheet() {
    var st = batcher.state();
    return '<section class="sheet batch" role="group" aria-label="' + esc(T("ba.title")) + '" data-p="' + esc(st.phase) + '">' +
      (st.phase === "idle" ? "" : '<div class="sh-title">' + esc(T("ba.title")) + '</div>' + batchSheetBody(st)) + '</section>';
  }
  var batchBeamed = false;
  function paintBatch() {
    var app = document.getElementById("app"), el = app.querySelector(".sheet.batch"), st = batcher.state();
    if (!el) return;
    if (st.phase === "idle") { batchBeamed = false; render(lastData); return; }   // the card draws again, with the new count and list
    var focused = document.activeElement, keepRev = focused && focused.matches && focused.matches('[data-ba="pick"]') ? focused.value : null;
    var had = !!(focused && el.contains(focused));
    el.setAttribute("data-p", st.phase);
    var prev = grab(el);
    el.innerHTML = '<div class="sh-title">' + esc(T("ba.title")) + '</div>' + batchSheetBody(st);
    morph(prev, el);
    if (st.phase === "done" || (st.phase === "confirm" && !batchBeamed)) { shine(el); beam(el); batchBeamed = batchBeamed || st.phase === "confirm"; }
    syncSheet();
    // Focus goes back to the box that was ticked, else to the sheet itself: a key held down on the button that
    // was pressed must never reach "Yes".
    try {
      var again = keepRev ? el.querySelector('[data-ba="pick"][value="' + keepRev + '"]:not([disabled])') : null;
      if (again) again.focus({ preventScroll: true });
      else if (had) { el.setAttribute("tabindex", "-1"); el.focus({ preventScroll: true }); }
    } catch (e) {}
    fit(); resize();
  }
  // A scheduled draft's page says where it stands.
  function paintBatched(rev) {
    var el = document.querySelector('[data-box="' + rev + '"]'), d = draftOf(rev);
    if (!el || !d) return;
    el.setAttribute("data-phase", "queued");
    el.style.minHeight = "";
    el.innerHTML = regionInner(d, -1);
  }
  // The statuses changed: the list under the pager, the pages of the scheduled drafts, and the panel if it is open.
  function paintTracked() {
    var dr = document.querySelector('.dr[data-dr="scheduled"]');
    if (dr) dr.innerHTML = scheduledRows();
    batcher.tracked().forEach(function (t) { paintBatched(t.rev); });
    if (batcher.state().phase === "done") paintBatch();
    resize();
  }
  function batchAction(name, rev) {
    if (name === "live") { var t = batched[rev]; if (t && web(t.url)) openLink(t.url); return; }
    if (name === "presence") { openLink(batcher.state().presence); return; }
    if (name === "open") {
      if (!canSend || manual) return;
      var c = batchCands(); if (c.length >= 2) batcher.dispatch("open", c);
      return;
    }
    batcher.dispatch(name, rev);
  }

  // ---- The disclosures under the pager: threads the agent skipped, replies that went out.
  function dtog(name, label) {
    return '<button class="dtog" data-tog="' + name + '" aria-expanded="' + (drawer === name ? "true" : "false") + '">' + esc(label) + ic("chevD") + '</button>';
  }
  function disclosures() {
    var out = "";
    if (skippedTotal > 0) out += dtog("skipped", P("sk.label", { n: skippedTotal }));
    if (sent.length > 0) out += dtog("sent", P("sn.label", { n: sent.length }));
    if (batcher.tracked().length > 0) out += dtog("scheduled", P("ba.sched", { n: batcher.tracked().length }));
    return out ? '<div class="dis">' + out + '</div>' : "";
  }
  function whereOf(o) {
    var reddit = o.platforma === "REDDIT";
    return o.spolecznosc ? (reddit ? "r/" + String(o.spolecznosc).replace(/^r\//, "") : o.spolecznosc) : (reddit ? "Reddit" : "X");
  }
  function skippedRows() {
    var rows = skipped.map(function (o, k) {
      var why = o.przez === "czlowiek" ? T("sk.byYou")
        : (o.przez === "system" && LANG !== "pl" ? L("rs." + o.powod_kod, o.powod) : o.powod) || T("sk.noReason");
      var when = ago(o.pominieto);
      return '<li class="rw"><span class="badge">' + (o.platforma === "REDDIT" ? "R" : "X") + '</span><div class="rb"><div class="t">' + esc(o.tytul || whereOf(o)) +
        '</div><div class="s">' + esc(whereOf(o)) + (when ? ' · ' + esc(when) : '') + '</div><div class="w1" title="' + esc(why) + '">' + esc(why) + '</div></div>' +
        (web(o.url) ? '<button class="lnk" data-skopen="' + k + '" aria-label="' + esc(T("sk.open")) + '">' + ic("out") + '</button>' : '') + '</li>';
    }).join("");
    var more = skippedTotal > skipped.length ? '<li class="rmore">' + esc(P("sk.more", { n: skippedTotal - skipped.length })) + '</li>' : '';
    var note = hidden > 0 ? '<li class="rnote">' + esc(P("hid.note", { n: hidden })) + '</li>' : '';
    return '<ul class="rows">' + note + rows + more + '</ul>';
  }
  function sentRows() {
    var rows = sent.map(function (r, k) {
      var o = r.okazja_id ? oppById[r.okazja_id] : null, reddit = r.platforma === "REDDIT";
      var first = String(r.tekst || "").replace(/\s+/g, " ").trim();
      var title = (o && o.tytul) || first || whereOf(r);
      var when = ago(r.opublikowano);
      var seen = L("seen." + r.widocznosc, T("seen.unconfirmed"));
      var nums = '<span>' + esc(T(reddit ? "pub.votes" : "pub.likes")) + ' ' + figure(r.glosy) + '</span><span>' + esc(T("pub.replies")) + ' ' + figure(r.odpowiedzi) + '</span><span>' +
        (r.widocznosc === "CONFIRMED" || r.widocznosc === "REMOVED" ? '<b>' + esc(seen) + '</b>' : '<i>' + esc(seen) + '</i>') + '</span>';
      return '<li class="rw"><span class="badge">' + (reddit ? "R" : "X") + '</span><div class="rb"><div class="t">' + esc(title) + '</div><div class="s">' +
        esc(o ? whereOf(o) : whereOf(r)) + (when ? ' · ' + esc(when) : '') + '</div><div class="nums">' + nums + '</div></div>' +
        (web(r.url) ? '<button class="lnk" data-snopen="' + k + '" aria-label="' + esc(T("sn.open")) + '">' + ic("out") + '</button>' : '') + '</li>';
    }).join("");
    return '<ul class="rows">' + rows + '</ul>';
  }
  function setDrawer(name) {
    drawer = drawer === name ? null : name;
    var d = document.querySelector(".drawer");
    if (d) { if (drawer) d.setAttribute("data-open", drawer); else d.removeAttribute("data-open"); }
    var t = document.querySelectorAll("[data-tog]"), k;
    for (k = 0; k < t.length; k++) t[k].setAttribute("aria-expanded", t[k].getAttribute("data-tog") === drawer ? "true" : "false");
    resize();
  }
  // Under the pages: the pager and Send all, ONE quiet line (Sent, Skipped, Limits and a small menu), the drawer it opens, and the
  // footer, which has something in it only when a person is needed.
  function bottom() {
    var pg = pager(), all = batchButton();
    var behind = skippedTotal > 0 || hidden > 0;
    var haveDrawer = behind || sent.length > 0 || batcher.tracked().length > 0;
    var drawerHtml = haveDrawer
      ? '<div class="drawer"' + (drawer ? ' data-open="' + drawer + '"' : '') + '><div>' +
        (behind ? '<div class="dr" data-dr="skipped">' + skippedRows() + '</div>' : '') +
        (sent.length > 0 ? '<div class="dr" data-dr="sent">' + sentRows() + '</div>' : '') +
        (batcher.tracked().length > 0 ? '<div class="dr" data-dr="scheduled">' + scheduledRows() + '</div>' : '') + '</div></div>' : '';
    return (pg || all ? '<div class="bar">' + pg + all + '</div>' : '') + quietLine() + drawerHtml +
      '<div class="botx">' + connectPanels() + footer() + '</div>';
  }

  // ---- INLINE EDIT (v9): the reply becomes a textarea; Save is `room.revise_draft`, a new revision.
  /*XLEN-BEGIN*/
  // HOW LONG X COUNTS A TEXT, for the editor's counter. It is `community_os_contracts.x_text` ported
  // line for line, and the server's answer is the one that decides (`room.revise_draft` refuses with the
  // same function): a link, and a BARE DOMAIN too (`getwenext.com`), weighs 23 whatever its length; a code
  // point in the light ranges weighs 1, every other one 2; the closing punctuation of a sentence after a link
  // is text; and a text over 280 PLAIN characters is over, whatever it weighs. The estimate errs upwards.
  var X_MAX = 280, X_URL = 23;
  var X_URL_LIKE = /(?:https?:\/\/)?(?:[\p{L}\p{N}](?:[\p{L}\p{N}_-]{0,61}[\p{L}\p{N}])?\.)+[A-Za-z]{2,63}(?::\d{1,5})?(?:\/\S*)?/gu;
  var X_TRAILING = ".,;:!?)]}>'\"\u2026\u2019\u201d\u00bb";
  function xChars(text) {
    var w = 0, k, c;
    for (k = 0; k < text.length; k++) {
      c = text.charCodeAt(k);
      if (c >= 0xD800 && c <= 0xDBFF && k + 1 < text.length) { k++; w += 2; continue; }   // an astral code point: two
      w += ((c <= 4351) || (c >= 8192 && c <= 8205) || (c >= 8208 && c <= 8223) || (c >= 8242 && c <= 8247)) ? 1 : 2;
    }
    return w;
  }
  function xWeight(text) {
    text = String(text == null ? "" : text);
    var total = 0, position = 0, re = new RegExp(X_URL_LIKE.source, "gu"), m, end;
    while ((m = re.exec(text)) !== null) {
      total += xChars(text.slice(position, m.index)) + X_URL;
      end = m.index + m[0].length;
      while (end > m.index && X_TRAILING.indexOf(text.charAt(end - 1)) >= 0) end--;
      position = end;
      if (m[0].length === 0) re.lastIndex++;
    }
    return total + xChars(text.slice(position));
  }
  // The number the server would quote: the plain length when that is over the bound, else the weighted one.
  function xCount(text) {
    var plain = 0, k, c, t = String(text == null ? "" : text);
    for (k = 0; k < t.length; k++) { c = t.charCodeAt(k); if (c >= 0xD800 && c <= 0xDBFF && k + 1 < t.length) k++; plain++; }
    return plain > X_MAX ? plain : xWeight(t);
  }
  /*XLEN-END*/
  function findDraft(rev) {
    for (var k = 0; k < items.length; k++) if (items[k].draft && items[k].draft.revision_id === rev) return items[k].draft;
    return null;
  }
  function indexOfDraft(d) {
    for (var k = 0; k < items.length; k++) if (items[k].draft === d) return k;
    return -1;
  }
  function editErrText(err) {
    if (!err) return "";
    var data = err.data || {};
    if (err.code === "DLUGOSC_X") return fmt(T("ref.DLUGOSC_X"), { waga: data.waga, limit: data.limit || X_MAX }, true);
    var own = err.code ? L("ref." + err.code, null) : null;
    return own || data.powod || err.why || T("ed.failed");
  }
  function editorHtml(d, i, ed) {
    var own = d.rodzaj === "OWN_POST", x = d.platforma === "X", busy = ed.phase === "saving", rev = esc(d.revision_id);
    var w = x ? xCount(ed.text) : 0;
    return '<div class="ed" data-p="edit"><div class="pv-label"><label for="ed-' + rev + '">' + esc(T(own ? "ed.labelPost" : "ed.labelReply")) + '</label></div>' +
      (own && !x && d.tytul ? '<div class="pv-title edt">' + esc(d.tytul) + '</div>' : '') +
      '<textarea class="edit" id="ed-' + rev + '" data-rev="' + rev + '" rows="4"' + (busy ? ' readonly' : '') + '>' + esc(ed.text) + '</textarea>' +
      '<div class="edrow"><span class="edcount"' + (x ? ' data-x="1"' + (w > X_MAX ? ' data-over="1"' : '') : '') + '>' +
      (x ? esc(P("ed.count", { n: w, max: X_MAX })) : '') + '</span><span class="edmsg" role="alert">' + esc(editErrText(ed.err)) + '</span></div>' +
      '<div class="send-a"><button class="primary" data-ed="save" data-i="' + i + '"' + (busy ? ' disabled' : '') + '>' + esc(T(busy ? "ed.saving" : "ed.save")) + '</button>' +
      '<button class="quiet" data-ed="cancel" data-i="' + i + '"' + (busy ? ' disabled' : '') + '>' + esc(T("ed.cancel")) + '</button>' +
      '<button class="quiet ask" data-fix="' + i + '">' + esc(T("ed.ask")) + '</button></div></div>';
  }
  function paintEdit(d) {
    var ed = edits[d.revision_id], el = document.querySelector('[data-box="' + d.revision_id + '"]'), i = indexOfDraft(d);
    if (!el || i < 0) return;
    var had = !!(document.activeElement && el.contains(document.activeElement));
    if (!ed) {
      // Leaving the editor: the region is what it was, and keeps no minimum height.
      el.style.minHeight = "";
      el.setAttribute("data-phase", manual ? mOf(d).phase : sendOf(d).phase);
      el.innerHTML = regionInner(d, i);
    } else {
      // Like a send: while the editor is open the region keeps the height the idle one had.
      if (!el.style.minHeight) {
        var wide = el.querySelector(".ptw[data-open]"); if (wide) fold(wide);
        el.style.minHeight = el.offsetHeight + "px";
      }
      el.setAttribute("data-phase", "edit");
      var prev = grab(el);
      el.innerHTML = regionInner(d, i);
      morph(prev, el);
      var box = el.querySelector("textarea.edit");
      try { if (box && ed.phase === "editing" && (ed.focus || had)) { box.focus({ preventScroll: true }); var n = box.value.length; box.setSelectionRange(n, n); } } catch (e) {}
      ed.focus = false;
    }
    syncBatchButton();
    fit(); resize();
  }
  function editStart(i) {
    var e = items[i], d = e && e.draft; if (!d || manual || !canSend) return;
    if (edits[d.revision_id] || sendOf(d).phase !== "idle") return;
    edits[d.revision_id] = { phase: "editing", text: String(d.tekst || ""), err: null, focus: true };
    paintEdit(d);
  }
  function editCancel(d) {
    if (!d || !edits[d.revision_id] || edits[d.revision_id].phase === "saving") return;
    delete edits[d.revision_id]; paintEdit(d);
  }
  function editSave(d) {
    var ed = edits[d.revision_id]; if (!ed || ed.phase !== "editing") return;
    var box = document.querySelector('textarea.edit[data-rev="' + d.revision_id + '"]');
    var text = String(box ? box.value : ed.text).trim();
    ed.text = box ? box.value : ed.text;
    if (!text) { ed.err = { code: "PUSTY" }; paintEdit(d); return; }
    if (text === String(d.tekst || "").trim()) { ed.err = { code: "BEZ_ZMIAN" }; paintEdit(d); return; }
    ed.phase = "saving"; ed.err = null; paintEdit(d);
    callTool("room.revise_draft", { revision_id: d.revision_id, text: text }).then(function (res) {
      if (edits[d.revision_id] !== ed) return;
      var data = dataOfResult(res);
      if (res && res.result && !res.result.isError && data && data.stan === "WYKONANE" && data.szkic && data.szkic.revision_id) { applyRevision(d, data.szkic); return; }
      // The same save asked twice (a retry after a lost answer): the server says it was done and gives no draft,
      // so the card reads the Pokój again and draws what is there.
      if (res && res.result && !res.result.isError && data && data.stan === "WYKONANE" && data.powtorzone) {
        callTool("room.show", {}).then(function (again) {
          var fresh = dataOfResult(again);
          if (edits[d.revision_id] !== ed) return;
          if (fresh && fresh.stan === "WYKONANE") { delete edits[d.revision_id]; render(fresh); return; }
          ed.phase = "editing"; ed.focus = true; ed.err = { why: T("e.noConnect") }; paintEdit(d);
        });
        return;
      }
      ed.phase = "editing"; ed.focus = true;
      ed.err = { code: data && data.kod, data: data, why: !res || res.error ? T("e.noConnect") : null };
      paintEdit(d);
    });
  }
  // The server made a NEW revision of the same draft: put it where the old one was and draw the card
  // again, so it carries the new digest (a confirmation minted for the old text is dead).
  function applyRevision(d, fresh) {
    var old = d.revision_id, data = lastData;
    delete edits[old]; delete expanded[old];
    saved[fresh.revision_id] = true;
    var merged = {}, key;
    for (key in d) if (Object.prototype.hasOwnProperty.call(d, key)) merged[key] = d[key];
    for (key in fresh) if (Object.prototype.hasOwnProperty.call(fresh, key)) merged[key] = fresh[key];
    data.szkice = (data.szkice || []).map(function (x) { return x.revision_id === old ? merged : x; });
    (data.okazje || []).forEach(function (o) { if (o.szkic === old) o.szkic = fresh.revision_id; });
    if (curKey === "d" + old) curKey = "d" + fresh.revision_id;
    render(data);
  }
  function editButton(name, i) {
    var e = items[i], d = e && e.draft; if (!d) return;
    if (name === "save") editSave(d); else if (name === "cancel") editCancel(d);
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
    return preview(d, !(e && e.opp), false, !!(e && e.opp)) + '<div class="acts"><span class="lead">' +
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
      case "marking": return box(pill("man.saving", "wait"), "marking");
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
    var prev = grab(el);
    el.innerHTML = regionInner(d, m.i);
    morph(prev, el);
    if (m.phase === "done") beam(el.closest(".stage"));
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

  // THE POST A REPLY ANSWERS, drawn as the platform draws it (v11, 13.10), from `cel` of the opportunity (`store.py::target_json`):
  // the author's photo with the thread line under it, name, verified mark, @handle and age, the text ONCE, the first picture, and the
  // counters with the platform's own icons. A counter nobody read is left out, never 0. The picture is an inline image the server
  // gave (`room.pictures`); until it comes, and when it never does, the initials stand there.
  function compact(n) {
    try { return Number(n).toLocaleString(LOCALE, { notation: "compact", maximumFractionDigits: 1 }); } catch (e) { return String(n); }
  }
  function countWord(n, key) { return fmt(plural(n, T(key)), { n: compact(n) }, true); }
  function targetMark(o, joined, inner, gone) {
    return '<div class="tp' + (joined ? ' open' : '') + '" data-p="' + esc(o.platforma === "X" ? "X" : "REDDIT") + '"' + (gone ? ' data-gone="1"' : '') + '>' + inner + '</div>';
  }
  function xTarget(o, joined) {
    var c = o.cel, handle = c.autor ? "@" + String(c.autor).replace(/^@/, "") : "", when = ago(c.utworzono || o.utworzono);
    var head = '<div class="ph"><b>' + esc(c.nazwa || handle || "X") + '</b>' + (c.zweryfikowany ? vfBadge() : '') +
      (c.nazwa && handle ? '<span class="hd">' + esc(handle) + '</span>' : '') + (when ? '<span class="dt">' + esc(when) + '</span>' : '') + trendBadge(o) + '</div>';
    var body = c.zniknal ? '<p class="tg-gone">' + esc(T("tg.gone")) + '</p>'
      : (c.tekst ? textBox(c.tekst, 9) : '') + mediaBlock(c.media) + xMetrics(c.metryki || {});
    return targetMark(o, joined, '<div class="rw2"><div class="avc">' + av40(c.awatar, handle || c.nazwa || "X") + (joined ? '<i class="tl"></i>' : '') + '</div>' +
      '<div class="bd">' + head + body + '</div></div>', !!c.zniknal);
  }
  function rTarget(o, joined) {
    var c = o.cel, p = postWords(o), when = ago(c.utworzono || o.utworzono), by = whoOf("REDDIT", c.autor || o.autor);
    var head = '<div class="ph"><b>' + esc(communityOf("REDDIT", o.spolecznosc)) + '</b>' + (when ? '<span class="dt">' + esc(when) + '</span>' : '') + trendBadge(o) + '</div>' +
      (by ? '<div class="ps">' + esc(by) + '</div>' : '');
    var body = c.zniknal ? '<p class="tg-gone">' + esc(T("tg.gone")) + '</p>'
      : (p.title ? '<h2 class="rt">' + esc(p.title) + '</h2>' : '') + (p.body ? textBox(p.body, 9) : '') + mediaBlock(c.media) + rMetrics(c.metryki || {});
    return targetMark(o, joined, '<div class="rw2"><div class="avc">' + av40(null, "r", "sn") + (joined ? '<i class="tl"></i>' : '') + '</div>' +
      '<div class="bd">' + head + body + '</div></div>', !!c.zniknal);
  }
  // What an opportunity says above the reply; the old blocks for a server that sends no `cel`.
  function targetBlock(o, joined) {
    if (!o.cel || typeof o.cel !== "object") return postBlock(o);
    return o.platforma === "X" ? xTarget(o, joined) : rTarget(o, joined);
  }

  // Where and when, on one line; on its right the one disclosure ("Why?") and, for a post that went out, its state.
  // With the post drawn above the reply (`cel`), its age is in the line of its numbers and, for X, its author in
  // its card: the line here keeps only where.
  function metaOf(o, open) {
    var reddit = o.platforma === "REDDIT";
    var where = o.spolecznosc ? (reddit ? "r/" + String(o.spolecznosc).replace(/^r\//, "") : o.spolecznosc) : (reddit ? "Reddit" : "X");
    var lean = !!(o.cel && typeof o.cel === "object");
    if (lean && !reddit) where = T("mt.reply");
    var author = lean ? null : whoOf(o.platforma, o.autor);
    var when = lean ? null : ago(o.utworzono);
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
    var inner = metaOf(o, opensWhy(kind)) + whyWell(o) + targetBlock(o, kind === "draft");
    if (kind === "posted") {
      tail = published(o, r, i);
    } else if (kind === "draft") {
      tail = region(d, i);
    } else if (kind === "gone") {
      tail = '<div class="region"><div class="note">' + esc(T("oc.draftGoneManual")) + '</div><div class="acts"><span class="lead">' +
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
      '<span class="where">' + S(d.rodzaj === "OWN_POST" ? (d.cytat ? "dc.quoteOn" : "dc.postOn") : "dc.replyOn", { p: x ? "X" : "Reddit" }) + '</span></div>' +
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
    // The first time a page's Send is on screen it shines once; while the reveal covers the card it waits for it.
    if (!revealOn) shineActive();
  }
  function shineActive() {
    var here = document.querySelector("[data-panel][data-active]"), key = cur + ":" + curKey;
    if (!here || shone[key] || !here.querySelector('.primary[data-act="send"]')) return;
    shone[key] = true; shine(here);
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
    // v9: the scope (a local install only), the threads the agent skipped, the replies that went out.
    zakres = !manual && data && data.zakres && typeof data.zakres === "object" ? data.zakres : null;
    accounts = !manual && data && Array.isArray(data.konta_stan) ? data.konta_stan.filter(function (a) { return a && a.platforma && a.konto; }) : [];
    skipped = (data && data.pominiete) || [];
    skippedTotal = Math.max(skipped.length, Number(data && data.pominiete_lacznie) || 0);
    hidden = Math.max(0, Number(data && data.ukryte) || 0);
    sent = results;
    oppById = {}; list.forEach(function (o) { oppById[o.id] = o; });
    if ((drawer === "skipped" && !skippedTotal && !hidden) || (drawer === "sent" && !sent.length) || (drawer === "scheduled" && !batcher.tracked().length)) drawer = null;
    if (!list.length && !drafts.length) {
      items = []; total = 0;
      app.innerHTML = top(true) + '<h1>' + T("rd.noneH") + '</h1>' +
        '<div class="empty">' + esc(T("rd.noneBody")) + '</div>' + bottom() + sheet() + batchSheet();
      syncSheet(); resize(); return;
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
    // THE PAGES ARE IN THE SERVER'S ORDER (v11): the drafts, best first (`room.show` ranks them and sends at most eight pages), each with
    // the conversation it answers when there is one; then the conversations nobody has drafted for; then the ones already answered.
    var entries = [], used = {};
    drafts.forEach(function (d) {
      var o = d.okazja_id && byOpportunity[d.okazja_id] ? list.filter(function (x) { return x.id === d.okazja_id; })[0] : null;
      if (o) {
        if (o.szkic !== d.revision_id) return;       // an older draft of a conversation whose newer one is on the page
        used[o.id] = true;
        entries.push({ rank: 0, opp: o, draft: d, result: resultOf[o.id] || null });
      } else entries.push({ rank: 0, draft: d });
    });
    list.forEach(function (o) {
      if (used[o.id]) return;
      var d = o.stan === "SZKIC" && o.szkic ? byRevision[o.szkic] || null : null;
      entries.push({ rank: o.stan === "ODPOWIEDZIANO" ? 3 : d ? 0 : 2, opp: o, draft: d, result: resultOf[o.id] || null });
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
    // The first drawing of the session opens with a short reveal (a layer over the headline and the panel).
    var revealing = !revealed && !calm() && order.length > 0;
    cancelReveal();
    revealOn = revealing;
    app.innerHTML = top(true) + '<div class="main"' + (revealing ? ' data-reveal="1"' : '') + '><h1>' + headline + '</h1>' +
      (bits.length ? '<div class="sum">' + bits.join(" ") + '</div>' : '') +
      '<section class="stage" role="group" aria-roledescription="carousel" aria-label="' + esc(T("nav.label")) + '">' + pages + '</section>' +
      sheet() + batchSheet() + (revealing ? revealLayer(order, list) : '') + '</div>' + bottom();
    syncSheet(); mark(); fit(); resize();
    wantPictures();
    if (revealing) { revealed = true; startReveal(); }
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
    if (b.hasAttribute("data-menu")) {
      menuOpen = !menuOpen;
      var mnu = document.querySelector(".menu");
      if (mnu) { if (menuOpen) mnu.setAttribute("data-open", "1"); else mnu.removeAttribute("data-open"); }
      b.setAttribute("aria-expanded", menuOpen ? "true" : "false");
      resize(); return;
    }
    if (menuOpen) closeMenu();
    if (b.hasAttribute("data-room")) {
      var here = items[cur] && items[cur].draft;
      openLink(here && room ? room.replace(/#.*$/, "") + "#szkic-" + here.revision_id : room); return;
    }
    if (b.hasAttribute("data-tog")) { setDrawer(b.getAttribute("data-tog")); return; }
    if (b.hasAttribute("data-skopen")) { var so = skipped[Number(b.getAttribute("data-skopen"))]; if (so) openLink(so.url); return; }
    if (b.hasAttribute("data-snopen")) { var sn = sent[Number(b.getAttribute("data-snopen"))]; if (sn) openLink(sn.url); return; }
    if (b.hasAttribute("data-conn")) { connectAction(b.getAttribute("data-conn"), b.getAttribute("data-p"), b.getAttribute("data-h")); return; }
    if (b.hasAttribute("data-ba")) { batchAction(b.getAttribute("data-ba"), b.getAttribute("data-rev")); return; }
    if (b.hasAttribute("data-scope")) { scopeAction(b.getAttribute("data-scope")); return; }
    if (b.hasAttribute("data-scact")) { scopeButton(b.getAttribute("data-scact")); return; }
    if (b.hasAttribute("data-edit")) { editStart(Number(b.getAttribute("data-edit"))); return; }
    if (b.hasAttribute("data-ed")) { editButton(b.getAttribute("data-ed"), Number(b.getAttribute("data-i"))); return; }
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
    var e = items[Number(b.getAttribute("data-reply") || b.getAttribute("data-open") ||
      b.getAttribute("data-fix") || b.getAttribute("data-result"))]; if (!e) return;
    var o = e.opp || null, d = e.draft || null;
    var url = originOf(e);
    var who = (o && o.autor) || (d && d.w_odpowiedzi_na && d.w_odpowiedzi_na.autor) || T("ask.who");
    if (b.hasAttribute("data-open")) { openLink(url); return; }
    if (b.hasAttribute("data-result")) { openLink(e.result && e.result.url); return; }
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
    // Escape leaves the inline editor, like Cancel.
    if (ev.key === "Escape" && ev.target && ev.target.matches && ev.target.matches("textarea.edit")) {
      var er = ev.target.getAttribute("data-rev");
      if (edits[er] && edits[er].phase === "editing") { ev.preventDefault(); editCancel(findDraft(er)); }
      return;
    }
    // Escape leaves the batch panel, like Cancel (a key never sends: only a click does).
    if (ev.key === "Escape" && /^(checking|confirm|refused|done)$/.test(batcher.state().phase)) { ev.preventDefault(); batcher.dispatch("cancel"); return; }
    // Typing a link (or moving in a selected reply) is not paging.
    if (ev.target && /^(INPUT|TEXTAREA)$/.test(String(ev.target.tagName))) return;
    if (scoper.state().phase !== "idle" || batcher.state().phase !== "idle") return;
    if (ev.key === "ArrowRight") { if (show(cur + 1)) ev.preventDefault(); }
    else if (ev.key === "ArrowLeft") { if (show(cur - 1)) ev.preventDefault(); }
  });

  // A tick in the batch panel: a NEW question for the new set (`room.batch_token`), never a send.
  document.addEventListener("change", function (ev) {
    var t = ev.target; if (!t || !t.matches || !t.matches('[data-ba="pick"]')) return;
    batcher.dispatch("toggle", t.value);
  });

  // What is typed in the card's own fields: the editor's text, the Limits form.
  document.addEventListener("input", function (ev) {
    var t = ev.target; if (!t || !t.matches) return;
    if (t.matches("textarea.edit")) {
      var ed = edits[t.getAttribute("data-rev")]; if (!ed) return;
      ed.text = t.value;
      var count = t.closest(".ed").querySelector(".edcount");
      if (count && count.hasAttribute("data-x")) {
        var w = xCount(t.value);
        count.textContent = P("ed.count", { n: w, max: X_MAX });
        if (w > X_MAX) count.setAttribute("data-over", "1"); else count.removeAttribute("data-over");
      }
      if (ed.err) { ed.err = null; var msg = t.closest(".ed").querySelector(".edmsg"); if (msg) msg.textContent = ""; }
    } else if (t.matches("[data-sc]")) {
      if (t.matches('[data-sc="acc"]')) {
        // The account's pace block follows its tick: dimmed and off when the account is not in the scope.
        var block = document.querySelector('[data-pa="' + t.value + '"]');
        if (block) {
          if (t.checked) block.classList.remove("off"); else block.classList.add("off");
          Array.prototype.forEach.call(block.querySelectorAll("input"), function (i) { i.disabled = !t.checked; });
        }
      }
      scForm = readForm(); scErr = null;
      var err = document.querySelector(".sh-err"); if (err) err.parentNode.removeChild(err);
    }
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
      appInfo: { name: "wenext-pokoj", version: "11" },
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
