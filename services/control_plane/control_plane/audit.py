"""The ONE entry point into `audit_events` (L5195, "audit events" in the scope of E1.F1).

`AuditWriter` takes an **open transaction** — an `asyncpg.Connection` the caller has already put
inside `connection.transaction()` — and never a pool.

Why that is the whole design and not a preference:

    A writer that opens its own connection passes every test shaped "the entry exists" and
    records audit for commands that FAILED. Its row commits independently, so a rollback of the
    domain command leaves an audit trail describing something that never happened. Evidence
    pointing at an event that did not occur is worse than no evidence, and it looks identical to
    a working audit log from the outside.

So the entry commits with the command and disappears with its rollback, and `record()` refuses
a connection with no transaction open — outside one, asyncpg autocommits each statement, which
is the failure above with extra steps.

The second half of this module is about what may be written, and it exists because
**`audit_events` is append-only**: the row trigger from 0001 refuses UPDATE and DELETE and the
statement trigger from 0002 refuses TRUNCATE. A secret written here once cannot be removed by
any ordinary path — not by a migration correcting a mistake, not by an operator. Hence:

*   a **closed list of `kind` values** — a typo must be a refusal, not a new category. An open
    vocabulary makes the log unqueryable in the way that matters: a reader filtering by kind
    cannot tell "nothing happened" from "somebody spelled it differently";
*   an **allowlist of payload keys**, and scalars only — a nested object is a place for keys
    nobody reviewed;
*   a **scan of the serialized payload**, modelled on
    `mcp_sidecar.capability.forbidden_field_violations` (read that function): it looks at the
    SERIALIZATION rather than at a list of known key names, because the problem is never the
    name of the field — it is that the VALUE carries a credential hash, a session cookie or a
    private key, whatever somebody chose to call the field holding it.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

import asyncpg

#: The closed vocabulary of `audit_events.kind` for phase E1.F1. Every value names something the
#: phase actually does; nothing here is speculative, because a kind nobody writes is a filter
#: option that always returns nothing.
#:
#:   * the session pair — this package, the operator boundary of D-016,
#:   * the two job entries — the queue and the lease reaper (L5194, L5212-5214),
#:   * the approval and intent entries — the approval chain, whose DoD is "approval wskazuje
#:     dokładny payload hash" and "żaden zapisujący side effect nie omija `ExecutionIntent`"
#:     (L5217-5218).
#:
#: Adding a value is a deliberate edit of this line, visible in a diff.
AUDIT_KINDS: frozenset[str] = frozenset(
    {
        "OPERATOR_SESSION_OPENED",
        "OPERATOR_SESSION_CLOSED",
        "JOB_STATE_CHANGED",
        "JOB_LEASE_EXPIRED",
        "APPROVAL_DECISION_RECORDED",
        "EXECUTION_INTENT_ISSUED",
        "EXECUTION_INTENT_CONSUMED",
        # Outcome A (docs/OUTCOMES.md §5 row 8) — the operator's four actions on the observe module.
        "COMMUNITY_OBSERVATION_ENABLED",
        # D-046: agent i człowiek robią to samo tą samą usługą, więc wyłączenie obserwacji
        # też musi mieć swój wpis. Jeden rodzaj na oba kierunki ukryłby kierunek, a to
        # pierwsze pytanie po tym, jak społeczność przestanie się czytać.
        "COMMUNITY_OBSERVATION_DISABLED",
        # 0025 (P4 etap C): człowiek rozstrzyga, czy w tej społeczności wolno zakładać NASZE
        # własne wątki. Osobny rodzaj od włączenia obserwacji, bo to są dwie różne zgody:
        # „czytajmy tam" i „piszmy tam od siebie", i tylko drugiej trzeba będzie kiedyś bronić.
        "COMMUNITY_OWN_POSTS_DECIDED",
        "USER_PROFILE_SAVED",
        "JUDGMENT_FEEDBACK_RECORDED",
        "WAITING_JOBS_RESUMED",
        # Plan B level 2 (D-026, docs/OUTCOMES.md §8) — the operator opens the capsule's screen for
        # a human login and closes it again. Who opened a screen, and when, is exactly what an
        # audit of a browser that holds the founder's account must be able to answer.
        "CAPSULE_SESSION_OPENED",
        "CAPSULE_SESSION_CONFIRMED",
        # P1 (ROADMAP-PIVOT, D-040) — the user's own structure: workspaces, projects, person
        # profiles, and which platform account works where. Attaching and detaching are SEPARATE
        # kinds, and so are assigning and unassigning: "konto X przestało należeć do projektu Y"
        # is the entry somebody will look for after a publication appears under the wrong
        # identity, and a single ACCOUNT_LINK_CHANGED kind would hide the direction.
        "WORKSPACE_CREATED",
        "PROJECT_CREATED",
        "PROJECT_UPDATED",
        "PERSON_PROFILE_CREATED",
        "SOCIAL_ACCOUNT_REGISTERED",
        # D-046: odczyt `/api/me.json` z kapsuły potwierdził, że konto jest zalogowane, i
        # wiersz przeszedł do READY. Osobny rodzaj od rejestracji, bo to są dwa różne
        # zdarzenia: „ktoś dodał konto" i „przeglądarka naprawdę jest na nim zalogowana".
        "SOCIAL_ACCOUNT_VERIFIED",
        "ACCOUNT_ATTACHED_TO_PROFILE",
        "ACCOUNT_DETACHED_FROM_PROFILE",
        "ACCOUNT_ASSIGNED_TO_PROJECT",
        "ACCOUNT_UNASSIGNED_FROM_PROJECT",
        # P1 etap 2 — access to the ENGINE, kept apart from the social accounts above because
        # confusing the two is the mistake ZAKRES-FUNKCJONALNY §2 names first. Registering and
        # starting a login are separate kinds: "kto podłączył konto z subskrypcją" and "kto
        # uruchomił logowanie" are different questions, and the second one can happen many times
        # for one connection.
        "CODEX_CONNECTION_REGISTERED",
        "CODEX_CONNECTION_LOGIN_STARTED",
        "CODEX_CONNECTION_DISCONNECTED",
        # P2 (ROADMAP-PIVOT) — powierzenie celu i trzy rzeczy, które człowiek robi z trwającą
        # pracą. Osobne rodzaje, nie jeden SESSION_CHANGED: „kto kazał agentowi przestać”
        # i „kto kazał mu ruszyć dalej” to dwa różne pytania, a jeden rodzaj chowałby kierunek,
        # dokładnie tak jak przy przypięciach kont w P1.
        "AGENT_SESSION_STARTED",
        "AGENT_SESSION_MESSAGE_ADDED",
        "AGENT_SESSION_INTERRUPTED",
        "AGENT_SESSION_RESUMED",
        # P3 etap A (ROADMAP-PIVOT) - wiedza z pochodzeniem. Dodanie materiału, wyszukanie,
        # zapis ustalenia, poprawka i wycofanie to pięć osobnych rodzajów z tego samego powodu,
        # dla którego przypięcie i odpięcie konta są osobne: „kto wycofał ustalenie” i „kto je
        # poprawił” to dwa różne pytania, a jeden rodzaj KNOWLEDGE_CHANGED chowałby kierunek.
        # Adres materiału nie trafia do payloadu, bo jest tekstem, który wpisał człowiek.
        "KNOWLEDGE_SOURCE_ADDED",
        "KNOWLEDGE_SOURCE_REFRESH_REQUESTED",
        "KNOWLEDGE_SEARCH_RUN",
        "KNOWLEDGE_ITEM_WRITTEN",
        "KNOWLEDGE_ITEM_CORRECTED",
        "KNOWLEDGE_ITEM_RETRACTED",
        # P5 etap A (ROADMAP-PIVOT) - pętla uczenia. Krótka korekta człowieka pod publikacją to
        # osobny rodzaj od zwykłego zapisu ustalenia: „kto poprawił agenta i kiedy" to pytanie,
        # które pada dokładnie wtedy, gdy agent zmienia zachowanie, i musi dać się odfiltrować.
        # Przemiał wyników nie woła modelu i nie zostawia wpisu na publikację; wpis zostawia
        # człowiek przy swojej decyzji i ten, kto zlecił pomiar.
        "KNOWLEDGE_FEEDBACK_RECORDED",
        "KNOWLEDGE_EXPERIENCE_SWEEP_REQUESTED",
        # P4 (ROADMAP-PIVOT) - powierzenie projektu agentowi. Cztery osobne rodzaje z tego
        # samego powodu, dla którego przypięcie i odpięcie konta są osobne: „kto podpisał
        # zakres", „kto go wstrzymał", „kto wznowił" i „kto odwołał" to cztery różne pytania,
        # a wstrzymanie i odwołanie NIE są tym samym zdarzeniem: pierwsze wraca tym samym
        # podpisem, drugie nie wraca nigdy. Zakres i budżet nie trafiają do payloadu; jest tam
        # `payload_hash`, czyli odcisk tego, co podpisano, i po nim wiadomo, która wersja to była.
        "PROJECT_DELEGATION_SIGNED",
        "PROJECT_DELEGATION_PAUSED",
        "PROJECT_DELEGATION_RESUMED",
        "PROJECT_DELEGATION_REVOKED",
        # 0033: the signed deadline passed and the product stopped the background work itself.
        "PROJECT_DELEGATION_EXPIRED",
        # P7 etap A (ROADMAP-PIVOT) - usługa dla kolejnego klienta. Kto założył przestrzeń, kogo
        # zaprosił, kto dołączył i komu odebrano dostęp to cztery pytania, które padają po tym,
        # jak w cudzych danych pojawi się ktoś nieoczekiwany; jeden rodzaj MEMBERSHIP_CHANGED
        # chowałby kierunek tak samo jak przy przypięciach kont w P1. Eksport i usunięcie są tu,
        # bo wyniesienie całej organizacji i jej skasowanie to dwie rzeczy, o których dziennik
        # musi umieć powiedzieć, kto i kiedy.
        "ORGANIZATION_REGISTERED",
        "ORGANIZATION_MEMBER_INVITED",
        "ORGANIZATION_MEMBER_JOINED",
        "ORGANIZATION_MEMBER_REVOKED",
        "USER_EMAIL_SET",
        "DATA_EXPORT_REQUESTED",
        "DATA_EXPORT_READY",
        "ORGANIZATION_DELETION_REQUESTED",
        "ORGANIZATION_DELETION_CANCELLED",
        "ORGANIZATION_PURGED",
        # P7 etap B: ślad, że system poprosił o kopię zapasową. Sam plik powstaje poza bazą
        # (`tools/dev/backup.sh`), bo spis kopii trzymany w bazie, którą się odtwarza, jest
        # spisem, którego w momencie odtwarzania nie ma. Ten wpis odróżnia „kopii nie zrobiono"
        # od „nikt jej nie zamówił", i tylko na to pytanie odpowiada.
        "BACKUP_REQUESTED",
        # P1 z 0022: nowa wersja głosu osoby. Osobny rodzaj od PERSON_PROFILE_CREATED, bo
        # „kto opisał, jak ta osoba mówi" to pytanie, które pada po komentarzu brzmiącym nie jak
        # ona; sam numer wersji i profil, bo treść głosu jest prozą i mieszka w swojej tabeli.
        "PERSON_VOICE_VERSION_WRITTEN",
        # P4 i P5: korekta kierunku wpisana po powrocie. Osobny rodzaj od zwykłej wiadomości do
        # sesji, bo „człowiek wrócił i zmienił kierunek" to zdarzenie, którego szuka się potem
        # w dzienniku, gdy agent zaczął pisać inaczej. Treści korekty tu nie ma: jest prozą.
        "PROJECT_RETURN_CORRECTION",
        # P7 awaryjnie: hasło ustawione z wiersza poleceń, gdy człowiek zapomniał własnego.
        # Osobny rodzaj od USER_EMAIL_SET, bo „ktoś zmienił hasło poza aplikacją" to pytanie,
        # które pada po włamaniu, i musi dać się odfiltrować. W payloadzie sam identyfikator.
        "USER_PASSWORD_RESET",
        # Zmiana sufitu całej organizacji. Osobny rodzaj od zmian projektu, bo „kto podniósł
        # limit organizacji" to inne pytanie niż „kto zmienił budżet jednego projektu".
        "ORG_LIMITS_CHANGED",
        # Wtyczka (Codex, Claude Code): agent człowieka dostał zdolność do narzędzi Wenext w
        # rozmowie projektu. Osobny rodzaj od AGENT_SESSION_STARTED, bo to nie człowiek na
        # dashboardzie powierzył cel, tylko cudzy proces dostał dostęp do bazy, i pytanie „kto
        # i kiedy dostał taką zdolność" musi dać się odfiltrować. Aktor to `plugin:<agent>`; w
        # payloadzie same identyfikatory i `created` (nowa rozmowa czy ta sama).
        "PLUGIN_SESSION_STARTED",
    }
)

#: Keys a payload may carry. Everything here is an identifier, a state name, a hash or a code —
#: nothing free-form, nothing that could reasonably hold a secret by mistake. `reason_code` is a
#: code and not a `reason`: a free-text reason is precisely where a copied credential ends up.
ALLOWED_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "attempt_id",
        "attempt_no",
        "community_id",
        "count",
        "decision",
        "expires_at",
        "from_state",
        "intent_id",
        "job_id",
        "judgment_id",
        "lock_version",
        "payload_hash",
        "proposal_revision_id",
        "reason_code",
        "to_state",
        "verdict",
        "version",
        # P1: identifiers and closed-list values only, as everywhere above. The NAME of a project
        # or a profile is deliberately absent — it is free text the user typed, and free text is
        # exactly where a copied credential ends up in an append-only table nobody can edit.
        "connection_id",
        "created",
        # `'.'` or a UUID, and the CHECK in 0014 allows nothing else. A directory NAME is not a
        # secret; what lives in it is, and that never comes near this table.
        "home_ref",
        "kind",
        "mode",
        "person_profile_id",
        "platform",
        "project_id",
        # P2: identyfikator sesji agenta. Identyfikator, nie treść: cel i wiadomości człowieka
        # są prozą i mieszkają w `session_events`, a nie w dzienniku audytu, którego payload
        # jest append-only i celowo nie przyjmuje pól wolnego tekstu.
        "session_id",
        "social_account_id",
        # P3: identyfikatory materiału i ustalenia oraz stan odczytu ('FETCHED'/'UNAVAILABLE').
        # Ani adres, ani zdanie ustalenia tu nie trafiają: jedno i drugie jest prozą, a payload
        # audytu przyjmuje identyfikatory i wartości z zamkniętych list.
        "item_id",
        # P7: identyfikatory użytkownika, zaproszenia i joba danych oraz rola z zamkniętej listy
        # ('OWNER'/'MEMBER'). Adresu e-mail tu NIE MA i nie będzie: to dane osobowe w tabeli,
        # której nie da się poprawić ani skasować, a identyfikator wskazuje tę samą osobę.
        "user_id",
        "invitation_id",
        "role",
        # P5: identyfikator publikacji, której dotyczy korekta człowieka. Treść notatki tu NIE
        # trafia: to wolny tekst, a payload audytu przyjmuje identyfikatory i wartości z list.
        "request_id",
        "source_id",
        "status",
        # P4: identyfikator powierzenia. Zakres i budżet tu nie trafiają, bo ich odcisk
        # (`payload_hash`) mówi to samo, a nie da się go przeczytać jako listy kont.
        "delegation_id",
        "workspace_id",
    }
)

#: Patterns no payload may contain, matched against the SERIALIZED payload. The shape of
#: `mcp_sidecar.capability.FORBIDDEN_PATTERNS`, for the same reason: a field name tells you
#: nothing about what its value holds. Descriptions are Polish — they end up in an error the
#: operator reads (D-010).
FORBIDDEN_PAYLOAD_PATTERNS: tuple[tuple[str, str], ...] = (
    # `(?i)` MUST sit at the START of the whole pattern: since Python 3.11 a global flag in
    # the middle of an alternation raises `re.error`, and a pattern compiled only on the
    # first scan would blow up in production rather than here.
    (r"\$argon2", "hash Argon2id poświadczenia operatora"),
    (r"(?i)wenext_operator_session", "ciasteczko sesji operatora"),
    (r"(?i)\bauthorization\b", "nagłówek Authorization"),
    (r"(?i)\bbearer\b", "token bearer"),
    (r"(?i)\bpassword\b|\bhasł|\bhaslo\b", "hasło"),
    (r"(?i)\bsecret\b|\bsekret", "sekret"),
    (r"(?i)\bcookie\b", "ciasteczko"),
    (r"-----BEGIN", "klucz prywatny"),
    (r"(?i)\bcdp[-_]?(port|url|endpoint)\b", "uchwyt CDP"),
    (r"(?i)\bws://", "adres WebSocket debuggera"),
)

#: Values a payload may hold. Scalars only: a nested dict or list would carry keys past the
#: allowlist, which is the allowlist quietly not applying.
_SCALAR_TYPES = (str, int, float, bool, type(None))


class ForbiddenAuditEntry(Exception):
    """The entry breaks a rule of this module, so nothing is written.

    Raised BEFORE the insert, always. `audit_events` cannot be edited or deleted, so validation
    after the write would have nothing left to protect.
    """


def forbidden_payload_violations(payload: dict[str, Any]) -> list[str]:
    """Descriptions of everything secret-shaped in this payload. Empty list means clean.

    Scans the serialization, not a list of known keys — same reasoning as the sidecar's
    `forbidden_field_violations`, and the same reason it is a separate function: a test can ask
    it directly instead of inferring the answer from an exception.
    """
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return [
        description
        for pattern, description in FORBIDDEN_PAYLOAD_PATTERNS
        if re.search(pattern, text)
    ]


class AuditWriter:
    """Writes audit entries inside the caller's transaction, and validates before writing.

    Constructed per command, around the connection that command is already using:

        async with connection.transaction():
            await AuditWriter(connection).record(...)
            await apply_transition(connection, ...)

    Both rows share one commit and one rollback. That is the property the whole class exists for.
    """

    def __init__(self, connection: asyncpg.Connection) -> None:
        self._connection = connection

    async def record(
        self,
        *,
        kind: str,
        organization_id: UUID,
        subject_ref: str,
        actor_ref: str,
        workspace_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        """Append one entry and return its id. Raises `ForbiddenAuditEntry` instead of bending.

        `occurred_at` is left to the column default, i.e. to the DATABASE clock — the same rule
        as everywhere else in this schema (L2668). A timestamp taken from the writing process
        would let two components disagree about the order of events in the one table meant to
        settle it.
        """
        self._require_open_transaction()
        self._require_known_kind(kind)
        self._require_reference("subject_ref", subject_ref)
        self._require_reference("actor_ref", actor_ref)
        checked_payload = self._checked_payload(payload)

        # THE ONLY insert into the audit table in this whole package. A second one anywhere
        # bypasses every rule above, which is why the Definition of Done greps for the statement
        # and expects exactly ONE hit — the line below. This comment deliberately does not spell
        # the statement out: a comment quoting it would make that grep return two places and the
        # count would stop meaning anything.
        return await self._connection.fetchval(
            """
            INSERT INTO audit_events
                (organization_id, workspace_id, kind, subject_ref, actor_ref, payload)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            RETURNING id
            """,
            organization_id,
            workspace_id,
            kind,
            subject_ref,
            actor_ref,
            json.dumps(checked_payload, ensure_ascii=False),
        )

    def _require_open_transaction(self) -> None:
        in_transaction = getattr(self._connection, "is_in_transaction", None)
        if not callable(in_transaction) or not in_transaction():
            raise ForbiddenAuditEntry(
                "AuditWriter dostał połączenie BEZ otwartej transakcji. Poza transakcją asyncpg "
                "zatwierdza każde zapytanie osobno, więc wpis audytu przeżyłby porażkę komendy, "
                "którą opisuje — dziennik mówiłby o zdarzeniu, które się nie stało. Otwórz "
                "`async with connection.transaction():` wokół komendy i tego wpisu"
            )

    @staticmethod
    def _require_known_kind(kind: str) -> None:
        if kind not in AUDIT_KINDS:
            raise ForbiddenAuditEntry(
                f"nieznany rodzaj zdarzenia audytowego {kind!r}. Lista jest zamknięta: "
                f"{', '.join(sorted(AUDIT_KINDS))}. Literówka ma być odmową, nie nową kategorią "
                "— czytający dziennik nie odróżnia „nic się nie stało” od „zapisano inaczej”"
            )

    @staticmethod
    def _require_reference(name: str, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ForbiddenAuditEntry(
                f"{name} jest puste. Wpis audytowy bez wskazania podmiotu albo aktora nie "
                "odpowiada na pytanie, po co dziennik istnieje"
            )

    @staticmethod
    def _checked_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise ForbiddenAuditEntry(f"payload audytu musi być słownikiem, jest {type(payload)!r}")

        forbidden_keys = sorted(set(payload) - ALLOWED_PAYLOAD_KEYS)
        if forbidden_keys:
            raise ForbiddenAuditEntry(
                f"klucze payloadu poza allowlistą: {', '.join(forbidden_keys)}. Dozwolone: "
                f"{', '.join(sorted(ALLOWED_PAYLOAD_KEYS))}. audit_events jest append-only — "
                "pola, którego nikt nie przejrzał, nie da się potem usunąć"
            )

        non_scalar = sorted(
            key for key, value in payload.items() if not isinstance(value, _SCALAR_TYPES)
        )
        if non_scalar:
            raise ForbiddenAuditEntry(
                f"wartości nieskalarne pod kluczami: {', '.join(non_scalar)}. Zagnieżdżona "
                "struktura wnosi klucze, których allowlista nie widzi"
            )

        violations = forbidden_payload_violations(payload)
        if violations:
            raise ForbiddenAuditEntry(
                "payload audytu niesie treść zakazaną: " + "; ".join(violations) + ". "
                "audit_events jest append-only, więc sekret zapisany tam raz jest nieusuwalny "
                "zwykłą ścieżką"
            )
        return payload
