"""What the schema of phase E1.F1 is REQUIRED to contain, written down once.

This module holds no behaviour. It holds the lists the schema tests assert against, and it
exists because of a specific failure mode this repository has already paid for eight times:
a check that runs over an empty set passes. `all([])` is `True`, `assert not found` over a
query that matched nothing is `True`, and a loop over a table list that turned out to be empty
is a green test that proved nothing (D-011, D-020).

So every list here is a CLOSED, EXPLICIT enumeration, and every test that uses one asserts its
length BEFORE it asserts anything about its contents. Adding a table to the schema without
adding it here is meant to be caught by the reverse check
(`test_no_undeclared_table_carries_a_workspace_column`), not by nobody noticing.

Anchors into ROADMAP_CODEX_CDP_COMMUNITY_OS.md are part of the data, not decoration: they are
what ties a column list to the sentence that demanded it.
"""

from __future__ import annotations

from typing import NamedTuple

# ---------------------------------------------------------------------------
# Migration 0002 — the twelve Polish identifiers left behind by 0001
# ---------------------------------------------------------------------------


class RenamedObject(NamedTuple):
    """One database object renamed by migration 0002 (D-010).

    `catalog` is the system catalog the object lives in. It is part of the record because the
    obvious version of this test — "select conname from pg_constraint" — would have left EIGHT
    of the twelve names in place and reported success: `ALTER TABLE ... RENAME CONSTRAINT`
    touches neither indexes, nor functions, nor triggers, and those three live in three other
    catalogs. Four catalogs, or the check is a false pass.
    """

    catalog: str
    old_name: str
    new_name: str


#: Every Polish identifier created by `db/migrations/0001_f0_kernel.sql` and renamed by 0002.
#:
#: 0001 must NOT be edited in place — it is already applied on developer databases, and an
#: edit-in-place makes the file disagree with every database that ran the previous version.
#: Hence a second migration that renames, rather than a rewrite of the first.
RENAMED_IN_0002: tuple[RenamedObject, ...] = (
    # --- pg_constraint: four CHECK constraints, renamed with ALTER TABLE ... RENAME CONSTRAINT
    RenamedObject("pg_constraint", "headless_bez_displayu", "headless_has_no_display"),
    RenamedObject(
        "pg_constraint",
        "control_wymaga_interaction_lease",
        "control_requires_interaction_lease",
    ),
    RenamedObject(
        "pg_constraint",
        "ttl_biletu_nie_dluzszy_niz_sesji",
        "ticket_ttl_within_session_ttl",
    ),
    RenamedObject("pg_constraint", "manual_only_nie_jest_headless", "manual_only_is_not_headless"),
    # --- pg_class (relkind 'i'): six indexes, renamed with ALTER INDEX ... RENAME TO
    RenamedObject(
        "pg_index",
        "browser_profiles_jeden_aktywny_na_konto",
        "browser_profiles_one_active_per_account",
    ),
    RenamedObject(
        "pg_index",
        "profile_leases_jeden_aktywny_na_profil",
        "profile_leases_one_active_per_profile",
    ),
    RenamedObject(
        "pg_index",
        "profile_leases_generacja_unikalna",
        "profile_leases_generation_is_unique",
    ),
    RenamedObject(
        "pg_index",
        "interaction_leases_jeden_aktywny_na_sesje",
        "interaction_leases_one_active_per_session",
    ),
    RenamedObject("pg_index", "audit_events_wg_czasu", "audit_events_by_time"),
    RenamedObject("pg_index", "audit_events_wg_rodzaju", "audit_events_by_kind"),
    # --- pg_proc and pg_trigger: DROP + CREATE, because the function body changes too
    RenamedObject("pg_proc", "audit_events_tylko_dopisywanie", "audit_events_append_only"),
    RenamedObject("pg_trigger", "audit_events_bez_zmian", "audit_events_no_mutation"),
)

#: The four catalogs that have to be read. Kept as data so a test can assert it read all four
#: rather than trusting that it did.
RENAME_CATALOGS: tuple[str, ...] = ("pg_constraint", "pg_index", "pg_proc", "pg_trigger")


# ---------------------------------------------------------------------------
# Tenant scope — section 18.2 (L3871-3876), decision D-020
# ---------------------------------------------------------------------------

#: Tables created by migration 0003 that belong to a WORKSPACE: `organization_id NOT NULL`,
#: `workspace_id NOT NULL`, and a composite foreign key in the canonical TRIPLE
#: `(organization_id, workspace_id, parent_id) -> parent(organization_id, workspace_id, id)`
#: (L3874-3876) — or, for a table whose parent is the workspace itself, the pair
#: `(organization_id, workspace_id) -> workspaces(organization_id, id)`.
#:
#: The list is written out by hand ON PURPOSE. The tempting version of the test —
#: "for every table that has a workspace_id, check it has a composite FK" — passes over a
#: schema in which no table has the column at all, which is precisely the shape of the bug
#: D-020 says to guard against. A hand-written list with an asserted length cannot go quiet.
WORKSPACE_TABLES: tuple[str, ...] = (
    "action_proposals",
    "action_proposal_revisions",
    "approval_decisions",
    "execution_intents",
    "execution_attempts",
    "artifacts",
    # Migration 0004 — observation and judgment (Outcome A, docs/OUTCOMES.md §5 row 4).
    "communities",
    "observed_threads",
    "observed_comments",
    "thread_metric_snapshots",
    "user_profile_versions",
    "community_pictures",
    "thread_judgments",
    "judgment_feedback",
    # Migration 0005 — the community's voice card (etap 3 M0).
    "community_voice_cards",
    # Migration 0006 — the 24 h pilot: run manifest, cycles, reactions (docs/START-PILOTA-24H.md).
    "pilot_runs",
    "pilot_cycles",
    "pilot_reactions",
    # Migration 0007 — publication of approved pilot drafts (R4 minimal, D-028).
    "publication_requests",
    "execution_evidence",
    "publication_observations",
    # Migration 0008 — tryb 7 dni: signed publication policy and the judge's verdicts (D-029).
    "publication_policies",
    "draft_judgments",
    # Migration 0013 — P1: projects, person profiles and the two attachment tables (D-040).
    "projects",
    "person_profiles",
    "person_profile_accounts",
    "project_account_assignments",
    # Migration 0016 — P3 etap A: where knowledge came from, and what it says.
    "knowledge_sources",
    "knowledge_items",
    # Migration 0015 — P2: sesja agenta z celem i jej append-only ślad.
    "agent_sessions",
    "session_events",
    # Migration 0017 — P4: delegacja, obudzenia i ich zdarzenia.
    "project_delegations",
    "wakeups",
    "wakeup_events",
    # Migration 0022 — P1: głos osoby, wersjonowany i append-only.
    "person_voice_versions",
    # Migration 0024 — P4 i P5: znacznik ostatniej wizyty człowieka w projekcie.
    "project_visits",
    # Migracja 0028 — dokumenty projektu (P8). Wszystkie trzy należą do PRZESTRZENI, nawet gdy
    # `project_documents.project_id` jest NULL: taki wiersz to podręcznik platformy, czyli
    # dokument tej przestrzeni, a nie wiersz bez właściciela.
    "project_documents",
    "project_document_versions",
    "project_document_sentences",
    # Migracja 0035 — co się niesie na X: zapytania wybrane przez agenta i zebrane posty.
    "x_watch_queries",
    "x_trend_posts",
    # Migracja 0036 — okazje: rozmowy na żywo, które agent uznał za warte uwagi założyciela.
    "project_opportunities",
    # Migracja 0037 — hostowany MCP: skrót tokenu wskazuje jeden projekt, a para (dostawca, podmiot)
    # z logowania OAuth wskazuje jeden projekt.
    "hosted_tokens",
    "external_identities",
    # Migracja 0038 (hostowany MVP): rewizja szkicu, którą człowiek wkleił sam i powiedział o tym.
    "manual_publications",
)

#: Parent of every workspace table of 0004 that has a workspace-scoped parent, reached through
#: the TRIPLE. `communities` and `user_profile_versions` hang off the workspace itself and are
#: therefore not here — excluded by name, like `action_proposals` and `artifacts` in the test.
OBSERVE_TRIPLE_PARENTS: dict[str, str] = {
    "observed_threads": "communities",
    "observed_comments": "observed_threads",
    "thread_metric_snapshots": "observed_threads",
    "community_pictures": "communities",
    "thread_judgments": "observed_threads",
    "judgment_feedback": "thread_judgments",
    "community_voice_cards": "communities",
    "pilot_runs": "communities",
    "pilot_cycles": "pilot_runs",
    "pilot_reactions": "pilot_runs",
    "publication_requests": "pilot_runs",
    "execution_evidence": "execution_attempts",
    "publication_observations": "publication_requests",
    # 0008: a verdict belongs to the policy that asked for it (and points at the reaction too).
    "draft_judgments": "publication_policies",
}

#: Parent of every workspace table of 0013 that hangs off another workspace-scoped row.
#:
#: `projects` and `person_profiles` are NOT here: their parent is the workspace itself, so they
#: carry the PAIR `(organization_id, workspace_id) -> workspaces (organization_id, id)`, exactly
#: like `communities`. The two attachment tables do have a workspace-scoped parent and therefore
#: have to reach it with all three columns — a pair would let an attachment of workspace A hang
#: under a profile of workspace B in the same organization.
WORKSPACE_TRIPLE_PARENTS: dict[str, str] = {
    "person_profile_accounts": "person_profiles",
    "project_account_assignments": "projects",
}

#: Tables of 0004 that are APPEND-ONLY by trigger: an UPDATE or a DELETE raises. A measurement
#: corrected after the fact is not a measurement, and a judgment rewritten is not a record.
OBSERVE_APPEND_ONLY_TABLES: tuple[str, ...] = ("thread_metric_snapshots", "thread_judgments")

#: Column lists of 0004, written out so that a column dropped or renamed fails a test rather
#: than a query somewhere in the control plane at run time.
COMMUNITIES_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "platform",
    "community_key",
    "display_name",
    "access_mode",
    "observe_enabled",
    "poll_interval_seconds",
    "last_polled_at",
    "watermark_created_at",
    "created_at",
    # 0011: czy ta społeczność dostaje osądy i migawki per wątek (D-032).
    "thread_analysis_enabled",
    # 0025: decyzja CZŁOWIEKA o własnych wątkach. NULL znaczy „nikt nie zdecydował" i jest
    # odmową; autor i moment są albo oba, albo żadne (CHECK w migracji).
    "own_posts_allowed",
    "own_posts_decided_by",
    "own_posts_membership_id",
    "own_posts_decided_at",
)
OBSERVED_THREADS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "community_id",
    "external_id",
    "url",
    "link_url",
    "title",
    "body",
    "author",
    "created_at_source",
    "first_observed_at",
    "last_observed_at",
    "raw_sha256",
)
OBSERVED_COMMENTS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "thread_id",
    "external_id",
    "parent_external_id",
    "author",
    "body",
    "created_at_source",
    "observed_at",
    "depth",
    # Migration 0005 — the comment's score as an Observed[int] pair (voice card, etap 3 M0).
    "score",
    "score_unknown_reason",
)
THREAD_METRIC_SNAPSHOTS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "thread_id",
    "observed_at",
    "score",
    "score_unknown_reason",
    "comment_count",
    "comment_count_unknown_reason",
)
USER_PROFILE_VERSIONS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "version",
    "knows_deeply",
    "positions",
    "would_never_claim",
    "voice_samples",
    "created_at",
)
COMMUNITY_PICTURES_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "community_id",
    "version",
    "window_start",
    "window_end",
    "content",
    "thread_count",
    "invalid_ref_count",
    "model",
    "effort",
    "prompt_name",
    "prompt_sha256",
    "created_at",
)
#: Migration 0005 — the community's voice card, versioned like the picture (docs/OUTCOMES.md §8,
#: etap 3 M0). `comment_count` and `invalid_quote_count` play the roles of the picture's
#: `thread_count` and `invalid_ref_count`: how much went in, how often the model pointed at a
#: comment that was not in the window.
COMMUNITY_VOICE_CARDS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "community_id",
    "version",
    "window_start",
    "window_end",
    "content",
    "comment_count",
    "invalid_quote_count",
    "model",
    "effort",
    "prompt_name",
    "prompt_sha256",
    "created_at",
)
#: Migration 0006 — the 24 h pilot (docs/START-PILOTA-24H.md). The manifest carries the budget
#: COUNTER (`model_calls_used`), reserved in the database before each model call, so a restart
#: cannot reset it; `host_events` is the cycles' log of detected gaps and outages.
PILOT_RUNS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "community_id",
    "status",
    "starts_at",
    "ends_at",
    "stopped_at",
    "poll_interval_seconds",
    "model_calls_budget",
    "model_calls_used",
    "max_generations_per_thread",
    "profile_version_id",
    "prompt_name",
    "prompt_sha256",
    "paused_job_types",
    "restore",
    "host_events",
    "created_at",
    # 0007: pace of publication and the stop flag (docs/ZGODY-PUBLIKACJI-2026-09-07.md).
    "max_publications_per_hour",
    "max_publications_per_day",
    "publishing_stopped_at",
)
PUBLICATION_REQUESTS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "run_id",
    "reaction_id",
    "proposal_revision_id",
    "approval_decision_id",
    "intent_id",
    "attempt_id",
    "position",
    "status",
    "consent_source",
    "draft_sha256",
    "target_external_id",
    "target_url",
    "queued_at",
    "not_before",
    "last_checked_at",
    "detail",
    "published_at",
    # 0012: widoczność publiczna. `public_visibility` to wniosek z odczytów anonimowych,
    # `removal_*` to niezależne zgłoszenie ze skrzynki konta (STAN-PRAC §8.22).
    "public_visibility",
    "public_visibility_checked_at",
    "removal_reported_at",
    "removal_source",
    # 0017: skąd wziął się wniosek. Bieg pilota (run_id, reaction_id) albo projekt
    # (project_id, delegation_id); CHECK w 0017 wymusza dokładnie jedno z dwojga.
    "project_id",
    "delegation_id",
    # 0023: odpowiedź pod cudzym wątkiem czy własny post. DEFAULT 'REPLY', więc każdy wiersz
    # sprzed tej migracji zachowuje znaczenie.
    "kind",
    # 0032: czego dotyczy usunięcie (nasza odpowiedź czy cały wątek pod nią); NULL = nie ustalono.
    "removal_scope",
)
EXECUTION_EVIDENCE_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "attempt_id",
    "external_id",
    "permalink",
    "account",
    "observed_at",
    "source",
    "raw",
    "created_at",
)
PUBLICATION_OBSERVATIONS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "request_id",
    "observed_at",
    "still_present",
    "score",
    "reply_count",
    "raw",
    # 0012: KTO patrzył. `still_present` sam nie odróżnia odczytu autora od odczytu anonimowego,
    # a to jest dokładnie ta różnica, która przepuściła usunięte komentarze jako sukces.
    "viewer",
)
PILOT_CYCLES_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "run_id",
    "slot",
    "planned_at",
    "started_at",
    "finished_at",
    "outcome",
    "detail",
    "model_calls_used_after",
)
PILOT_REACTIONS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "run_id",
    "thread_id",
    "origin",
    "target_message_external_id",
    "decision",
    "draft",
    "contribution",
    "brief_rationale",
    "confidence",
    "model",
    "effort",
    "profile_version_id",
    "prompt_name",
    "prompt_sha256",
    "input_sha256",
    "situation_sha256",
    "read_at",
    "context_complete",
    "comments_seen",
    "wait_reason",
    "next_check_at",
    "review_status",
    "created_at",
    # 0009: the move the draft is built around (prompt v3); NULL before it.
    "move",
    # 0010: the move the cycle ASKED for (D-031 experiment); NULL before it.
    "requested_move",
)
THREAD_JUDGMENTS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "thread_id",
    "decision",
    "reasoning",
    "what_user_could_add",
    "confidence",
    "user_profile_version_id",
    "community_picture_id",
    "model",
    "effort",
    "prompt_name",
    "prompt_sha256",
    "created_at",
)
JUDGMENT_FEEDBACK_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "judgment_id",
    "verdict",
    "note",
    "created_at",
)
OBSERVE_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "communities": COMMUNITIES_COLUMNS,
    "observed_threads": OBSERVED_THREADS_COLUMNS,
    "observed_comments": OBSERVED_COMMENTS_COLUMNS,
    "thread_metric_snapshots": THREAD_METRIC_SNAPSHOTS_COLUMNS,
    "user_profile_versions": USER_PROFILE_VERSIONS_COLUMNS,
    "community_pictures": COMMUNITY_PICTURES_COLUMNS,
    "thread_judgments": THREAD_JUDGMENTS_COLUMNS,
    "judgment_feedback": JUDGMENT_FEEDBACK_COLUMNS,
    "community_voice_cards": COMMUNITY_VOICE_CARDS_COLUMNS,
    "pilot_runs": PILOT_RUNS_COLUMNS,
    "pilot_cycles": PILOT_CYCLES_COLUMNS,
    "pilot_reactions": PILOT_REACTIONS_COLUMNS,
    "publication_requests": PUBLICATION_REQUESTS_COLUMNS,
    "execution_evidence": EXECUTION_EVIDENCE_COLUMNS,
    "publication_observations": PUBLICATION_OBSERVATIONS_COLUMNS,
}

# ---------------------------------------------------------------------------
# Migration 0013 — P1: projects, person profiles, attachments (ROADMAP-PIVOT P1, D-040)
# ---------------------------------------------------------------------------

PROJECTS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "name",
    "goal",
    "mode",
    "paused_at",
    "created_at",
    "archived_at",
    # 0026: ile przysługuje każdej następnej ROZMOWIE w tym projekcie. NULL znaczy „nikt nie
    # ustawił", czyli obowiązuje wartość domyślna z kodu, i to jest co innego niż zero.
    "conversation_budget",
)
PERSON_PROFILES_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "name",
    "kind",
    "created_at",
    "archived_at",
)
PERSON_PROFILE_ACCOUNTS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "person_profile_id",
    "social_account_id",
    "attached_at",
    "detached_at",
)
PROJECT_ACCOUNT_ASSIGNMENTS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "project_id",
    "social_account_id",
    "assigned_at",
    "unassigned_at",
)
WORKSPACE_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "projects": PROJECTS_COLUMNS,
    "person_profiles": PERSON_PROFILES_COLUMNS,
    "person_profile_accounts": PERSON_PROFILE_ACCOUNTS_COLUMNS,
    "project_account_assignments": PROJECT_ACCOUNT_ASSIGNMENTS_COLUMNS,
}


# ---------------------------------------------------------------------------
# Migration 0015 — sesja agenta i jej zdarzenia (P2, ROADMAP-PIVOT)
# ---------------------------------------------------------------------------
# Własna mapa, a nie dopisek do WORKSPACE_TABLE_COLUMNS ani do OBSERVE_TABLE_COLUMNS: obie mają
# w swoich testach zadeklarowaną LICZNOŚĆ (cztery i piętnaście), a liczność jest tam częścią
# dowodu, nie ozdobą. Dopisanie się do cudzej mapy zmienia cudzy test; rozdzielenie kosztuje
# jedną nazwę.
AGENT_SESSIONS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "project_id",
    "codex_connection_id",
    "goal",
    "status",
    # 0017: rozmowa z człowiekiem albo praca bez niego. DEFAULT 'CONVERSATION'.
    "kind",
    # 0019: sesja, która zleciła tę pracę. NULL znaczy „nikt nie zlecił”, czyli sesja otwarta
    # przez człowieka albo przez tik obecności.
    "parent_session_id",
    "engine_thread_id",
    "budget",
    "result_summary",
    "result_refs",
    "created_at",
    "updated_at",
    "finished_at",
)
#: 0019: liczby o zasięgu. Każda niesie `source`, bo odczyt ze strony profilu, z API platformy i
#: wpis ręczny to trzy różne jakości dowodu, a bez tej kolumny wyglądają tak samo.
AUDIENCE_SNAPSHOTS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    # Bez `workspace_id`: liczba należy do konta, a konto jest własnością organizacji. Patrz
    # komentarz w 0019.
    "social_account_id",
    "metric",
    "value",
    "period",
    "source",
    "observed_at",
    "created_at",
)

#: Zamknięte listy z 0019. Liczone przed porównaniem, jak wszędzie w tym module.
AUDIENCE_METRICS: frozenset[str] = frozenset({"FOLLOWERS", "POSTS", "IMPRESSIONS", "ENGAGEMENTS"})
AUDIENCE_PERIODS: frozenset[str] = frozenset({"POINT_IN_TIME", "DAY", "WEEK", "MONTH"})
AUDIENCE_SOURCES: frozenset[str] = frozenset({"PLATFORM_API", "PLATFORM_PAGE", "MANUAL"})

#: 0020 (P7 etap B): gotowość operacyjna. Cztery tabele ORGANIZACYJNE, bez `workspace_id`:
#: dostępność usługi, awaria, koszt i sufit są faktami o organizacji, a nie o jednej przestrzeni
#: roboczej. Ta sama zasada, co przy `audience_snapshots` w 0019.
SERVICE_HEARTBEATS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "component",
    # Początek interwału, nie moment zapisu: dzięki temu „ile interwałów miało wiersz" jest
    # liczeniem, a nie kubełkowaniem znaczników czasu przy każdym raporcie.
    "interval_start",
    "detail",
    "created_at",
)
INCIDENTS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "component",
    "started_at",
    "ended_at",
    "reason",
    "detected_by",
    "created_at",
)
COST_LEDGER_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "project_id",
    "kind",
    # Jednostki, nie pieniądze: cena zmienia się wstecz, a „12 tur modelu" zostaje prawdą.
    "units",
    "source_ref",
    "occurred_at",
    "created_at",
)
ORG_LIMITS_COLUMNS: tuple[str, ...] = (
    "organization_id",
    "model_turns_per_day",
    "publications_per_day",
    "updated_at",
    "updated_by",
)
OPS_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "service_heartbeats": SERVICE_HEARTBEATS_COLUMNS,
    "incidents": INCIDENTS_COLUMNS,
    "cost_ledger": COST_LEDGER_COLUMNS,
    "org_limits": ORG_LIMITS_COLUMNS,
}

#: Zamknięte listy z 0020. Komponent jest ten sam w pulsie i w incydencie, i to jest celowe:
#: „co biło" i „co padło" muszą być tym samym słownikiem, inaczej raport nie połączy jednego
#: z drugim.
SERVICE_COMPONENTS: frozenset[str] = frozenset(
    {"DISPATCHER", "COLLECTOR", "PUBLISHER", "CAPSULE", "CODEX_CONNECTION"}
)
INCIDENT_DETECTORS: frozenset[str] = frozenset({"SELF_REPORT", "MISSING_HEARTBEAT", "HUMAN"})
COST_KINDS: frozenset[str] = frozenset({"MODEL_TURN", "TOOL_CALL", "PUBLICATION", "FETCH"})

SESSION_EVENTS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "session_id",
    "seq",
    "kind",
    "payload",
    "created_at",
)
SESSION_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "agent_sessions": AGENT_SESSIONS_COLUMNS,
    "session_events": SESSION_EVENTS_COLUMNS,
}


#: Tabela 0015 APPEND-ONLY przez trigger, z tego samego powodu, co tabele 0004: przebieg
#: rozmowy poprawiony po fakcie nie jest już przebiegiem rozmowy.
SESSION_APPEND_ONLY_TABLES: tuple[str, ...] = ("session_events",)

# ---------------------------------------------------------------------------
# Migration 0017 — obecność bez otwartego dashboardu (P4)
# ---------------------------------------------------------------------------
PROJECT_DELEGATIONS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "project_id",
    "mode",
    "scope",
    "budget",
    "scope_sha256",
    "signed_at",
    "signed_by",
    "signed_membership_id",
    "paused_at",
    "revoked_at",
    "created_at",
    # 0033: termin powierzenia, wchodzący do odcisku zgody; NULL tylko dla podpisów sprzed 0033.
    "expires_at",
)
WAKEUPS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "project_id",
    "reason",
    "session_id",
    "skipped_reason",
    "skipped_detail",
    # 0023: zaplanowany powrót agenta. NULL w `due_at` znaczy „wymagalne od razu", czyli
    # obudzenie z obserwacji; `cancelled_at` dotyczy wyłącznie zaplanowanych.
    "due_at",
    "cancelled_at",
    "cost",
    "created_at",
    "consumed_at",
)
WAKEUP_EVENTS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "project_id",
    "wakeup_id",
    "kind",
    "event_key",
    "payload",
    "created_at",
)
PRESENCE_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "project_delegations": PROJECT_DELEGATIONS_COLUMNS,
    "wakeups": WAKEUPS_COLUMNS,
    "wakeup_events": WAKEUP_EVENTS_COLUMNS,
}

#: Zamknięte listy 0017.
DELEGATION_MODES: frozenset[str] = frozenset({"AUTONOMOUS", "REVIEW", "RESEARCH"})
WAKEUP_REASONS: frozenset[str] = frozenset({"NEW_EVENT", "DUE", "USER_MESSAGE", "RETRY"})
WAKEUP_EVENT_KINDS: frozenset[str] = frozenset(
    {"NEW_THREAD", "REPLY", "MODERATION", "DUE", "OUTCOME"}
)
#: Rodzaj sesji, dodany w 0017 do tabeli z 0015.
AGENT_SESSION_KINDS: frozenset[str] = frozenset({"CONVERSATION", "PRESENCE"})

#: Zamknięta lista 0023: odpowiedź pod cudzym wątkiem albo własny wątek. Sparowana z
#: `publication_requests_kind_is_known` w `enumerated_checks`, bo trzeci rodzaj dopisany po
#: jednej stronie (w kodzie albo w migracji) ma się wywalić na teście, a nie na publikacji.
PUBLICATION_REQUEST_KINDS: frozenset[str] = frozenset({"REPLY", "OWN_POST"})

#: Zamknięte listy 0015. Osiem stanów sesji i siedem rodzajów zdarzeń, liczone w teście osobno
#: od porównania, bo `all([])` jest prawdą.
AGENT_SESSION_STATUSES: frozenset[str] = frozenset(
    {
        "QUEUED",
        "RUNNING",
        "WAITING_USER",
        "WAITING_EVENT",
        "PAUSED",
        "DONE",
        "FAILED",
        "BUDGET_EXHAUSTED",
    }
)
SESSION_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "USER_MESSAGE",
        "ASSISTANT_MESSAGE",
        "TOOL_CALL",
        "TOOL_RESULT",
        "STATUS",
        "ERROR",
        "INTERRUPT",
    }
)


# ---------------------------------------------------------------------------
# Migration 0014 — Codex connections (P1 etap 2, D-040)
# ---------------------------------------------------------------------------

CODEX_CONNECTIONS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "label",
    "home_ref",
    "status",
    "account_email",
    "plan_type",
    "installation_id",
    "models",
    "rate_limits",
    "login_ticket",
    "last_read_at",
    "last_error",
    "created_at",
    "disconnected_at",
)

#: The closed status list of a connection. Same five values the API and the screen use; a typo
#: has to be a refusal from the database, not a sixth state nobody declared.
CODEX_CONNECTION_STATUSES: frozenset[str] = frozenset(
    {"PENDING_LOGIN", "CONNECTED", "RATE_LIMITED", "DISCONNECTED", "ERROR"}
)

#: Columns of `codex_connections` that must NOT exist, ever. A connection points at the directory
#: where Codex keeps `auth.json`; it does not hold the credential. A token in a column is a token
#: in every backup and in every diagnostic dump, and this list is what makes adding one a failing
#: test rather than a commit nobody read.
CODEX_CONNECTION_FORBIDDEN_COLUMNS: tuple[str, ...] = (
    "auth_json",
    "access_token",
    "refresh_token",
    "id_token",
    "api_key",
    "credential",
    "credential_hash",
    "password",
    "secret",
)


# ---------------------------------------------------------------------------
# Migration 0016 — knowledge sources and items (P3 etap A, ROADMAP-PIVOT)
# ---------------------------------------------------------------------------

KNOWLEDGE_SOURCES_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "project_id",
    "kind",
    "locator",
    "title",
    "fetched_at",
    "content_hash",
    "status",
    "raw_artifact_id",
    "unavailable_reason",
    "created_at",
)

KNOWLEDGE_ITEMS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "project_id",
    "person_profile_id",
    "kind",
    "statement",
    "source_id",
    "source_span",
    "stated_by",
    "session_id",
    "payload",
    "valid_from",
    "superseded_by",
    "retracted_at",
    "retracted_reason",
    "created_at",
)

KNOWLEDGE_SOURCE_KINDS: frozenset[str] = frozenset(
    {"WEB_PAGE", "WEB_SEARCH", "RSS", "NEWSLETTER", "DOCUMENT", "USER_NOTE"}
)
KNOWLEDGE_SOURCE_STATUSES: frozenset[str] = frozenset({"FETCHED", "UNAVAILABLE", "STALE"})

#: The four kinds P3 asks the result to distinguish: "fakt, interpretację, preferencję człowieka
#: i doświadczenie z działań". The distinction is the deliverable, not a label.
KNOWLEDGE_ITEM_KINDS: frozenset[str] = frozenset(
    {"FACT", "INTERPRETATION", "PREFERENCE", "EXPERIENCE"}
)
KNOWLEDGE_STATED_BY: frozenset[str] = frozenset({"USER", "AGENT", "SOURCE"})

KNOWLEDGE_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "knowledge_sources": KNOWLEDGE_SOURCES_COLUMNS,
    "knowledge_items": KNOWLEDGE_ITEMS_COLUMNS,
}


# ---------------------------------------------------------------------------
# 0018 — tożsamość użytkownika, członkostwo, zaproszenia, eksport i usunięcie (P7 etap A)
# ---------------------------------------------------------------------------
#
# These five tables are ORGANIZATION-scoped and carry no `workspace_id`, which is why none of
# them joins `WORKSPACE_TABLES`: a person belongs to an organization, not to a workspace, and a
# `workspace_id` here would be a scope nobody could answer for. `users` has no tenant column at
# all — one account can belong to two organizations, which is the whole point of `memberships`.

USERS_COLUMNS: tuple[str, ...] = (
    "id",
    # NULLABLE, and only for the founder's migrated account: 0018 moved the single `operators`
    # row over and nobody ever recorded whose it is. A made-up address would be made-up data in
    # the table that decides who may log in.
    "email",
    "credential_hash",
    "created_at",
    "disabled_at",
    "last_login_at",
)

MEMBERSHIPS_COLUMNS: tuple[str, ...] = (
    "id",
    "user_id",
    "organization_id",
    "role",
    "invited_by",
    "created_at",
    "revoked_at",
)

INVITATIONS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "email",
    # The SHA-256 of the token, never the token. Same rule as the session cookie.
    "token_hash",
    "expires_at",
    "accepted_at",
    "created_by",
    "created_at",
)

DATA_EXPORT_JOBS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "requested_by",
    "status",
    "artifact_id",
    "requested_at",
    "finished_at",
    "purge_after",
    "detail",
)

DATA_DELETION_JOBS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "requested_by",
    "status",
    "requested_at",
    "finished_at",
    "purge_after",
    "detail",
)

#: Migracja 0022. Cztery pola głosu są te same, co w `user_profile_versions` (0004) i tak samo
#: nazwane; test parzystości nazw pilnuje, żeby jedna z tabel nie odjechała od drugiej.
PERSON_VOICE_VERSIONS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "person_profile_id",
    "version",
    "knows_deeply",
    "positions",
    "would_never_claim",
    "voice_samples",
    # NULL dla sesji otwartej wspólnym hasłem operatora sprzed P7. Kolumna jest, autor bywa
    # nieznany, i to jest widoczne zamiast zmyślone.
    "created_by",
    "created_at",
)

#: Migracja 0024. Dwa znaczniki, nie jeden: `previous_seen_at` jest początkiem pokazywanego okna,
#: `last_seen_at` tą wizytą. Z jednym odświeżenie strony kasowałoby okno w trakcie czytania.
PROJECT_VISITS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "project_id",
    # NULL dla sesji otwartej wspólnym hasłem operatora sprzed P7; indeks ma NULLS NOT DISTINCT,
    # więc te sesje dzielą jeden wiersz, zamiast zakładać nowy przy każdym wejściu.
    "user_id",
    "previous_seen_at",
    "last_seen_at",
    "visits",
    "created_at",
)

#: Migracja 0035 — zapytania X wybrane przez agenta do cogodzinnego zbierania.
X_WATCH_QUERIES_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "project_id",
    "query",
    "mode",
    "created_by_session",
    "created_at",
    "removed_at",
)

#: Migracja 0035 — posty X z jednego odczytu, z licznikami, których X nie pokazał jako NULL.
X_TREND_POSTS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "project_id",
    "collected_at",
    "source",
    "query",
    "post_id",
    "author",
    "body",
    "posted_at",
    "permalink",
    "likes",
    "replies",
    "reposts",
    "views",
)

#: Migracja 0036 — okazje projektu. Stan okazji nie ma kolumny: wynika ze szkicu i z publikacji.
PROJECT_OPPORTUNITIES_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "project_id",
    "platform",
    "external_id",
    "url",
    "community",
    "title",
    "excerpt",
    "author",
    "created_at_source",
    "score",
    "num_comments",
    "reason",
    "angle",
    "marked_by_session",
    "created_at",
    "updated_at",
    "dismissed_at",
    "dismissed_by",
)

#: Migracja 0037 — tokeny hostowanego MCP. Klucz główny to SHA-256 tokenu (64 znaki hex), nigdy
#: sam token. Cofnięcie to `revoked_at`, nie DELETE.
HOSTED_TOKENS_COLUMNS: tuple[str, ...] = (
    "token_sha256",
    "organization_id",
    "workspace_id",
    "project_id",
    "label",
    "created_at",
    "revoked_at",
    "last_used_at",
)

#: Migracja 0037 — tożsamość z logowania OAuth. Klucz główny to para (provider, subject); wiersz
#: nie ma odpowiednika w `users`, bo osoba wchodzi przez agenta, nie przez dashboard.
EXTERNAL_IDENTITIES_COLUMNS: tuple[str, ...] = (
    "provider",
    "subject",
    "organization_id",
    "workspace_id",
    "project_id",
    "email",
    "display_name",
    "created_at",
    "last_seen_at",
    "disabled_at",
)

#: Kolumny 0028: dokumenty projektu jako widok na rejestr ustaleń (P8 etap A, D-043).
PROJECT_DOCUMENTS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    # NULL dla dokumentu przestrzeni (podręcznik platformy). Nie „dowolny projekt".
    "project_id",
    "role",
    "platform",
    "created_at",
)
PROJECT_DOCUMENT_VERSIONS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "document_id",
    "version",
    "title",
    "generated_by",
    "prompt_name",
    "prompt_sha256",
    # Skrót z posortowanych identyfikatorów i dat ustaleń: ten sam rejestr, ta sama wersja.
    "input_sha256",
    "item_count",
    "generated_at",
)
PROJECT_DOCUMENT_SENTENCES_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "version_id",
    "position",
    "section",
    "text",
    # Tablica identyfikatorów ustaleń, NIEPUSTA z więzu: zdanie bez podstawy nie jest widokiem
    # na rejestr, tylko drugą pamięcią.
    "basis",
)
DOCUMENT_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "project_documents": PROJECT_DOCUMENTS_COLUMNS,
    "project_document_versions": PROJECT_DOCUMENT_VERSIONS_COLUMNS,
    "project_document_sentences": PROJECT_DOCUMENT_SENTENCES_COLUMNS,
}

#: Zamknięte listy 0028.
DOCUMENT_ROLES: frozenset[str] = frozenset(
    {"PROJECT_CARD", "WHAT_WORKS", "DECISIONS", "COMMUNITIES", "PLATFORM_HANDBOOK"}
)
DOCUMENT_GENERATED_BY: frozenset[str] = frozenset({"RULES", "MODEL"})

IDENTITY_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": USERS_COLUMNS,
    "memberships": MEMBERSHIPS_COLUMNS,
    "invitations": INVITATIONS_COLUMNS,
    "data_export_jobs": DATA_EXPORT_JOBS_COLUMNS,
    "data_deletion_jobs": DATA_DELETION_JOBS_COLUMNS,
}

#: Exactly two. A third would be RBAC growing back where D-016 said it must not (L4253, L4361):
#: owners invite and revoke, members do everything else the product does, and nothing in the
#: product asks a finer question than that.
MEMBERSHIP_ROLES: frozenset[str] = frozenset({"OWNER", "MEMBER"})

DATA_EXPORT_STATUSES: frozenset[str] = frozenset(
    {"REQUESTED", "RUNNING", "READY", "FAILED", "PURGED"}
)
DATA_DELETION_STATUSES: frozenset[str] = frozenset({"REQUESTED", "PURGED", "CANCELLED", "FAILED"})


class PartialUniqueIndex(NamedTuple):
    """One partial unique index of 0013, named so the test can look it up by name.

    Named rather than found by "an index mentioning this column", for the reason
    `test_enum_parity` gives about CHECK constraints: a search by shape answers nothing after a
    typo and says nothing about it, while a lookup by name fails loudly.

    `predicate_column` is the column whose NULL means "still active". The rule these indexes
    carry — one active attachment, one active assignment per pair — has to survive two
    concurrent inserts, and a partial unique index is the only one of CHECK / trigger / index
    that does (0001 says the same about `browser_profiles_one_active_per_account`).
    """

    name: str
    table: str
    columns: tuple[str, ...]
    predicate_column: str


#: The two rules of 0013 that are enforced by the database rather than by a code path anybody
#: could forget to call.
WORKSPACE_ACTIVE_INDEXES: tuple[PartialUniqueIndex, ...] = (
    PartialUniqueIndex(
        "person_profile_accounts_one_active_per_account",
        "person_profile_accounts",
        ("social_account_id",),
        "detached_at",
    ),
    PartialUniqueIndex(
        "project_account_assignments_one_active_per_pair",
        "project_account_assignments",
        ("project_id", "social_account_id"),
        "unassigned_at",
    ),
    # 0014: two LIVE connections may not point at one directory. L2159 forbids two App Servers on
    # one CODEX_HOME; the runtime lease file catches it while they run, this index stops the pair
    # from being created at all.
    PartialUniqueIndex(
        "codex_connections_one_live_per_home",
        "codex_connections",
        ("home_ref",),
        "disconnected_at",
    ),
)

#: Tables that carry `workspace_id` as a NULLABLE column because the document says so, and are
#: therefore NOT workspace tables:
#:
#:   * `audit_events` — 0001 L288. D-020 forbids retrofitting the tenant triple onto tables
#:     from 0001; 0002 adds only the composite FK, with MATCH SIMPLE so an organization-scoped
#:     event stays legal.
#:   * `jobs` — L3825 spells out "workspace_id nullable", and L3884 relies on it:
#:     "Job organizacyjny z `workspace_id IS NULL` przechodzi wyłącznie przez org-scope policy".
#:
#:   * `codex_connections` — 0014. A Codex connection is access to the ENGINE, and WIZJA-WORKSPACE
#:     says one connection may serve many sessions: "limity konta nie stają się osobnym
#:     przydziałem dla każdego workspace'u". NULL means "available to every workspace of this
#:     organization", a value narrows it to one. The isolation of context is the session's job,
#:     not the connection's.
#:
#: This list exists so that the reverse check has something to compare against: any OTHER table
#: growing a `workspace_id` column must either join `WORKSPACE_TABLES` or be added here with a
#: reason. Silence about a fourth one is the failure this pair of lists prevents.
MIXED_SCOPE_TABLES: tuple[str, ...] = ("audit_events", "jobs", "codex_connections")


class EnforcedPairKey(NamedTuple):
    """A composite foreign key that must be enforced with NO null-escape, ever.

    `columns` is the exact local column list, in order. It is written out rather than derived,
    because the defect this guards against ADDS a column to the key.
    """

    table: str
    parent: str
    columns: tuple[str, ...]


#: The composite foreign keys of the three QUEUE tables of migration 0003.
#:
#: These tables are ORGANIZATION-scoped, not workspace-scoped: an attempt belongs to a job, and
#: a job may be organizational (`jobs.workspace_id` is nullable — see MIXED_SCOPE_TABLES). So the
#: key is the PAIR `(organization_id, <parent>_id)` and every column in it is NOT NULL.
#:
#: Why that last clause is the whole invariant, quoted from 0003 lines 121-126: "Trójka
#: z NULL-owym `workspace_id` i MATCH SIMPLE byłaby GORSZA niż para: przy NULL-u MATCH SIMPLE
#: uznaje klucz za spełniony BEZ SPRAWDZANIA `job_id`, czyli próba mogłaby wskazywać na
#: nieistniejący job." PostgreSQL's default match type IS MATCH SIMPLE, and under it a key with
#: any NULL column is satisfied without looking at the parent at all. The pair is safe only
#: because neither of its columns can be NULL — so "NOT NULL" is not a detail of these columns,
#: it is the mechanism, and it is what the test asserts.
#:
#: Measured on 2026-08-31: dropping all three of these keys left the suite at 54 passed, and so
#: did replacing the `job_attempts` pair with a nullable-`workspace_id` triple under MATCH
#: SIMPLE. Nothing inserted an attempt pointing at a job that does not exist.
QUEUE_PAIR_KEYS: tuple[EnforcedPairKey, ...] = (
    EnforcedPairKey("job_attempts", "jobs", ("organization_id", "job_id")),
    EnforcedPairKey("job_leases", "jobs", ("organization_id", "job_id")),
    EnforcedPairKey("job_leases", "job_attempts", ("organization_id", "attempt_id")),
)


# ---------------------------------------------------------------------------
# Column lists transcribed from the document
# ---------------------------------------------------------------------------

#: `jobs` — the THIRTEEN columns of L3823-3835, in the document's order.
#: `workspace_id` is nullable there and stays nullable here (see MIXED_SCOPE_TABLES).
JOBS_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "job_type",
    "routing_key",
    "priority",
    "payload_ref",
    "dispatch_version",
    "status",
    "not_before",
    "attempt_limit",
    "idempotency_key",
    "lock_version",
)

#: The fields L3838 requires of BOTH `job_attempts` and `job_leases` ("zapisują co najmniej").
#: `attempt_id` is the primary key of `job_attempts` and a foreign key on `job_leases`; both
#: tables carry it, which is what the sentence asks for.
JOB_ATTEMPT_FIELDS: tuple[str, ...] = (
    "attempt_id",
    "job_id",
    "attempt_no",
    "dispatch_version",
    "lease_generation",
    "lease_owner",
    "status",
    "not_before",
    "heartbeat_at",
    "expires_at",
    "started_at",
    "completed_at",
    "failure_class",
)

#: `action_proposal_revisions` — the SIXTEEN columns of L3686-3703, in the document's order.
ACTION_PROPOSAL_REVISION_COLUMNS: tuple[str, ...] = (
    "id",
    "organization_id",
    "workspace_id",
    "proposal_id",
    "revision",
    "payload",
    "payload_hash",
    "source_fingerprint",
    "intelligence_profile_version_id",
    "plan_version_id",
    "memory_snapshot_id",
    "planning_context_snapshot_id",
    "task_resource_binding_ids",
    "prompt_version_id",
    "created_by_model_call_id",
    "expires_at",
)

#: `execution_intents` — the NINE columns of L3707-3719 ("wiąże dokładnie"), in order.
#: The table also carries `id` plus the fields `contracts.ExecutionIntent` persists
#: (`payload_hash`, `account_id`, `assignment_version_id`, `issued_at`, `consumed_at`);
#: those are additions, and the nine below are the requirement.
EXECUTION_INTENT_COLUMNS: tuple[str, ...] = (
    "organization_id",
    "workspace_id",
    "proposal_revision_id",
    "approval_decision_id",
    "task_resource_binding_ids",
    "planning_context_snapshot_id",
    "one_time_grant_hash",
    "expires_at",
    "status",
)


# ---------------------------------------------------------------------------
# Closed value lists kept as `text` + CHECK rather than as a native enum
# ---------------------------------------------------------------------------
#
# Why text + CHECK and not CREATE TYPE: `ALTER TYPE ... ADD VALUE` cannot run inside a
# transaction block and cannot be undone, so a native enum turns "we named a state wrong" into
# a one-way migration. The pattern already exists in 0001 — `capsule_mode_transitions.reason`
# around L320 of that file — and 0003 follows it for every new closed list.

#: Lifecycle statuses of a row in `jobs` that are NOT one of the `WAITING_*` states.
#:
#: The document never enumerates job statuses (grep: `status` appears in the `jobs` column list
#: at L3831 and nowhere as a value list), so these five are derived from what the Definition of
#: Done requires the queue to be able to say:
#:   READY      — dispatchable; this is what `SELECT ... FOR UPDATE SKIP LOCKED` claims (L5193),
#:   RUNNING    — an attempt holds a live lease,
#:   SUCCEEDED  — terminal, work done,
#:   FAILED     — terminal after the last attempt; "ostatnia próba terminalizuje job zamiast
#:                tworzyć nieskończony redispatch" (L5214),
#:   CANCELLED  — terminal by operator decision.
#: The `WAITING_*` half is NOT written here — it is taken from `contracts.JobWaitingState`, so
#: that a new waiting state added to the contracts breaks the parity test instead of quietly
#: becoming a status the database rejects (L2144, L2147, L5288).
JOB_LIFECYCLE_STATUSES: frozenset[str] = frozenset(
    {"READY", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"}
)

#: Statuses of one row in `job_attempts`. `LEASE_EXPIRED` is deliberately distinct from
#: `FAILED`: the reaper closing an attempt whose lease ran out is not the same event as a
#: worker reporting a failure, and the attempt limit of L5214 counts both. Collapsing them
#: would make "restart komponentu nie gubi taska" (L5212) untestable — a reaped attempt would
#: be indistinguishable from a rejected one.
JOB_ATTEMPT_STATUSES: frozenset[str] = frozenset(
    {"RUNNING", "SUCCEEDED", "FAILED", "LEASE_EXPIRED"}
)

#: Statuses of one row in `job_leases`. Same five words as `lease_state` in 0001, because it is
#: the same notion one level up: the lease is authoritative in PostgreSQL, losing the heartbeat
#: starts reconciliation rather than an immediate second start (L2371-2379, D-004).
JOB_LEASE_STATUSES: frozenset[str] = frozenset(
    {"ACTIVE", "RECONCILING", "RELEASED", "REVOKED", "EXPIRED"}
)

#: Statuses of an `execution_intents` row. A one-time grant is either unspent, spent, expired
#: or withdrawn — L2755 ("one-time ExecutionIntent"), L2821 ("maksymalnie jedna automatyczna
#: próba submitu na ExecutionIntent"), L5692 ("stale execution grant is single-use and expires").
EXECUTION_INTENT_STATUSES: frozenset[str] = frozenset({"ISSUED", "CONSUMED", "EXPIRED", "REVOKED"})

#: `approval_decisions.decision`. Two values and no third: L2774 draws `APPROVED | REJECTED`
#: out of `READY_FOR_APPROVAL`, and `EXPIRED` is a state of the revision, not a human decision.
APPROVAL_DECISION_VALUES: frozenset[str] = frozenset({"APPROVED", "REJECTED"})

#: `artifacts.kind`. The kinds the document names for phase E1: trace and screenshot (L3897),
#: plus the log and report files a run leaves behind. HAR is here because the URL/egress policy
#: work of F1 (L5197) produces network evidence and it must not be filed as a `LOG`.
#: 0003 declared five; 0016 added `SOURCE` for the raw material behind a knowledge source (P3).
#: Widened with DROP CONSTRAINT + ADD CONSTRAINT, the way 0010 widened the move list — a CHECK
#: has no `ALTER ... ADD VALUE`.
ARTIFACT_KINDS: frozenset[str] = frozenset(
    {"TRACE", "SCREENSHOT", "HAR", "LOG", "REPORT", "SOURCE", "EXPORT"}
)

#: `artifacts.acl` — "artifacts ACL i retention" (L5196). Three values, because there are
#: exactly three readers in E1: the operator, the Browser Runner, and both. Profile storage is
#: reachable only by the runner (L3894) and there is no third principal to grant anything to.
ARTIFACT_ACL_VALUES: frozenset[str] = frozenset(
    {"OPERATOR_ONLY", "RUNNER_ONLY", "OPERATOR_AND_RUNNER"}
)

#: `profile_deks.algorithm`. One value, on purpose: D-018 wraps a per-profile DEK, and a column
#: that accepts an algorithm nobody implemented would be a place for a silent downgrade.
PROFILE_DEK_ALGORITHMS: frozenset[str] = frozenset({"AES-256-GCM"})


class CheckedValueList(NamedTuple):
    """A named CHECK constraint, THE TABLE IT STANDS ON, and the values it must accept.

    The constraints are NAMED in the migration rather than left to PostgreSQL's
    `<table>_<column>_check` autonaming, so that this test can look one up by name and FAIL
    when it is missing. Searching `pg_constraint` for "a CHECK that mentions this column"
    would find nothing after a typo and report no drift.

    `table` IS PART OF THE LOOKUP KEY, and saying so here is the point of this docstring.
    Until 2026-08-31 it was dead data: `_check_values` in `test_enum_parity.py` searched
    `pg_constraint` by `conname` alone, so a constraint of the right name standing on a
    COMPLETELY DIFFERENT table satisfied the parity check while the column it was supposed to
    guard had no CHECK at all. Measured, not reasoned about: moving `artifacts_kind_is_known`
    off `artifacts` onto a decoy table left the whole suite at 54 passed, with
    `artifacts.kind` accepting any string. A name is not an identity in `pg_constraint` —
    `(conname, conrelid)` is — and a test matching on half of a key has exactly the detection
    power of no test.
    """

    constraint: str
    table: str
    values: frozenset[str]


def enumerated_checks(job_statuses: frozenset[str]) -> tuple[CheckedValueList, ...]:
    """Every closed value list that migration 0003 enforces with a named CHECK.

    `job_statuses` is a parameter and not a constant because half of it lives in the contracts
    package (`JobWaitingState`) — the caller composes it and the composition itself is asserted
    in `test_enum_parity.py`. Passing it in keeps this module free of an import that would let
    the two sources of the same truth drift apart unnoticed.
    """
    return (
        CheckedValueList("jobs_status_is_known", "jobs", job_statuses),
        CheckedValueList("job_attempts_status_is_known", "job_attempts", JOB_ATTEMPT_STATUSES),
        CheckedValueList("job_leases_status_is_known", "job_leases", JOB_LEASE_STATUSES),
        CheckedValueList(
            "approval_decisions_decision_is_known",
            "approval_decisions",
            APPROVAL_DECISION_VALUES,
        ),
        CheckedValueList(
            "execution_intents_status_is_known",
            "execution_intents",
            EXECUTION_INTENT_STATUSES,
        ),
        CheckedValueList("artifacts_kind_is_known", "artifacts", ARTIFACT_KINDS),
        # 0018: the three closed vocabularies of identity. A role or a status spelled differently
        # is a refusal, not a new category, for the same reason as everywhere else in this file.
        CheckedValueList("memberships_role_is_known", "memberships", MEMBERSHIP_ROLES),
        CheckedValueList(
            "data_export_jobs_status_is_known", "data_export_jobs", DATA_EXPORT_STATUSES
        ),
        CheckedValueList(
            "data_deletion_jobs_status_is_known", "data_deletion_jobs", DATA_DELETION_STATUSES
        ),
        CheckedValueList("artifacts_acl_is_known", "artifacts", ARTIFACT_ACL_VALUES),
        CheckedValueList("profile_deks_algorithm_is_known", "profile_deks", PROFILE_DEK_ALGORITHMS),
        # 0023: rodzaj wpisu do kolejki publikacji.
        CheckedValueList(
            "publication_requests_kind_is_known",
            "publication_requests",
            PUBLICATION_REQUEST_KINDS,
        ),
    )


#: Native PostgreSQL enum types created by 0001, paired with the name of the contracts enum
#: that must hold exactly the same values. The pairing is data so the test can assert it read
#: all nine pairs before comparing any of them.
NATIVE_ENUM_PARITY: tuple[tuple[str, str], ...] = (
    ("social_account_state", "SocialAccountState"),
    ("browser_profile_state", "BrowserProfileState"),
    ("browser_session_state", "BrowserSessionState"),
    ("capsule_mode", "CapsuleMode"),
    ("lease_state", "LeaseState"),
    ("interaction_lease_mode", "InteractionLeaseMode"),
    ("stream_session_role", "StreamSessionRole"),
    ("capability_state", "CapabilityState"),
    ("execution_mode", "ExecutionMode"),
)


__all__ = [
    "ACTION_PROPOSAL_REVISION_COLUMNS",
    "AGENT_SESSIONS_COLUMNS",
    "AGENT_SESSION_KINDS",
    "AGENT_SESSION_STATUSES",
    "APPROVAL_DECISION_VALUES",
    "ARTIFACT_ACL_VALUES",
    "ARTIFACT_KINDS",
    "AUDIENCE_METRICS",
    "AUDIENCE_PERIODS",
    "AUDIENCE_SNAPSHOTS_COLUMNS",
    "AUDIENCE_SOURCES",
    "CODEX_CONNECTIONS_COLUMNS",
    "CODEX_CONNECTION_FORBIDDEN_COLUMNS",
    "CODEX_CONNECTION_STATUSES",
    "COST_KINDS",
    "COST_LEDGER_COLUMNS",
    "DATA_DELETION_JOBS_COLUMNS",
    "DATA_DELETION_STATUSES",
    "DATA_EXPORT_JOBS_COLUMNS",
    "DATA_EXPORT_STATUSES",
    "DELEGATION_MODES",
    "DOCUMENT_GENERATED_BY",
    "DOCUMENT_ROLES",
    "DOCUMENT_TABLE_COLUMNS",
    "EXECUTION_INTENT_COLUMNS",
    "EXECUTION_INTENT_STATUSES",
    "IDENTITY_TABLE_COLUMNS",
    "INCIDENTS_COLUMNS",
    "INCIDENT_DETECTORS",
    "INVITATIONS_COLUMNS",
    "JOBS_COLUMNS",
    "JOB_ATTEMPT_FIELDS",
    "JOB_ATTEMPT_STATUSES",
    "JOB_LEASE_STATUSES",
    "JOB_LIFECYCLE_STATUSES",
    "KNOWLEDGE_ITEMS_COLUMNS",
    "KNOWLEDGE_ITEM_KINDS",
    "KNOWLEDGE_SOURCES_COLUMNS",
    "KNOWLEDGE_SOURCE_KINDS",
    "KNOWLEDGE_SOURCE_STATUSES",
    "KNOWLEDGE_STATED_BY",
    "KNOWLEDGE_TABLE_COLUMNS",
    "MEMBERSHIPS_COLUMNS",
    "MEMBERSHIP_ROLES",
    "MIXED_SCOPE_TABLES",
    "NATIVE_ENUM_PARITY",
    "OBSERVE_APPEND_ONLY_TABLES",
    "OBSERVE_TABLE_COLUMNS",
    "OBSERVE_TRIPLE_PARENTS",
    "OPS_TABLE_COLUMNS",
    "ORG_LIMITS_COLUMNS",
    "PERSON_PROFILES_COLUMNS",
    "PERSON_PROFILE_ACCOUNTS_COLUMNS",
    "PERSON_VOICE_VERSIONS_COLUMNS",
    "PRESENCE_TABLE_COLUMNS",
    "PROFILE_DEK_ALGORITHMS",
    "PROJECTS_COLUMNS",
    "PROJECT_ACCOUNT_ASSIGNMENTS_COLUMNS",
    "PROJECT_DELEGATIONS_COLUMNS",
    "PROJECT_DOCUMENTS_COLUMNS",
    "PROJECT_DOCUMENT_SENTENCES_COLUMNS",
    "PROJECT_DOCUMENT_VERSIONS_COLUMNS",
    "PROJECT_VISITS_COLUMNS",
    "PUBLICATION_REQUESTS_COLUMNS",
    "PUBLICATION_REQUEST_KINDS",
    "QUEUE_PAIR_KEYS",
    "RENAMED_IN_0002",
    "RENAME_CATALOGS",
    "SERVICE_COMPONENTS",
    "SERVICE_HEARTBEATS_COLUMNS",
    "SESSION_APPEND_ONLY_TABLES",
    "SESSION_EVENTS_COLUMNS",
    "SESSION_EVENT_KINDS",
    "SESSION_TABLE_COLUMNS",
    "USERS_COLUMNS",
    "WAKEUPS_COLUMNS",
    "WAKEUP_EVENTS_COLUMNS",
    "WAKEUP_EVENT_KINDS",
    "WAKEUP_REASONS",
    "WORKSPACE_ACTIVE_INDEXES",
    "WORKSPACE_TABLES",
    "WORKSPACE_TABLE_COLUMNS",
    "WORKSPACE_TRIPLE_PARENTS",
    "CheckedValueList",
    "EnforcedPairKey",
    "PartialUniqueIndex",
    "RenamedObject",
    "enumerated_checks",
]
