"""The rest of what a person does from the card, so they never need the dashboard (v9, 11.10).

v4 gave the card one act, "Wyslij", and `tools/card_send.py` holds its threat model. The founder
then asked for everything else a person did on the web page to happen in the chat: correct a draft,
pause the agent, resume it, and set the limits of what it may do. Five more APP-ONLY tools
(`_meta.ui.visibility: ["app"]`, listed only for a Codex host, never for Claude Code, never for a
hosted session, never in `catalog.list` or `catalog.describe`), built exactly like the send's:

  * `room.revise_draft`   a person's correction of a waiting draft, as a NEW revision;
  * `room.scope_token`    the question before an act on the scope: validates it, mints a token;
  * `room.pause`, `room.resume`, `room.sign_scope`   the acts, each behind that token.

THREAT MODEL, AS AN ADDITION TO THE FIVE LINES OF `card_send.py` (they all still hold):

  6. The model has NO tool that signs, widens, pauses or resumes a scope, and the card's five are
     invisible to it for the reason `card_send` states (the host's promise, plus: not listed for
     any other host, "no such tool" for a call from one). The model's way to a changed limit is a
     sentence to the person: a refusal that a limit would fix says "call room.show", which draws
     the card with the Limits panel (agent side: `HOST_INSTRUCTIONS`, `with_scope_hint`).
  7. A CHANGE OF THE SCOPE IS TWO STEPS AND A TOKEN, as a send is. `room.scope_token` validates
     what the person asked for against rows the SERVER reads (accounts assigned to the project,
     not stopped, able to do the chosen actions on their platform; limits inside fixed bounds; a
     term of 7 or 14 days; never an empty list of accounts or actions), builds the plan and mints
     a one-time token (three minutes, burned by the first presentation, only in `_meta`, redacted
     from the trace). The token holds the WHOLE plan, including the deadline computed once: the
     acting tool takes only the token, so what is signed is exactly what the person was shown, and
     a card that lied to the server after the sentence was drawn can change nothing. The acting
     tool re-reads the scope and refuses (`ZAKRES_ZMIENIONY`) when it is no longer the one the
     sentence was about.
  8. PAUSING IS THE SAFE DIRECTION AND STILL NEEDS THE CLICK: a prompt injected into a model must
     not be able to stop the founder's agent either (a denial of service is still a harm), and the
     pause interrupts running sessions. RESUMING and SIGNING are the dangerous directions: only
     the dedicated click lifts a pause (a send click never does), a signature made in the card is
     always REVIEW (every text still waits for the person's click), and a signature is refused
     while a request waits in the publisher's queue, exactly as `CONSENT_WIDEN` is.
  9. REVISING needs no token, and that is a decision: it publishes and approves nothing. It writes
     the next revision of the same proposal (the old one is kept, REJECTED, never overwritten),
     the approval window is not extended, and the new digest makes any send confirmation minted
     for the old text worth nothing. The model can already write any draft text through
     `draft.create`, so this opens no capability it lacks; what it adds is the person's words, and
     those are checked server side (length, X's weighted length, no long dash) and never silently
     rewritten.
 10. RESIDUAL, stated plainly, as in `card_send`: an agent that ignores the host's filter, learns
     the names and calls both steps in order can change the scope. It cannot publish by it: a
     REVIEW scope still waits for the person's click on every text.
 11. LIMITS PER ACCOUNT (v11, 13.10) ARE NO NEW WAY TO SIGN, AND NO WAY TO WIDEN BY OMISSION. They
     ride the same two steps and the same token: `room.scope_token` takes `limity_konta` (account
     id -> per hour, per day, own posts a day), the server checks every number against the SAME
     bounds as the shared three (`CARD_LIMIT_BOUNDS`, hour and own posts never above the day),
     refuses a limit for an account that was not chosen (`LIMIT_POZA_GRANICA`: a limit for nobody
     is a signature that looks like a permission) and gives EVERY chosen account an explicit entry
     (one the card left out gets the card's defaults, never "unlimited"), and the token holds the
     whole plan including those numbers, so the acting tool signs what was shown and the plan
     rebuilt from the kept inputs must equal it. The model still has no tool that signs. What a
     pace per account can do that one bucket could not is let X post faster than Reddit: it never
     lifts the mode (always REVIEW) nor the person's click per text, and an account the scope does
     not list is refused by the gates (fail closed), not counted as unlimited.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from community_os_contracts.x_text import X_MAX_WEIGHT, x_length_problem

from control_plane.observe.text_rules import count_long_dashes
from control_plane.opportunities.room import draft_json
from control_plane.opportunities.store import OpportunityStore
from control_plane.pilot.publication import (
    ConsentError,
    join_own_post,
    split_media,
    split_own_post,
)
from control_plane.presence.approval import ACTION_OF_CAPABILITY, ACTION_OWN_POST
from control_plane.presence.budget import effective_limits
from control_plane.presence.card_consent import (
    CONSENT_SET,
    REFUSED_QUEUE,
    CardConsentRefused,
    ConsentPlan,
    account_limit_rows,
    assigned_accounts,
    named_scope_accounts,
    pause_for_card,
    plan_limits,
    plan_pause,
    plan_resume,
    requests_waiting_on,
    resume_for_card,
    scope_summary,
    sign_for_card,
)
from control_plane.presence.human_approval import HumanApprovalRefused, ensure_waiting, payload_of
from control_plane.presence.human_revision import revise_into_new_revision
from control_plane.presence.store import PresenceRefusal, PresenceStore

from .card_send import (
    CODE_CONFIRMATION,
    CODE_NOT_FOUND,
    CODE_SCOPE_MOVED,
    CODE_SIGNING,
    META_TOKEN,
    TOKEN_TTL,
    TokenRefused,
)

#: The longest correction a person may save: the dashboard's own bound (`api/project_approvals.py`).
MAX_TEXT = 4000

CODE_EMPTY = "PUSTY"
CODE_TOO_LONG = "ZA_DLUGI"
CODE_LONG_DASH = "DLUGA_PAUZA"
CODE_X_LENGTH = "DLUGOSC_X"
CODE_ARGUMENT = "ARGUMENT"

PURPOSE_PAUSE = "wstrzymaj"
PURPOSE_RESUME = "wznow"
PURPOSE_SET = "podpisz"
PURPOSES = (PURPOSE_PAUSE, PURPOSE_RESUME, PURPOSE_SET)

#: How many unspent scope confirmations are kept at once: a person has one panel open at a time.
MAX_OUTSTANDING = 8


@dataclass(frozen=True)
class ScopeGrant:
    """What one scope confirmation was minted for. The plan is whole: the acting tool signs THIS."""

    purpose: str
    plan: ConsentPlan
    #: The inputs the plan was built from, so the acting tool can build it again and compare.
    args: dict[str, Any]
    minted_at: datetime
    expires_at: datetime


class ScopeTokens:
    """One-time confirmations for an act on the scope, in memory, for the life of the tool server.

    In memory and hashed for the reasons `card_send.SendTokens` gives. Different from it in one way:
    the grant carries the whole validated plan, because a scope change has no row (like a draft)
    the click is about, so the plan IS what the person agreed to. A new confirmation for a purpose
    kills the earlier one of that purpose, and any presentation of a known token burns it.
    """

    def __init__(
        self,
        *,
        ttl: timedelta = TOKEN_TTL,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._grants: dict[str, ScopeGrant] = {}

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def now(self) -> datetime:
        return self._clock()

    def _prune(self) -> None:
        now = self._clock()
        for key in [k for k, grant in self._grants.items() if grant.expires_at <= now]:
            del self._grants[key]

    def mint(
        self, *, purpose: str, plan: ConsentPlan, args: dict[str, Any], minted_at: datetime
    ) -> tuple[str, datetime]:
        self._prune()
        for key in [k for k, g in self._grants.items() if g.purpose == purpose]:
            del self._grants[key]
        while len(self._grants) >= MAX_OUTSTANDING:
            del self._grants[next(iter(self._grants))]
        token = secrets.token_urlsafe(32)
        expires_at = self._clock() + self._ttl
        self._grants[self._key(token)] = ScopeGrant(
            purpose=purpose, plan=plan, args=args, minted_at=minted_at, expires_at=expires_at
        )
        return token, expires_at

    def redeem(self, token: str, *, purpose: str) -> ScopeGrant:
        """The grant behind `token`, consumed. Any presentation of a known token burns it, so a
        token shown to the wrong tool is spent by the mistake."""
        grant = self._grants.pop(self._key(token), None)
        if grant is None:
            raise TokenRefused(
                CODE_CONFIRMATION,
                "Brak ważnego potwierdzenia kliknięciem: nieznane albo już zużyte. Zmianę zakresu "
                "zatwierdza człowiek w karcie, agent tego nie robi.",
            )
        if grant.expires_at <= self._clock():
            raise TokenRefused(CODE_CONFIRMATION, "Potwierdzenie wygasło. Spróbuj ponownie.")
        if grant.purpose != purpose:
            raise TokenRefused(
                CODE_CONFIRMATION,
                "To potwierdzenie dotyczy innej zmiany zakresu. Spróbuj ponownie.",
            )
        return grant


def scope_plan_json(plan: ConsentPlan, *, previous_mode: str | None) -> dict[str, Any]:
    """What the card tells the person before the click: the plan as numbers and names."""
    limits = effective_limits(plan.budget)
    return {
        "rodzaj": plan.kind,
        "tryb": plan.mode,
        "wygasa": plan.expires_at.isoformat() if plan.expires_at else None,
        "limity": {
            "godzina": limits.publications_per_hour,
            "doba": limits.publications_per_day,
            "wlasne_doba": limits.own_posts_per_day,
        },
        "konta": [account.to_json() for account in plan.accounts],
        "dzialania": list(plan.scope.actions),
        "poprzedni_tryb": previous_mode,
        # v11: what binds EACH account when the limits are per account (empty: one shared bucket,
        # the three numbers above), and the overall ceiling when the scope signs one.
        "limity_kont": account_limit_rows(plan.budget, plan.accounts),
        "razem": {
            "godzina": limits.overall_per_hour() if limits.per_account else None,
            "doba": limits.overall_per_day() if limits.per_account else None,
        },
    }


#: The codes of a refusal that a change of the scope, a limit or a pause would fix. The card's own
#: tools never reach the model, but a model-visible tool can say the same thing in other words.
SCOPE_REFUSAL_CODES = frozenset(
    {
        "BUDZET",
        "ZAKRES",
        "ZAKRES_WSTRZYMANY",
        "ZAKRES_INNY_TRYB",
        "ZAKRES_PUSTY",
        "ZAKRES_WYGASL",
        "BRAK_ZAKRESU",
    }
)
#: Tools whose answer can be "the scope has to change first" (the ones a model can call that touch
#: the signed scope or the pace): their non-final results carry the hint below.
SCOPE_TOOLS = frozenset({"delegation.propose", "drafts.publish"})

SCOPE_HINT = (
    "To nie jest rzecz do naprawienia przez Ciebie: nie podpisujesz, nie poszerzasz ani nie "
    "wznawiasz zakresu i nie masz do tego narzędzia. Powiedz człowiekowi jednym zdaniem, co "
    "stoi na przeszkodzie, i zawołaj room.show: karta ma przyciski Limity, Wstrzymaj i Wznów, "
    "którymi człowiek zmienia to jednym kliknięciem, bez opuszczania rozmowy."
)

#: The code of a refusal that only the person's own sign-in fixes: the agent's browser has no
#: session of the project's account (or has somebody else's). v10, 12.10, the founder's X session
#: that was lost overnight. Set by the tools that read through the browser (`x.*`, `reddit.*`).
SESSION_LOST_CODE = "SESJA_UTRACONA"
SESSION_REFUSAL_CODES = frozenset({SESSION_LOST_CODE})

#: The code of an answer that says the browser's session could NOT BE CHECKED right now: the
#: browser did not answer in time, or the page did not load. NOT in `SESSION_REFUSAL_CODES`, on
#: purpose: that set drives the hint that sends a person to Reconnect, and a person must not be
#: asked to sign in again because OUR read was slow (12 and 13.10.2026: `x.read` called a capsule
#: that had hung "no session" while the actuator published from the account). The tool's own hint
#: says what to do instead: retry, and work on other sources meanwhile.
SESSION_UNKNOWN_CODE = "SESJA_NIEPEWNA"
SESSION_UNKNOWN_HINT = (
    "To nie jest wylogowanie: przeglądarka agenta nie odpowiedziała na czas albo strona się nie "
    "wczytała, więc sesji nie dało się teraz sprawdzić. Nie proś człowieka o logowanie i nie "
    "obchodź tego. Spróbuj za kilka minut (system ponawia sprawdzenie sam) i do tego czasu "
    "pracuj na innych źródłach."
)

#: The sentence the model reads with such a refusal. It says what the model must NOT do (log in for
#: the person, work around the missing session, ask for a password) and what to do instead: tell
#: the person in one sentence and draw the card, whose footer shows each account's health and a
#: Reconnect button that opens the agent's browser for the person to sign in themselves.
SESSION_HINT = (
    "To nie jest rzecz do naprawienia przez Ciebie: nie loguj się za człowieka, nie proś o hasło "
    "i nie obchodź braku sesji. Powiedz człowiekowi jednym zdaniem, że przeglądarka agenta nie "
    "ma sesji tego konta, i zawołaj room.show: karta pokazuje stan kont i przycisk „Połącz "
    "ponownie”, którym człowiek loguje się sam w jednej chwili, bez opuszczania rozmowy."
)


def with_scope_hint(tool: str, result: dict[str, Any]) -> dict[str, Any]:
    """`result` with `wskazowka` pointing the model at the card, when the answer is a scope or
    budget refusal, or a refusal that the person's own sign-in fixes (`SESSION_LOST_CODE`).

    PURE; a hint the tool already gave stays, ours is added after it; used for sessions run in a
    person's own agent, where the card exists (`mcp_server._run_tool`). The two hints are told
    apart by the `kod`: a lost session says Reconnect, a scope refusal says Limits."""
    code = str(result.get("kod") or "")
    if code in SESSION_REFUSAL_CODES:
        hint = SESSION_HINT
    elif code in SCOPE_REFUSAL_CODES or tool in SCOPE_TOOLS:
        hint = SCOPE_HINT
    else:
        return result
    if result.get("stan") == "WYKONANE" or hint in str(result.get("wskazowka") or ""):
        return result
    earlier = str(result.get("wskazowka") or "").strip()
    return {**result, "wskazowka": f"{earlier} {hint}".strip()}


CARD_ACTION_TOOL_SPECS = (
    {
        "name": "room.revise_draft",
        "summary": "TYLKO DLA KARTY: zapisz poprawkę człowieka jako nową wersję szkicu.",
        "detail": (
            "Narzędzie dla karty Pokoju w agencie, niewidoczne dla modelu (visibility: app). "
            "Człowiek poprawił tekst w karcie i zapisuje: powstaje NOWA rewizja tej samej "
            "propozycji (poprzednia zostaje w historii jako odrzucona), tą samą drogą co trasa "
            "poprawki w dashboardzie. Niczego nie zatwierdza i niczego nie publikuje; okno zgody "
            "się nie przedłuża, a odcisk nowej wersji jest inny, więc potwierdzenie wysyłki "
            "wydane dla starej nic nie znaczy. `text` to cały tekst odpowiedzi albo posta X; dla "
            "własnego posta na Reddicie to sama treść, tytuł zostaje. Serwer odmawia: pustego "
            "tekstu, tekstu bez zmian, długiej pauzy, tekstu dłuższego niż 280 w rachunku X. "
            "Wynik ma `szkic` w kształcie jednego elementu `szkice` z room.show."
        ),
        "state": "DOSTEPNE",
        "writes": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "revision_id": {"type": "string"},
                "text": {"type": "string", "maxLength": MAX_TEXT},
            },
            "required": ["revision_id", "text"],
            "additionalProperties": False,
        },
        "tags": ("karta", "działanie"),
    },
    {
        "name": "room.scope_token",
        "summary": "TYLKO DLA KARTY: potwierdzenie zmiany zakresu, po kliknięciu człowieka.",
        "detail": (
            "Narzędzie dla karty Pokoju w agencie, niewidoczne dla modelu (visibility: app). "
            "Sprawdza, o co człowiek prosi (wstrzymać zakres, wznowić wstrzymany, ustawić nowy: "
            "konta, działania, limity, termin 7 albo 14 dni), po rzeczach, które serwer czyta z "
            "bazy, i mówi, co kliknięcie zrobi (`zakres`). Potwierdzenie jedzie tylko w `_meta` "
            "wyniku, ważne trzy minuty, jednorazowe, i niesie cały plan. Niczego nie zmienia; nie "
            "ma `writes`, żeby powtórne wywołanie wydało nowe potwierdzenie."
        ),
        "state": "DOSTEPNE",
        "input_schema": {
            "type": "object",
            "properties": {
                "akcja": {"type": "string", "enum": list(PURPOSES)},
                "konta": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Identyfikatory kont z `zakres.do_wyboru` w room.show.",
                },
                "dzialania": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["REPLY", "OWN_POST", "RESEARCH"]},
                },
                "limity": {
                    "type": "object",
                    "properties": {
                        "godzina": {"type": "integer"},
                        "doba": {"type": "integer"},
                        "wlasne_doba": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
                "limity_konta": {
                    "type": "object",
                    "description": (
                        "Limity PER KONTO: identyfikator konta -> {godzina, doba, wlasne_doba}. "
                        "Odpowiedzi liczone osobno od własnych postów; bez tego pola trzy liczby "
                        "z `limity` są jednym wspólnym koszem projektu."
                    ),
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "godzina": {"type": "integer"},
                            "doba": {"type": "integer"},
                            "wlasne_doba": {"type": "integer"},
                        },
                        "additionalProperties": False,
                    },
                },
                "dni": {"type": "integer", "enum": [7, 14]},
            },
            "required": ["akcja"],
            "additionalProperties": False,
        },
        "tags": ("karta",),
    },
    {
        "name": "room.pause",
        "summary": "TYLKO DLA KARTY: wstrzymaj zakres projektu po kliknięciu człowieka.",
        "detail": (
            "Narzędzie dla karty Pokoju w agencie, niewidoczne dla modelu (visibility: app). "
            "Wymaga potwierdzenia z room.scope_token (akcja wstrzymaj), jednorazowego. Wstrzymuje "
            "podpisany zakres (ta sama droga co przycisk w dashboardzie), przerywa sesje w tle i "
            "zapisuje to w dzienniku. Wynik ma `zakres` po zmianie."
        ),
        "state": "DOSTEPNE",
        "writes": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "Potwierdzenie z room.scope_token."}
            },
            "required": ["token"],
            "additionalProperties": False,
        },
        "tags": ("karta", "działanie"),
    },
    {
        "name": "room.resume",
        "summary": "TYLKO DLA KARTY: wznów wstrzymany zakres projektu po kliknięciu człowieka.",
        "detail": (
            "Narzędzie dla karty Pokoju w agencie, niewidoczne dla modelu (visibility: app). "
            "Wymaga potwierdzenia z room.scope_token (akcja wznow), jednorazowego. Zdejmuje "
            "wstrzymanie z TEGO SAMEGO podpisu (tryb, konta, limity i termin bez zmian); "
            "wygasłego nie wznawia. Wynik ma `zakres` po zmianie."
        ),
        "state": "DOSTEPNE",
        "writes": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "Potwierdzenie z room.scope_token."}
            },
            "required": ["token"],
            "additionalProperties": False,
        },
        "tags": ("karta", "działanie"),
    },
    {
        "name": "room.sign_scope",
        "summary": "TYLKO DLA KARTY: podpisz nowy zakres z limitami po kliknięciu człowieka.",
        "detail": (
            "Narzędzie dla karty Pokoju w agencie, niewidoczne dla modelu (visibility: app). "
            "Wymaga potwierdzenia z room.scope_token (akcja podpisz), jednorazowego; podpisuje "
            "dokładnie ten plan, który człowiek widział, tą samą drogą co formularz Obecności "
            "(ten sam wiersz, ten sam odcisk, ten sam wpis w dzienniku), zawsze w trybie REVIEW: "
            "każdy tekst nadal czeka na kliknięcie. Zastępuje poprzedni podpis; odmawia, gdy "
            "wniosek jeszcze czeka w kolejce publikatora. Wynik ma `zakres` po zmianie."
        ),
        "state": "DOSTEPNE",
        "writes": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "Potwierdzenie z room.scope_token."}
            },
            "required": ["token"],
            "additionalProperties": False,
        },
        "tags": ("karta", "działanie"),
    },
)


class CardActionTools:
    """Mixin of `ToolRegistry`: the five app-only tools behind the card's Edit, Pause, Limits."""

    @property
    def _scope_tokens(self) -> ScopeTokens:
        # A property over `__dict__` instead of a dataclass field: `registry.ToolRegistry` is not
        # edited for this, and a test can still set `registry._scope_tokens = ScopeTokens(...)`.
        store = self.__dict__.get("_scope_tokens_value")
        if store is None:
            store = self.__dict__["_scope_tokens_value"] = ScopeTokens()
        return store  # type: ignore[no-any-return]

    @_scope_tokens.setter
    def _scope_tokens(self, value: ScopeTokens) -> None:
        self.__dict__["_scope_tokens_value"] = value

    # -- plumbing -----------------------------------------------------------------------------

    def _gone(self, reason: str, code: str, **extra: Any) -> dict[str, Any]:
        """A refusal, always with a `kod`: the card branches on it, never on the sentence."""
        from .registry import unavailable

        return unavailable(reason, kod=code, **extra)

    def _host(self) -> str:
        return str(self.capability.host)  # type: ignore[attr-defined]

    def _ids(self) -> tuple[UUID, UUID, UUID]:
        capability = self.capability  # type: ignore[attr-defined]
        return capability.organization_id, capability.workspace_id, capability.project_id

    # -- room.revise_draft --------------------------------------------------------------------

    async def _room_revise_draft(self, args: dict[str, Any]) -> dict[str, Any]:
        from .registry import done

        try:
            revision_id = UUID(str(args.get("revision_id")))
        except (TypeError, ValueError):
            return self._gone("revision_id nie jest identyfikatorem szkicu.", CODE_ARGUMENT)
        raw = args.get("text")
        if not isinstance(raw, str):
            return self._gone("text musi być napisem.", CODE_ARGUMENT)
        typed = raw.strip()
        if not typed:
            return self._gone(
                "Pusta odpowiedź to nie poprawka. Wpisz tekst albo anuluj.", CODE_EMPTY
            )
        if len(typed) > MAX_TEXT:
            return self._gone(
                f"Tekst ma {len(typed)} znaków, a poprawka może mieć najwyżej {MAX_TEXT}.",
                CODE_TOO_LONG,
                dlugosc=len(typed),
                limit=MAX_TEXT,
            )
        if count_long_dashes(typed):
            return self._gone(
                "W tekście jest długa pauza (myślnik em albo en). Wenext ich nie publikuje: zamień "
                "ją na kropkę, przecinek albo dwukropek.",
                CODE_LONG_DASH,
            )

        organization_id, workspace_id, _project_id = self._ids()
        row = await self._card_draft(revision_id)  # type: ignore[attr-defined]
        if row is None:
            return self._gone("Nie ma takiej propozycji w tym projekcie.", CODE_NOT_FOUND)
        try:
            ensure_waiting(row)
        except HumanApprovalRefused as exc:
            return self._gone(exc.detail, exc.code)

        payload = payload_of(row)
        target = str(payload.get("target_external_id") or "")
        on_x = str(row["platform"]) == "X" or target.startswith("x:")
        own = ACTION_OF_CAPABILITY.get(str(payload.get("action_type"))) == ACTION_OWN_POST
        if on_x:
            # The ONE definition of X's length, shared with the actuator (`x_length_problem`): the
            # weighted count (a bare domain is a link of 23) AND the plain bound, so what is refused
            # here is what the actuator would refuse after the approval.
            problem = x_length_problem(typed)
            if problem is not None:
                _kind, number = problem
                return self._gone(
                    f"Tekst ma {number} w rachunku X, a X przyjmuje {X_MAX_WEIGHT}. Skróć go.",
                    CODE_X_LENGTH,
                    waga=number,
                    limit=X_MAX_WEIGHT,
                )
        text = typed
        if own and not on_x:
            # The card edits the BODY of an own Reddit post; the title is not on the card's editor
            # and stays as the agent wrote it (it rides in the same field, `join_own_post`).
            shown, _media = split_media(str(payload.get("text_and_media") or ""))
            title, _body = split_own_post(shown)
            try:
                text = join_own_post(title, typed)
            except ValueError as exc:
                return self._gone(f"Tego posta nie da się poprawić z karty: {exc}.", CODE_ARGUMENT)

        try:
            revised = await revise_into_new_revision(
                self.pool,  # type: ignore[attr-defined]
                organization_id=organization_id,
                workspace_id=workspace_id,
                row=row,
                text=text,
                actor_ref=f"plugin:{self._host()}",
            )
        except HumanApprovalRefused as exc:
            return self._gone(exc.detail, exc.code)

        fresh = await self._card_draft(revised.revision_id)  # type: ignore[attr-defined]
        if fresh is None:  # the account was unassigned in the meantime: the revision is saved
            return self._gone(
                "Poprawka zapisana, ale konto szkicu nie jest już w projekcie.", CODE_NOT_FOUND
            )
        return done(
            szkic=await self._draft_for_card(fresh),
            poprzednia=str(revision_id),
        )

    async def _draft_for_card(self, row: Any) -> dict[str, Any]:
        """One draft in the shape of `szkice` of `room.show`, for a revision just written here."""
        organization_id, workspace_id, project_id = self._ids()
        payload = payload_of(row)
        target = str(payload.get("target_external_id") or "")
        opportunity = (
            await OpportunityStore(self.pool).for_target(  # type: ignore[attr-defined]
                organization_id=organization_id, project_id=project_id, external_id=target
            )
            if target
            else None
        )
        thread = None
        if target.startswith("t3_"):
            async with self.pool.acquire() as conn:  # type: ignore[attr-defined]
                thread = await conn.fetchrow(
                    "SELECT t.external_id, t.title, t.author, c.community_key "
                    "FROM observed_threads t JOIN communities c "
                    "ON c.id = t.community_id AND c.organization_id = t.organization_id "
                    "WHERE t.organization_id = $1 AND t.workspace_id = $2 "
                    "AND ('t3_' || t.external_id) = $3 LIMIT 1",
                    organization_id,
                    workspace_id,
                    target,
                )
        shown = draft_json(
            {**dict(row), "payload": payload}, opportunity=opportunity, thread=thread
        )
        if getattr(self.capability, "hosted", False):  # type: ignore[attr-defined]
            shown["konto"] = None
        return shown

    # -- room.scope_token ---------------------------------------------------------------------

    async def _scope_plan(self, args: dict[str, Any], *, now: datetime) -> tuple[ConsentPlan, str]:
        """The plan an `akcja` asks for, or raises `CardConsentRefused`. Shared by the question and
        by the acting tool, which builds it again from the inputs the grant kept and compares."""
        organization_id, workspace_id, project_id = self._ids()
        delegation = await PresenceStore(self.pool).delegation_for(  # type: ignore[attr-defined]
            organization_id=organization_id, project_id=project_id
        )
        action = str(args.get("akcja"))
        if action in (PURPOSE_PAUSE, PURPOSE_RESUME):
            named = (
                await named_scope_accounts(
                    self.pool,  # type: ignore[attr-defined]
                    organization_id=organization_id,
                    ids=delegation.scope.accounts,
                )
                if delegation is not None
                else ()
            )
            plan = (
                plan_pause(delegation, named=named)
                if action == PURPOSE_PAUSE
                else plan_resume(delegation, named=named)
            )
            return plan, delegation.mode if delegation is not None else ""
        assigned = await assigned_accounts(
            self.pool,  # type: ignore[attr-defined]
            organization_id=organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        plan = plan_limits(
            delegation,
            assigned=assigned,
            chosen=args.get("konta") or (),
            actions=args.get("dzialania") or (),
            limits=args.get("limity"),
            days=args.get("dni"),
            now=now,
            account_limits=args.get("limity_konta"),
        )
        if plan.replaces is not None and await requests_waiting_on(
            self.pool,  # type: ignore[attr-defined]
            organization_id=organization_id,
            delegation_id=plan.replaces,
        ):
            raise CardConsentRefused(
                REFUSED_QUEUE,
                "Poprzednia odpowiedź jeszcze czeka w kolejce publikatora, a nowy podpis "
                "zatrzymałby ją. Poczekaj, aż wyjdzie, i spróbuj ponownie.",
            )
        return plan, delegation.mode if delegation is not None else ""

    @staticmethod
    def _scope_args(args: dict[str, Any]) -> dict[str, Any] | str:
        """The inputs of a question, shaped and checked for TYPE only (the plan checks the rest), or
        a sentence saying what is wrong."""
        action = args.get("akcja")
        if action not in PURPOSES:
            return "akcja to wstrzymaj, wznow albo podpisz."
        shaped: dict[str, Any] = {"akcja": action}
        if action != PURPOSE_SET:
            return shaped
        konta, dzialania = args.get("konta") or [], args.get("dzialania") or []
        if not isinstance(konta, list) or not all(isinstance(one, str) for one in konta):
            return "konta to lista identyfikatorów."
        if not isinstance(dzialania, list) or not all(isinstance(one, str) for one in dzialania):
            return "dzialania to lista nazw."
        try:
            shaped["konta"] = sorted({str(UUID(one)) for one in konta})
        except ValueError:
            return "konta zawiera napis, który nie jest identyfikatorem konta."
        shaped["dzialania"] = sorted({one.strip().upper() for one in dzialania})
        limity = args.get("limity")
        if limity is not None and not isinstance(limity, dict):
            return "limity to obiekt z liczbami."
        shaped["limity"] = dict(limity) if limity else None
        per_account = args.get("limity_konta")
        if per_account is not None:
            if not isinstance(per_account, dict) or not all(
                isinstance(one, dict) for one in per_account.values()
            ):
                return "limity_konta to obiekt: identyfikator konta -> obiekt z liczbami."
            try:
                shaped["limity_konta"] = {
                    str(UUID(str(key))): dict(value) for key, value in per_account.items()
                } or None
            except ValueError:
                return "limity_konta zawiera klucz, który nie jest identyfikatorem konta."
        else:
            shaped["limity_konta"] = None
        dni = args.get("dni")
        if dni is not None and (isinstance(dni, bool) or not isinstance(dni, int)):
            return "dni to liczba: 7 albo 14."
        shaped["dni"] = dni
        return shaped

    async def _room_scope_token(self, args: dict[str, Any]) -> dict[str, Any]:
        from .registry import done

        shaped = self._scope_args(args)
        if isinstance(shaped, str):
            return self._gone(shaped, CODE_ARGUMENT)
        moment = self._scope_tokens.now()
        try:
            plan, previous = await self._scope_plan(shaped, now=moment)
        except CardConsentRefused as exc:
            return self._gone(
                exc.detail,
                exc.code,
                obecnosc_adres=self._presence_address(),  # type: ignore[attr-defined]
            )
        token, expires_at = self._scope_tokens.mint(
            purpose=shaped["akcja"], plan=plan, args=shaped, minted_at=moment
        )
        return {
            **done(
                zakres=scope_plan_json(plan, previous_mode=previous or None),
                wygasa_potwierdzenie=expires_at.isoformat(),
            ),
            # ONLY the card reads this, and nothing that is written to a trace or shown as text.
            "_mcp_meta": {META_TOKEN: token},
        }

    # -- room.pause, room.resume, room.sign_scope ---------------------------------------------

    async def _redeem_scope(
        self, args: dict[str, Any], purpose: str
    ) -> ScopeGrant | dict[str, Any]:
        token = str(args.get("token") or "")
        if not token:
            return self._gone(
                "Brak potwierdzenia kliknięciem. Zmianę zakresu zatwierdza człowiek w karcie, "
                "agent tego nie robi.",
                CODE_CONFIRMATION,
            )
        # THE TOKEN IS CONSUMED FIRST, before anything is looked at or written.
        try:
            return self._scope_tokens.redeem(token, purpose=purpose)
        except TokenRefused as exc:
            return self._gone(exc.detail, exc.code)

    async def _still_the_same(self, grant: ScopeGrant) -> ConsentPlan | dict[str, Any]:
        """The plan, built again from the inputs the grant kept, or the refusal. It must be the
        very plan the person was shown: anything else is a different sentence."""
        try:
            plan, _previous = await self._scope_plan(grant.args, now=grant.minted_at)
        except CardConsentRefused as exc:
            return self._gone(
                exc.detail,
                exc.code,
                obecnosc_adres=self._presence_address(),  # type: ignore[attr-defined]
            )
        if plan != grant.plan:
            return self._gone(
                "Zakres zmienił się od chwili, gdy pokazano potwierdzenie. Spróbuj ponownie.",
                CODE_SCOPE_MOVED,
            )
        return plan

    async def _scope_after(self, change: str, **extra: Any) -> dict[str, Any]:
        from .registry import done

        organization_id, workspace_id, project_id = self._ids()
        return done(
            zmiana=change,
            zakres=await scope_summary(
                self.pool,  # type: ignore[attr-defined]
                organization_id=organization_id,
                workspace_id=workspace_id,
                project_id=project_id,
            ),
            **extra,
        )

    async def _room_pause(self, args: dict[str, Any]) -> dict[str, Any]:
        granted = await self._redeem_scope(args, PURPOSE_PAUSE)
        if isinstance(granted, dict):
            return granted
        plan = await self._still_the_same(granted)
        if isinstance(plan, dict):
            return plan
        organization_id, workspace_id, project_id = self._ids()
        try:
            await pause_for_card(
                self.pool,  # type: ignore[attr-defined]
                plan,
                organization_id=organization_id,
                workspace_id=workspace_id,
                project_id=project_id,
                host=self._host(),
            )
        except PresenceRefusal as exc:
            return self._gone(str(exc), CODE_SCOPE_MOVED)
        return await self._scope_after("WSTRZYMANO")

    async def _room_resume(self, args: dict[str, Any]) -> dict[str, Any]:
        granted = await self._redeem_scope(args, PURPOSE_RESUME)
        if isinstance(granted, dict):
            return granted
        plan = await self._still_the_same(granted)
        if isinstance(plan, dict):
            return plan
        organization_id, workspace_id, project_id = self._ids()
        try:
            await resume_for_card(
                self.pool,  # type: ignore[attr-defined]
                plan,
                organization_id=organization_id,
                workspace_id=workspace_id,
                project_id=project_id,
                host=self._host(),
            )
        except PresenceRefusal as exc:
            return self._gone(str(exc), CODE_SCOPE_MOVED)
        return await self._scope_after("WZNOWIONO", tryb=plan.mode)

    async def _room_sign_scope(self, args: dict[str, Any]) -> dict[str, Any]:
        granted = await self._redeem_scope(args, PURPOSE_SET)
        if isinstance(granted, dict):
            return granted
        plan = await self._still_the_same(granted)
        if isinstance(plan, dict):
            return plan
        if plan.kind != CONSENT_SET:  # cannot happen: the purpose says so; belt and braces
            return self._gone("To nie jest plan podpisu.", CODE_SIGNING)
        organization_id, workspace_id, project_id = self._ids()
        try:
            await sign_for_card(
                self.pool,  # type: ignore[attr-defined]
                plan,
                organization_id=organization_id,
                workspace_id=workspace_id,
                project_id=project_id,
                host=self._host(),
            )
        except PresenceRefusal as exc:
            return self._gone(str(exc), CODE_SIGNING)
        except ConsentError as exc:  # not exactly one operator to file the signature under
            return self._gone(
                f"{exc}. Podpisz zakres w Obecności.",
                CODE_SIGNING,
                obecnosc_adres=self._presence_address(),  # type: ignore[attr-defined]
            )
        return await self._scope_after("USTAWIONO", podpisano=True)
