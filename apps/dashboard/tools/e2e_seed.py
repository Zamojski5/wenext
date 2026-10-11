"""Insert the rows the end-to-end run looks for, with raw SQL and nothing else.

WHY RAW SQL AND NOT THE DEVELOPMENT CLI. `e2e/dashboard_reads_postgres.e2e.test.ts` claims that the
screen reads PostgreSQL. If the rows were created by `control_plane.jobs.devcli`, a bug that made
the dashboard render its own in-memory idea of a queue could still show them — the two would share
code. Inserting through `asyncpg` with literal statements means the ONLY path from these rows to the
HTML is: PostgreSQL, control plane HTTP, Next.js. That is the same discipline
`services/control_plane/tests/test_jobs_api.py` follows for the same reason.

THE ONE THING IT DOES IMPORT is `control_plane.auth.hash_credential`. Hashing is not the subject of
the test, and a second Argon2 configuration here could differ from the one the boundary verifies
against — a fixture that fails to log in for a reason unrelated to the code under test. The CHECK
`credential_hash_is_argon2id` in migration 0002 would also reject anything else.

Prints one `KEY=value` line per fact the suite needs, for the shell to export. Anything unexpected
raises: a seeding script that half-succeeds and exits zero would leave the tests looking for rows
that are not there, and the failure would be blamed on the dashboard.

OUTCOME A ROWS (2026-09-03, for `e2e/community_reads_postgres.e2e.test.ts`): one community, one
observed thread, one profile version and ONE judgment — `ENGAGE`, so the screen has a card with
the three feedback buttons — and deliberately NO `IGNORE` judgment, NO picture and NO feedback:
the suite asserts those three as MEASURED empties (the read happened, the answer was empty) and
then writes the first feedback row itself through the dashboard's own form handler. Same rule as
above: raw SQL, no `ObservationStore`, so the only path from these rows to the HTML is
PostgreSQL → control plane HTTP → Next.js.

DOKUMENTY PROJEKTU (2026-09-20, dla `e2e/project_documents.e2e.test.ts`) SĄ JEDYNYM WYJĄTKIEM
od reguły „tylko surowy SQL" i wyjątek jest świadomy. Ustalenia, na których dokumenty stoją, są
wstawiane tak jak wszystko inne tutaj, natomiast same dokumenty powstają przez
`control_plane.documents.DocumentStore.rebuild_project`, czyli przez JEDYNY kod, który potrafi je
zapisać. Powód: reguła wyżej broni ŚCIEŻKI ODCZYTU, żeby zasiew nie dzielił kodu z tym, co
renderuje HTML. Generator jest ścieżką ZAPISU i nie występuje między PostgreSQL a przeglądarką,
a zasiew zbudowany własnymi wstawkami umiałby stworzyć dokument, którego produkt nigdy nie
wyprodukuje (zdanie bez podstawy, numer wersji z palca), i wtedy ekran byłby mierzony na stanie,
którego nikt nie zobaczy. Wołany jest DWA RAZY, z jednym nowym ustaleniem pomiędzy, żeby na
ekranie stał dokument w wersji 2 obok dokumentów w wersji 1: bez tego suita nie odróżniłaby
„pokazuje najnowszą wersję" od „pokazuje jedyną, jaka jest".

P1 ROWS (2026-09-19, for `e2e/workspace_writes_postgres.e2e.test.ts`): NONE, deliberately. That
suite creates its own workspace, projects, person profiles, platform accounts and assignments
through the dashboard's real forms, and then looks for them again after a reload and a second
login. Seeding them here would let the screens pass while writing nothing — which is the exact
claim P1 has to make good on ("nie kończ na makiecie: ekran bez zapisu do backendu nie zalicza
etapu"). The only thing it takes from this script is `WENEXT_E2E_WORKSPACE_ID`, the workspace the
organization already has, so the suite can tell an existing workspace from the one it creates.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import uuid

import asyncpg
from control_plane.auth import hash_credential
from control_plane.documents import DocumentStore

#: The password the run logs in with. A constant, because the tests assert on the OUTCOME of
#: verification, not on secrecy — and a random password per run would make a failing login
#: indistinguishable from a mistyped fixture. Same reasoning as `conftest.OPERATOR_PASSWORD`.
OPERATOR_PASSWORD = "operator-e2e-hasło-2026"

#: Szkic odpowiedzi (D-046). Treść krótka i rozpoznawalna, bo suita szuka jej w HTML-u.
DRAFT_TEXT = "Zaczalbym od jednej powtarzajacej sie potrzeby (e2e)."
DRAFT_TARGET = "t3_e2edraft"

#: Rozmowa z KARTAMI (D-046). Ślad niesie wyniki narzędzi z kartami, a stan produktu decyduje,
#: w jakim stanie te karty się pokażą: konto z `social_accounts`, społeczność z tego, czy ktoś
#: ją obserwuje. Dlatego zasiew wstawia jedno i drugie, a nie same zdarzenia.
CARDS_GOAL = "Sprawdz, gdzie warto promowac ten projekt (e2e)."
CARDS_LOGIN = "demo_maintainer_e2e"
#: KLUCZE BEZ MYŚLNIKA, i to nie jest kosmetyka. Adapter Reddita sprawdza klucz wzorcem
#: `^r/([A-Za-z0-9_]{2,21})$` (`reddit_payload.subreddit_name`), więc `r/saas-e2e` jest ODRZUCANE
#: z powodem „klucz community Reddita ma postać r/<nazwa>". Pierwszy przebieg tej suity tak
#: właśnie padł: przycisk „Dołącz" działał, trasa była dobra, a odmowa przychodziła z walidacji
#: klucza. Zasiew ma podawać dane, które produkt przyjmuje, bo inaczej mierzy własną literówkę.
CARDS_COMMUNITY = "r/saas_e2e"
CARDS_SECOND_COMMUNITY = "r/startups_e2e"
CARDS_WHY = "Rozmowy o produkcie i pierwszych klientach (e2e)."
CARDS_ERROR = "Platforma odmowila dolaczenia: konto bez potwierdzonego adresu (e2e)."
CARDS_PAUSED_LOGIN = "PausedAccountE2E"

#: Druga rozmowa projektu (D-045), zamknięta i z wynikiem. Tytuł na ekranie liczy control plane
#: z PIERWSZEGO ZDANIA celu, więc cel ma tu dwa zdania: gdyby miał jedno, test nie odróżniłby
#: „pokazujemy pierwsze zdanie" od „pokazujemy cały cel".
DONE_SESSION_GOAL = "Sprawdz pore publikacji (e2e). Potem opisz, co z tego wyszlo."
DONE_SESSION_RESULT = "Wieczorem odzew jest mniejszy, przenosimy publikacje na rano (e2e)."

#: Zdania, które suita dokumentów szuka na ekranie. Trzymane jako stałe, bo tekst wpisany w dwóch
#: miejscach rozjeżdża się przy pierwszej poprawce, a wtedy czerwony test mówi o literówce w
#: zasiewie, nie o produkcie.
DOCUMENT_WORKED = "Komentarz z liczbami z produkcji dostal odpowiedz autora (e2e)"
DOCUMENT_FAILED = "Komentarz z linkiem w pierwszym zdaniu zniknal (e2e)"
DOCUMENT_PREFERENCE = "Odpowiadamy tylko tam, gdzie mamy liczby z produkcji (e2e)"
DOCUMENT_HYPOTHESIS = "Wieczorem odzew jest mniejszy (e2e)"

#: D-017 froze `attempt_limit` at 5 before the queue was written. Used here so the rendered
#: "próby: 2 / 5" on the screen is the value the phase actually decided on.
ATTEMPT_LIMIT = 5


async def _seed_draft(dsn: str, connection, organization_id, workspace_id, project_id, session_id):
    """Propozycja publikacji z jedną rewizją, zapisana PRZEZ USŁUGĘ, nie wstawką (D-046).

    DLACZEGO NIE SUROWY `INSERT`. Rewizja jest przedmiotem zgody: jej `payload_hash` liczy się
    z dwunastu pól (`ApprovalHashInput`), a zgoda dotyczy DOKŁADNIE tego odcisku. Wstawka
    zbudowana ręcznie dałaby wiersz o kształcie, którego produkt nigdy nie wyprodukuje, i suita
    mierzyłaby ekran na szkicu, którego nie da się ani poprawić, ani zatwierdzić prawdziwą trasą.

    KARTA IDZIE OSOBNO, w `payload.karty` zdarzenia TOOL_RESULT, tak samo jak przy koncie i
    społeczności: projekcja bierze z rejestru STAN, ale tożsamość i kolejność ze śladu rozmowy.

    BEZ POWIERZENIA, świadomie. Projekt nie ma żywej delegacji, więc karta dostaje tylko akcję
    `popraw`; `opublikuj` NIE JEST wystawiane, bo zatwierdzenie i tak odmówiłoby, a przycisk,
    który obiecuje publikację i jej nie robi, kłamie dopiero po kliknięciu.
    """
    from datetime import UTC, datetime, timedelta

    import asyncpg as _asyncpg
    from community_os_contracts import ApprovalHashInput, SemanticCapability
    from community_os_contracts.execution import ActionProposalState
    from control_plane.approval.store import ApprovalStore

    account_id = await connection.fetchval(
        "SELECT id FROM social_accounts WHERE organization_id = $1 AND external_account_id = $2",
        organization_id,
        CARDS_LOGIN,
    )
    if account_id is None:
        raise RuntimeError(f"nie ma konta {CARDS_LOGIN}; zasiew szkicu nie ma na czym stanąć")

    # KONTO MUSI BYĆ PRZYPISANE DO PROJEKTU, inaczej rewizja do tego projektu nie należy.
    # `api/project_approvals.py::_revision_row` łączy rewizję z projektem przez ŻYWE przypisanie
    # konta (`project_account_assignments`), więc bez tego wiersza poprawka szkicu dostaje 404 i
    # ekran wygląda, jakby przycisk nic nie robił. Zmierzone pierwszym przebiegiem tej suity:
    # rewizja powstała, karta się narysowała, a `revise` odmawiał, bo szkic wisiał w powietrzu.
    # To jest zwykły wiersz łączący, bez odcisku i bez zgody, więc wstawiany wprost.
    await connection.execute(
        "INSERT INTO project_account_assignments "
        "(organization_id, workspace_id, project_id, social_account_id) "
        "VALUES ($1, $2, $3, $4)",
        organization_id,
        workspace_id,
        project_id,
        account_id,
    )

    now = datetime.now(UTC).replace(microsecond=0)
    hash_input = ApprovalHashInput(
        organization=organization_id,
        workspace=workspace_id,
        assignment_version=session_id,
        account=account_id,
        platform="REDDIT",
        action_type=SemanticCapability.SUBMIT_COMMENT,
        target_external_id=DRAFT_TARGET,
        target_url=f"https://example.test/{DRAFT_TARGET}",
        source_fingerprint=f"session:{session_id}",
        text_and_media=DRAFT_TEXT,
        intelligence_profile_version=session_id,
        plan_version=project_id,
        memory_snapshot=session_id,
        adapter_version="wenext-session-draft/1",
    )

    pool = await _asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        store = ApprovalStore(pool)
        proposal_id = await store.create_proposal(
            organization_id=organization_id,
            workspace_id=workspace_id,
            social_account_id=account_id,
        )
        revision = await store.record_revision(
            organization_id=organization_id,
            workspace_id=workspace_id,
            proposal_id=proposal_id,
            revision_no=1,
            hash_input=hash_input,
            state=ActionProposalState.READY_FOR_APPROVAL,
            expires_at=now + timedelta(hours=12),
        )
    finally:
        await pool.close()

    base = f"/api/workspaces/{workspace_id}/projects/{project_id}/approvals/{revision.id}"
    card = {
        "karta_id": f"szkic:{revision.id}",
        "rodzaj": "SZKIC",
        "stan": "szkic",
        "powod": None,
        "akcje": [
            {
                "nazwa": "popraw",
                "trasa": {"metoda": "POST", "sciezka": f"{base}/revise"},
                "payload": {"text": DRAFT_TEXT},
            }
        ],
        "rewizja": str(revision.id),
        "tytul": "Szkic odpowiedzi",
        "spolecznosc": CARDS_COMMUNITY,
        "profil": "Anna (e2e)",
        "tresc": DRAFT_TEXT,
        "delegacja_pozwala": False,
        "dlaczego_bez_akcji": None,
    }
    seq = await connection.fetchval(
        "SELECT coalesce(max(seq), 0) + 1 FROM session_events WHERE session_id = $1", session_id
    )
    await connection.execute(
        """
        INSERT INTO session_events
            (organization_id, workspace_id, session_id, seq, kind, payload)
        VALUES ($1, $2, $3, $4, 'TOOL_RESULT', $5::jsonb)
        """,
        organization_id,
        workspace_id,
        session_id,
        seq,
        json.dumps({"tool": "draft.create", "karty": [card]}),
    )
    return {
        "WENEXT_E2E_DRAFT_REVISION": str(revision.id),
        "WENEXT_E2E_DRAFT_TEXT": DRAFT_TEXT,
    }


async def _knowledge(connection, organization_id, workspace_id, project_id, **row) -> uuid.UUID:
    """Jedno ustalenie w rejestrze, surowym SQL-em jak reszta tego pliku.

    Rodzaj decyduje o więzach (0016): FACT musi mieć źródło, PREFERENCE musi pochodzić od
    człowieka, EXPERIENCE musi mieć sytuację, decyzję i wynik. Dlatego wołający podaje je wprost,
    zamiast dostać domyślne, które kiedyś przestaną pasować i zamienią odmowę bazy w cichą zmianę
    znaczenia.
    """
    return await connection.fetchval(
        """
        INSERT INTO knowledge_items
            (organization_id, workspace_id, project_id, kind, statement, stated_by, payload)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        RETURNING id
        """,
        organization_id,
        workspace_id,
        project_id,
        row["kind"],
        row["statement"],
        row["stated_by"],
        json.dumps(row.get("payload") or {}),
    )


def _experience(thread: str, community: str, outcome: str) -> dict:
    """`payload` doświadczenia w kształcie, który pisze `knowledge/experience.py`.

    NIE „sytuacja jako zdanie". Więz `knowledge_items_experience_is_complete` (0016) pilnuje
    tylko OBECNOŚCI trzech kluczy, więc napis przechodzi przez bazę bez szemrania, a generator
    dokumentów czyta `situation.community` i na napisie się wywraca. Zmierzone tym zasiewem
    2026-09-20: `AttributeError: 'str' object has no attribute 'get'`. Zasiew ma odwzorowywać to,
    co produkt zapisuje naprawdę, więc odwzorowuje obiekt.
    """
    return {
        "situation": {
            "community": community,
            "platform": "reddit",
            "thread": thread,
            "post_age_hours": 3,
        },
        "decision": {"excerpt": "odpowiedzielismy w watku (e2e)", "length_chars": 32},
        "outcome": {"kind": outcome},
    }


async def _seed_documents(dsn: str, connection, organization_id, workspace_id, project_id) -> dict:
    """Ustalenia i dokumenty projektu dla `e2e/project_documents.e2e.test.ts`.

    DWA PRZEBIEGI GENERATORA, jeden nowy wynik pomiędzy. Po pierwszym każdy dokument ma wersję 1,
    po drugim „Co działa" ma wersję 2 i o jedno zdanie więcej, a pozostałe zostają na jedynce, bo
    ich wejście się nie zmieniło. Ekran ma na czym pokazać, że czyta NAJNOWSZĄ wersję.
    """
    await _knowledge(
        connection,
        organization_id,
        workspace_id,
        project_id,
        kind="PREFERENCE",
        statement=DOCUMENT_PREFERENCE,
        stated_by="USER",
    )
    await _knowledge(
        connection,
        organization_id,
        workspace_id,
        project_id,
        kind="INTERPRETATION",
        statement=DOCUMENT_HYPOTHESIS,
        stated_by="AGENT",
        payload={"hypothesis": True},
    )
    await _knowledge(
        connection,
        organization_id,
        workspace_id,
        project_id,
        kind="EXPERIENCE",
        statement=DOCUMENT_WORKED,
        stated_by="AGENT",
        payload=_experience("watek o wdrozeniach (e2e)", "r/devops (e2e)", "REPLIED"),
    )

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        store = DocumentStore(pool)
        first = await store.rebuild_project(
            organization_id=organization_id, workspace_id=workspace_id, project_id=project_id
        )
        if not first or not all(created for _, created in first):
            raise RuntimeError(f"pierwszy przebieg generatora nie zbudowal dokumentow: {first}")

        await _knowledge(
            connection,
            organization_id,
            workspace_id,
            project_id,
            kind="EXPERIENCE",
            statement=DOCUMENT_FAILED,
            stated_by="AGENT",
            payload=_experience("watek o cenach (e2e)", "r/devops (e2e)", "REMOVED"),
        )
        second = await store.rebuild_project(
            organization_id=organization_id, workspace_id=workspace_id, project_id=project_id
        )
    finally:
        await pool.close()

    # Nowy wynik pomiaru dotyka DWÓCH dokumentów: „co działa" i „społeczności", bo doświadczenie
    # niesie i wynik, i społeczność. Trzeci i czwarty stoją na preferencji i hipotezie, więc
    # zostają na wersji 1, i to ta różnica jest tu potrzebna: ekran ma obok siebie dokument
    # przepisany i dokument nietknięty.
    grew = {version.role: version for version, created in second if created}
    stayed = [version.role for version, created in second if not created]
    if "WHAT_WORKS" not in grew:
        raise RuntimeError(
            "drugi przebieg nie przepisal dokumentu WHAT_WORKS: "
            f"{[(one.role, created) for one, created in second]}"
        )
    if not stayed:
        raise RuntimeError(
            "drugi przebieg przepisal WSZYSTKIE dokumenty, wiec zaden nie zostal na wersji 1 "
            "i suita nie odrozni najnowszej wersji od jedynej"
        )
    what_works = grew["WHAT_WORKS"]
    if what_works.version != 2:
        raise RuntimeError(f"WHAT_WORKS ma wersje {what_works.version}, nie 2")

    # Odczyt kontrolny z bazy, nie z tego, co zwrócił store: zasiew, który wierzy własnemu
    # zapisowi, jest tego samego kształtu, co bramka wierząca własnemu werdyktowi.
    documents = await connection.fetchval(
        "SELECT count(*) FROM project_documents WHERE project_id = $1", project_id
    )
    if documents != len(second):
        raise RuntimeError(
            f"w bazie jest {documents} dokumentow, a generator zwrocil {len(second)}"
        )
    bare = await connection.fetchval(
        "SELECT count(*) FROM project_document_sentences s "
        "JOIN project_document_versions v ON v.id = s.version_id "
        "JOIN project_documents d ON d.id = v.document_id "
        "WHERE d.project_id = $1 AND cardinality(s.basis) = 0",
        project_id,
    )
    if bare != 0:
        raise RuntimeError(f"zasiew zapisal {bare} zdan bez podstawy")

    return {
        "WENEXT_E2E_DOCUMENT_ID": str(what_works.document_id),
        "WENEXT_E2E_DOCUMENT_SENTENCE": DOCUMENT_WORKED,
        "WENEXT_E2E_DOCUMENT_REMOVED_SENTENCE": DOCUMENT_FAILED,
    }


async def seed(dsn: str) -> dict[str, str]:
    connection = await asyncpg.connect(dsn)
    try:
        async with connection.transaction():
            # One enabled operator per organization is enforced by a partial unique index (0002).
            # The throwaway database is fresh, but disabling anything left behind keeps this script
            # idempotent enough to run twice against a kept stack.
            await connection.execute(
                "UPDATE operators SET disabled_at = now() WHERE disabled_at IS NULL"
            )

            organization_id = await connection.fetchval(
                "INSERT INTO organizations (name) VALUES ('e2e-dashboard') RETURNING id"
            )
            workspace_id = await connection.fetchval(
                "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws-e2e') RETURNING id",
                organization_id,
            )
            operator_id = await connection.fetchval(
                "INSERT INTO operators (organization_id, credential_hash) VALUES ($1, $2)"
                " RETURNING id",
                organization_id,
                hash_credential(OPERATOR_PASSWORD),
            )

            lease_owner = uuid.uuid4()

            # ---------------------------------------------------------------------------
            # Job 1: two attempts, the LATEST of them ending in SUBMIT_STARTED.
            #
            # That last detail is the point of the fixture. `contracts.may_retry_automatically`
            # returns False for SUBMIT_STARTED (L2673: the side effect may already have happened
            # on the platform), so the control plane sends `may_retry_automatically: false` and the
            # detail screen must print ZABRONIONE. A dashboard that recomputed the rule, inverted
            # it, or read the FIRST attempt instead of the last would render something else.
            # ---------------------------------------------------------------------------
            job_id = await connection.fetchval(
                """
                INSERT INTO jobs
                    (organization_id, workspace_id, job_type, routing_key, priority, payload_ref,
                     dispatch_version, status, not_before, attempt_limit, idempotency_key,
                     lock_version)
                VALUES ($1, $2, 'E2E_DASHBOARD', 'e2e.dashboard', 100, NULL, 2, 'FAILED', now(),
                        $3, $4, 3)
                RETURNING id
                """,
                organization_id,
                workspace_id,
                ATTEMPT_LIMIT,
                f"e2e-{uuid.uuid4()}",
            )

            # `running_attempt_is_not_completed` (0003): a non-RUNNING attempt MUST have
            # `completed_at`. Both rows below are FAILED, so both carry it.
            await connection.execute(
                """
                INSERT INTO job_attempts
                    (organization_id, job_id, attempt_no, dispatch_version, lease_generation,
                     lease_owner, status, heartbeat_at, expires_at, started_at, completed_at,
                     failure_class)
                VALUES ($1, $2, 1, 1, 1, $3, 'FAILED', now() - interval '5 minutes',
                        now() - interval '4 minutes', now() - interval '6 minutes',
                        now() - interval '4 minutes', 'FAILED_BEFORE_SIDE_EFFECT')
                """,
                organization_id,
                job_id,
                lease_owner,
            )
            await connection.execute(
                """
                INSERT INTO job_attempts
                    (organization_id, job_id, attempt_no, dispatch_version, lease_generation,
                     lease_owner, status, heartbeat_at, expires_at, started_at, completed_at,
                     failure_class)
                VALUES ($1, $2, 2, 2, 2, $3, 'FAILED', now() - interval '30 seconds',
                        now() + interval '30 seconds', now() - interval '90 seconds',
                        now() - interval '20 seconds', 'SUBMIT_STARTED')
                """,
                organization_id,
                job_id,
                lease_owner,
            )

            # ---------------------------------------------------------------------------
            # Job 2: never claimed. Its attempt list is a MEASURED empty, and "no attempts" must
            # not fall through to "retry allowed".
            # ---------------------------------------------------------------------------
            fresh_job_id = await connection.fetchval(
                """
                INSERT INTO jobs
                    (organization_id, workspace_id, job_type, routing_key, priority, payload_ref,
                     dispatch_version, status, not_before, attempt_limit, idempotency_key,
                     lock_version)
                VALUES ($1, $2, 'E2E_FRESH', 'e2e.fresh', 50, NULL, 1, 'READY',
                        now() + interval '10 minutes', $3, $4, 1)
                RETURNING id
                """,
                organization_id,
                workspace_id,
                ATTEMPT_LIMIT,
                f"e2e-fresh-{uuid.uuid4()}",
            )

            # ---------------------------------------------------------------------------
            # Outcome A: community → thread → profile → ONE ENGAGE judgment. Text values are ASCII
            # apart from the display name, so the suite can look for them in the served HTML
            # without reasoning about React's escaping of quotes.
            # ---------------------------------------------------------------------------
            community_id = await connection.fetchval(
                """
                INSERT INTO communities
                    (organization_id, workspace_id, platform, community_key, display_name,
                     access_mode, observe_enabled, poll_interval_seconds, last_polled_at,
                     watermark_created_at)
                VALUES ($1, $2, 'HACKER_NEWS', 'ask', 'Hacker News, Ask HN (e2e)',
                        'PUBLIC_API_NO_ACCOUNT', true, 600, now() - interval '3 minutes',
                        now() - interval '10 minutes')
                RETURNING id
                """,
                organization_id,
                workspace_id,
            )
            thread_external_id = f"e2e-{uuid.uuid4()}"
            thread_title = f"E2E watek {uuid.uuid4()}"
            thread_id = await connection.fetchval(
                """
                INSERT INTO observed_threads
                    (organization_id, workspace_id, community_id, external_id, url, link_url,
                     title, body, author, created_at_source, first_observed_at,
                     last_observed_at, raw_sha256)
                VALUES ($1, $2, $3, $4, $5, NULL, $6, 'Tresc watku e2e.', 'e2e-author',
                        now() - interval '20 minutes', now() - interval '3 minutes',
                        now() - interval '3 minutes', $7)
                RETURNING id
                """,
                organization_id,
                workspace_id,
                community_id,
                thread_external_id,
                f"https://news.ycombinator.com/item?id={thread_external_id}",
                thread_title,
                hashlib.sha256(thread_external_id.encode("utf-8")).hexdigest(),
            )
            # SPOŁECZNOŚĆ I WĄTEK, POD KTÓRYM STOI SZKIC. Bez nich karta po trasie poprawki nie
            # ma skąd wziąć nazwy społeczności do podpisu, bo trasa czyta ją z bazy po wątku, a
            # nie z identyfikatora celu (zrzut rundy 2, 21.09). Zasiew ma odwzorowywać prawdę:
            # agent proponuje odpowiedź pod wątkiem, który wcześniej obserwowaliśmy.
            #
            # `observe_enabled = false`, I TO JEST ISTOTNE, nie szczegół. Wiersz jest tu po to,
            # żeby podpis szkicu miał skąd wziąć NAZWĘ; obserwacja jest osobną rzeczą i osobnym
            # stanem karty. Przy `true` karta SPOŁECZNOŚĆ tej samej społeczności startuje w
            # stanie „obserwuje", więc nie ma na niej akcji „Obserwuj" i dwa przypadki
            # `conversation_cards.e2e` tracą swój przedmiot: pierwszy sprawdza, że propozycja ma
            # co zaproponować, drugi że kliknięcie przechodzi tą samą drogą, co narzędzie agenta.
            # Zmierzone 21.09: po dosianiu z `true` oba padły. „Znamy tę społeczność i jej nie
            # obserwujemy" jest przy tym prawdziwym stanem produktu (`stop_observing`), a nie
            # ustawką pod test.
            draft_community_id = await connection.fetchval(
                """
                INSERT INTO communities
                    (organization_id, workspace_id, platform, community_key, display_name,
                     access_mode, observe_enabled, poll_interval_seconds)
                VALUES ($1, $2, 'REDDIT', $3, $4, 'PUBLIC_API_NO_ACCOUNT', false, 600)
                RETURNING id
                """,
                organization_id,
                workspace_id,
                CARDS_COMMUNITY,
                f"Reddit · {CARDS_COMMUNITY}",
            )
            await connection.execute(
                """
                INSERT INTO observed_threads
                    (organization_id, workspace_id, community_id, external_id, url, title,
                     body, author, created_at_source, first_observed_at, last_observed_at,
                     raw_sha256)
                VALUES ($1, $2, $3, $4, $5, 'Watek pod szkicem (e2e)', 'Tresc watku e2e.',
                        'e2e-author', now() - interval '30 minutes',
                        now() - interval '5 minutes', now() - interval '5 minutes', $6)
                """,
                organization_id,
                workspace_id,
                draft_community_id,
                DRAFT_TARGET,
                f"https://www.reddit.com/{CARDS_COMMUNITY}/comments/e2edraft/x/",
                hashlib.sha256(DRAFT_TARGET.encode("utf-8")).hexdigest(),
            )
            profile_version_id = await connection.fetchval(
                """
                INSERT INTO user_profile_versions
                    (organization_id, workspace_id, version, knows_deeply, positions,
                     would_never_claim, voice_samples)
                VALUES ($1, $2, 1, 'kolejki na Postgresie (e2e)', 'nudna technologia wygrywa (e2e)',
                        'benchmarkow, ktorych nie robil (e2e)', '')
                RETURNING id
                """,
                organization_id,
                workspace_id,
            )
            judgment_id = await connection.fetchval(
                """
                INSERT INTO thread_judgments
                    (organization_id, workspace_id, thread_id, decision, reasoning,
                     what_user_could_add, confidence, user_profile_version_id,
                     community_picture_id, model, effort, prompt_name, prompt_sha256)
                VALUES ($1, $2, $3, 'ENGAGE',
                        'Autor pyta o to, co uzytkownik robil przez rok (e2e).',
                        'Liczby z roku produkcji (e2e).', 0.71, $4, NULL, 'e2e-model', 'high',
                        'judge_thread', $5)
                RETURNING id
                """,
                organization_id,
                workspace_id,
                thread_id,
                profile_version_id,
                hashlib.sha256(b"judge_thread.v1 e2e").hexdigest(),
            )

        # Sesja RUNNING dla regresji z audytu 19.09: strumień ma po limicie czasu poprosić
        # o wznowienie, a nie ogłosić koniec pracy. Musi być zasiana, bo w tej atrapie nie ma
        # Codexa, więc sesja założona przez ekran kończy się porażką i nigdy nie jest RUNNING.
        live_project_id = await connection.fetchval(
            """
            INSERT INTO projects (organization_id, workspace_id, name, goal, mode)
            VALUES ($1, $2, 'Sesja na zywo (e2e)', 'obserwuj i odpowiadaj (e2e)', 'REVIEW')
            RETURNING id
            """,
            organization_id,
            workspace_id,
        )
        live_session_id = await connection.fetchval(
            """
            INSERT INTO agent_sessions
                (organization_id, workspace_id, project_id, goal, status, budget)
            VALUES ($1, $2, $3, 'sesja, ktora trwa dluzej niz limit polaczenia (e2e)',
                    'RUNNING', '{"max_turns": 3}'::jsonb)
            RETURNING id
            """,
            organization_id,
            workspace_id,
            live_project_id,
        )
        for seq, (kind, text) in enumerate(
            (
                ("USER_MESSAGE", "co slychac w tym watku (e2e)"),
                ("ASSISTANT_MESSAGE", "czytam watek, zaraz odpowiem (e2e)"),
            ),
            start=1,
        ):
            await connection.execute(
                """
                INSERT INTO session_events
                    (organization_id, workspace_id, session_id, seq, kind, payload)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """,
                organization_id,
                workspace_id,
                live_session_id,
                seq,
                kind,
                json.dumps({"text": text}),
            )

        # DRUGA ROZMOWA W TYM SAMYM PROJEKCIE (D-045). Projekt jest zbiorem rozmów, więc suita
        # musi mieć DWIE, żeby w ogóle dało się sprawdzić, że pasek je rozróżnia i że każda ma
        # własny adres. Ta jest zamknięta i ma wynik jednym zdaniem, bo lista rozmów pokazuje
        # właśnie wynik, a rozmowa bez wyniku nie sprawdziłaby tej kolumny.
        #
        # Zasiana, a nie założona przez ekran, z tego samego powodu co sesja wyżej: w tej atrapie
        # nie ma Codexa, więc pole „Co robimy w …?" jest wyłączone ze wskazaniem powodu i suita
        # sprawdza WŁAŚNIE ten stan, a nie omija go.
        done_session_id = await connection.fetchval(
            """
            INSERT INTO agent_sessions
                (organization_id, workspace_id, project_id, goal, status, result_summary,
                 finished_at)
            VALUES ($1, $2, $3, $4, 'DONE', $5, now())
            RETURNING id
            """,
            organization_id,
            workspace_id,
            live_project_id,
            DONE_SESSION_GOAL,
            DONE_SESSION_RESULT,
        )
        for seq, (kind, body) in enumerate(
            (
                ("USER_MESSAGE", DONE_SESSION_GOAL),
                ("ASSISTANT_MESSAGE", DONE_SESSION_RESULT),
            ),
            start=1,
        ):
            await connection.execute(
                """
                INSERT INTO session_events
                    (organization_id, workspace_id, session_id, seq, kind, payload)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """,
                organization_id,
                workspace_id,
                done_session_id,
                seq,
                kind,
                json.dumps({"text": body}),
            )

        # ROZMOWA Z KARTAMI (D-046). Trzy karty w jednym śladzie: konto bez połączenia,
        # społeczność do dołączenia i społeczność, której dołączenie odmówiono. Czwarta, konto
        # wstrzymane, jest po to, żeby dało się zmierzyć stan, w którym przycisku NIE MA.
        #
        # Karty jadą w `payload.karty` wyniku narzędzia, bo stamtąd bierze je projekcja; STANY
        # przychodzą z produktu przy każdym odczycie, więc niżej zasiewane są też wiersze
        # `social_accounts`. Społeczności NIE zasiewamy: ich brak w tabeli znaczy „propozycja",
        # czyli dokładnie stan, od którego zaczyna się scenariusz z dołączaniem.
        for login, state in ((CARDS_LOGIN, "PROVISIONED"), (CARDS_PAUSED_LOGIN, "PAUSED")):
            await connection.execute(
                "INSERT INTO social_accounts "
                "(organization_id, platform, external_account_id, state) "
                "VALUES ($1, 'REDDIT', $2, $3)",
                organization_id,
                login,
                state,
            )

        cards_session_id = await connection.fetchval(
            """
            INSERT INTO agent_sessions
                (organization_id, workspace_id, project_id, goal, status)
            VALUES ($1, $2, $3, $4, 'WAITING_USER')
            RETURNING id
            """,
            organization_id,
            workspace_id,
            live_project_id,
            CARDS_GOAL,
        )

        def _card(kind, card_id, state, actions, **fields):
            return {
                "karta_id": card_id,
                "rodzaj": kind,
                "stan": state,
                "powod": fields.pop("powod", None),
                "akcje": actions,
                **fields,
            }

        join_action = [
            {
                "nazwa": "dolacz",
                "trasa": {"metoda": "POST", "sciezka": "/api/communities"},
                "payload": {"platform": "REDDIT", "community_key": CARDS_COMMUNITY},
            }
        ]
        cards_events = [
            ("USER_MESSAGE", {"text": CARDS_GOAL, "is_goal": True}),
            ("TOOL_CALL", {"tool": "accounts.status", "arguments": {}}),
            (
                "TOOL_RESULT",
                {
                    "tool": "accounts.status",
                    "karty": [
                        _card(
                            "KONTO",
                            f"konto:REDDIT:{CARDS_LOGIN}",
                            "brak_polaczenia",
                            [
                                {
                                    "nazwa": "polacz",
                                    "trasa": {"metoda": "POST", "sciezka": "/api/capsule/session"},
                                    "payload": {},
                                }
                            ],
                            platforma="REDDIT",
                            konto=CARDS_LOGIN,
                            profil="Anna (e2e)",
                            adres_logowania=None,
                        ),
                        _card(
                            "KONTO",
                            f"konto:REDDIT:{CARDS_PAUSED_LOGIN}",
                            "brak_polaczenia",
                            [],
                            platforma="REDDIT",
                            konto=CARDS_PAUSED_LOGIN,
                            profil="Konto wstrzymane (e2e)",
                            adres_logowania=None,
                        ),
                    ],
                },
            ),
            ("TOOL_CALL", {"tool": "communities.list", "arguments": {}}),
            (
                "TOOL_RESULT",
                {
                    "tool": "communities.list",
                    "karty": [
                        _card(
                            "SPOLECZNOSC",
                            f"spolecznosc:REDDIT:{CARDS_COMMUNITY}",
                            "propozycja",
                            join_action,
                            klucz=CARDS_COMMUNITY,
                            platforma="REDDIT",
                            nazwa="Produkt i pierwsi klienci (e2e)",
                            uzasadnienie=CARDS_WHY,
                        ),
                        _card(
                            "SPOLECZNOSC",
                            f"spolecznosc:REDDIT:{CARDS_SECOND_COMMUNITY}",
                            "blad",
                            [],
                            klucz=CARDS_SECOND_COMMUNITY,
                            platforma="REDDIT",
                            nazwa="Budowanie firmy (e2e)",
                            uzasadnienie=None,
                            powod=CARDS_ERROR,
                        ),
                    ],
                },
            ),
            ("ASSISTANT_MESSAGE", {"text": "Znalazlem dwie spolecznosci (e2e)."}),
            # KARTA POWIERZENIA. Odbiór 1 nie mógł porównać jej z makietą, bo zasiew jej nie miał,
            # a jest to jedyna karta, na której człowiek widzi ZAKRES zgody: gdzie, czym i ile.
            (
                "TOOL_RESULT",
                {
                    "tool": "delegation.propose",
                    "karty": [
                        _card(
                            "POWIERZENIE",
                            "powierzenie",
                            "do_podpisu",
                            [
                                {
                                    "nazwa": "powierz",
                                    "trasa": {
                                        "metoda": "POST",
                                        "sciezka": (
                                            f"/api/workspaces/{workspace_id}"
                                            f"/projects/{live_project_id}/delegation"
                                        ),
                                    },
                                    "payload": {},
                                }
                            ],
                            spolecznosci=[CARDS_COMMUNITY],
                            dzialania=["REPLY"],
                            limity={"publikacje_doba": 3, "wlasne_posty_doba": 0},
                            uzasadnienie=(
                                "Odpowiadam tam, gdzie pytają o produkt; wlasnych postow nie zakladam."
                            ),
                        )
                    ],
                },
            ),
            # PRACA W TOKU PO OSTATNIEJ WIADOMOŚCI CZŁOWIEKA. Postęp liczy się z tury, czyli ze
            # zdarzeń PO ostatnim `USER_MESSAGE` (`projection.py::progress_for`), więc bez tych
            # dwóch wywołań rozmowa z kartami nie ma czego pokazać w linii pracy agenta i odbiór 1
            # zapisał ją jako niesprawdzoną. Jeden etap zamknięty, drugi w toku: tak wygląda tura,
            # którą człowiek zastaje w połowie.
            ("USER_MESSAGE", {"text": "Sprawdz jeszcze raz, co tam slychac (e2e)."}),
            ("TOOL_CALL", {"tool": "communities.list", "arguments": {}}),
            (
                "TOOL_RESULT",
                {
                    "tool": "communities.list",
                    "summary": "Dwie spolecznosci, jedna obserwowana (e2e).",
                    "status": "completed",
                },
            ),
            ("TOOL_CALL", {"tool": "thread.read", "arguments": {"thread_id": "e2e001"}}),
        ]
        for seq, (kind, payload) in enumerate(cards_events, start=1):
            await connection.execute(
                """
                INSERT INTO session_events
                    (organization_id, workspace_id, session_id, seq, kind, payload)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """,
                organization_id,
                workspace_id,
                cards_session_id,
                seq,
                kind,
                json.dumps(payload),
            )

        draft_facts = await _seed_draft(
            dsn, connection, organization_id, workspace_id, live_project_id, cards_session_id
        )

        # Obudzenia dla widoku powrotu (P4 i P5): jedno z sesją i dwa pominięte. Bez pominiętych
        # suita nie odróżniłaby „kolektor pracował i nie było o czym pisać" od „kolektor stanął",
        # a to jest cała różnica, którą ten widok ma pokazywać.
        await connection.execute(
            """
            INSERT INTO wakeups
                (organization_id, workspace_id, project_id, reason, session_id, skipped_reason)
            VALUES ($1, $2, $3, 'NEW_EVENT', $4, NULL),
                   ($1, $2, $3, 'DUE', NULL, 'BEZ_ZDARZEN'),
                   ($1, $2, $3, 'DUE', NULL, 'BEZ_ZDARZEN')
            """,
            organization_id,
            workspace_id,
            live_project_id,
            live_session_id,
        )

        # Read the rows back through a SECOND connection-level statement. A seeding script that
        # trusts its own INSERTs is the same shape as a gate trusting its own verdict: asyncpg
        # returns "INSERT 0" without raising when nothing matched, and this repository has been
        # caught by that exact string before.
        attempts = await connection.fetchval(
            "SELECT count(*) FROM job_attempts WHERE job_id = $1", job_id
        )
        if attempts != 2:
            raise RuntimeError(
                f"zasiew nie zapisał prób: job_attempts dla {job_id} ma {attempts} wierszy, nie 2"
            )
        jobs = await connection.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1", organization_id
        )
        if jobs != 2:
            raise RuntimeError(f"zasiew nie zapisał jobów: jest {jobs}, nie 2")
        judgments = await connection.fetchval(
            "SELECT count(*) FROM thread_judgments WHERE organization_id = $1", organization_id
        )
        if judgments != 1:
            raise RuntimeError(f"zasiew nie zapisał osądu: jest {judgments}, nie 1")
        feedback = await connection.fetchval(
            "SELECT count(*) FROM judgment_feedback WHERE organization_id = $1", organization_id
        )
        if feedback != 0:
            # The suite writes the FIRST feedback row itself, through the form handler. A row
            # already here would make that test pass without the POST having done anything.
            raise RuntimeError(f"zasiew zostawił feedback: {feedback} wierszy, oczekiwano 0")

        documents = await _seed_documents(
            dsn, connection, organization_id, workspace_id, live_project_id
        )

        return {
            **documents,
            "WENEXT_E2E_OPERATOR_PASSWORD": OPERATOR_PASSWORD,
            "WENEXT_E2E_JOB_ID": str(job_id),
            "WENEXT_E2E_FRESH_JOB_ID": str(fresh_job_id),
            "WENEXT_E2E_LEASE_OWNER": str(lease_owner),
            # Not seeded: the login itself writes this event, so the audit screen is asked to show
            # something the system produced during the run rather than a row planted for it.
            "WENEXT_E2E_AUDIT_KIND": "OPERATOR_SESSION_OPENED",
            "WENEXT_E2E_ORGANIZATION_ID": str(organization_id),
            "WENEXT_E2E_WORKSPACE_ID": str(workspace_id),
            "WENEXT_E2E_OPERATOR_ID": str(operator_id),
            # Outcome A rows, for `e2e/community_reads_postgres.e2e.test.ts`.
            "WENEXT_E2E_COMMUNITY_ID": str(community_id),
            "WENEXT_E2E_JUDGMENT_ID": str(judgment_id),
            "WENEXT_E2E_THREAD_TITLE": thread_title,
            # Regresja strumienia (audyt 19.09): projekt i sesja, która NAPRAWDĘ jest RUNNING.
            "WENEXT_E2E_LIVE_PROJECT_ID": str(live_project_id),
            "WENEXT_E2E_LIVE_SESSION_ID": str(live_session_id),
            # D-045: druga rozmowa tego samego projektu, zamknięta, z wynikiem jednym zdaniem.
            "WENEXT_E2E_DONE_SESSION_ID": str(done_session_id),
            "WENEXT_E2E_DONE_SESSION_TITLE": DONE_SESSION_GOAL.split(".")[0],
            "WENEXT_E2E_DONE_SESSION_RESULT": DONE_SESSION_RESULT,
            # D-046: rozmowa z kartami i wartości, których szuka na ekranie suita kart.
            "WENEXT_E2E_CARDS_SESSION_ID": str(cards_session_id),
            "WENEXT_E2E_CARDS_LOGIN": CARDS_LOGIN,
            "WENEXT_E2E_CARDS_PAUSED_LOGIN": CARDS_PAUSED_LOGIN,
            "WENEXT_E2E_CARDS_COMMUNITY": CARDS_COMMUNITY,
            "WENEXT_E2E_CARDS_WHY": CARDS_WHY,
            "WENEXT_E2E_CARDS_ERROR": CARDS_ERROR,
            **draft_facts,
        }
    finally:
        await connection.close()


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("BŁĄD: brak DATABASE_URL — nie ma czego zasiać", file=sys.stderr)
        return 2
    facts = asyncio.run(seed(dsn))
    for key, value in facts.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
