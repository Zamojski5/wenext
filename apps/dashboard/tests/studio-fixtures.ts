/**
 * Row shapes for the studio view tests, and NOTHING that ships.
 *
 * Same rule as `tests/fixtures.ts`: these objects imitate what `api/workspaces.py` sends, and
 * they are NOT the proof that the screens read PostgreSQL. That proof is
 * `e2e/workspace_writes_postgres.e2e.test.ts`, which creates a workspace, a project, two profiles
 * and their accounts through the real forms of a real Next.js server talking to a real control
 * plane, and then looks for them again after a reload and a second login.
 */
import type { SessionRow } from "../lib/session-schema";
import type { StudioFrame } from "../lib/studio-frame";
import type {
  AccountSummary,
  AssignmentSummary,
  AttachmentSummary,
  PersonProfileSummary,
  PlatformSupport,
  ProjectSummary,
  WorkspaceResults,
  WorkspaceSummary,
} from "../lib/workspace-schema";

export const WORKSPACE_ID = "aaaaaaaa-0000-4000-8000-000000000001";
export const OTHER_WORKSPACE_ID = "aaaaaaaa-0000-4000-8000-000000000002";
export const PROJECT_ID = "bbbbbbbb-0000-4000-8000-000000000001";
export const PROFILE_ID = "cccccccc-0000-4000-8000-000000000001";
export const ACCOUNT_ID = "dddddddd-0000-4000-8000-000000000001";

const CREATED_AT = "2026-09-19T08:00:00+00:00";

export function workspace(overrides: Partial<WorkspaceSummary> = {}): WorkspaceSummary {
  return { workspace_id: WORKSPACE_ID, name: "Studio", created_at: CREATED_AT, ...overrides };
}

export function project(overrides: Partial<ProjectSummary> = {}): ProjectSummary {
  return {
    project_id: PROJECT_ID,
    workspace_id: WORKSPACE_ID,
    name: "Obecność devops",
    goal: "Zbuduj obecność wokół niezawodności agentów.",
    mode: "AUTONOMOUS",
    paused: false,
    paused_at: null,
    archived: false,
    created_at: CREATED_AT,
    // 0026: projekt bez własnego limitu rozmowy, czyli stan, w którym zaczyna każdy nowy.
    conversation_budget: null,
    conversation_budget_default: { max_turns: 30, max_tool_calls: 120 },
    ...overrides,
  };
}

export function profile(overrides: Partial<PersonProfileSummary> = {}): PersonProfileSummary {
  return {
    person_profile_id: PROFILE_ID,
    workspace_id: WORKSPACE_ID,
    name: "Anna",
    kind: "PERSON",
    created_at: CREATED_AT,
    archived: false,
    ...overrides,
  };
}

export function support(overrides: Partial<PlatformSupport> = {}): PlatformSupport {
  return {
    platform: "REDDIT",
    label: "Reddit",
    integrated: true,
    read: true,
    comment: true,
    own_post: false,
    effect_readback: true,
    audience_metrics: false,
    note: null,
    ...overrides,
  };
}

/** A platform the product cannot do anything on. The case P1 names explicitly. */
export function noIntegration(): PlatformSupport {
  return support({
    platform: "LINKEDIN",
    label: "LinkedIn",
    integrated: false,
    read: false,
    comment: false,
    effect_readback: false,
    note: "integracja niedostępna",
  });
}

export function account(overrides: Partial<AccountSummary> = {}): AccountSummary {
  return {
    social_account_id: ACCOUNT_ID,
    platform: "REDDIT",
    external_account_id: "Demo_Maintainer",
    state: "READY",
    created_at: CREATED_AT,
    person_profile_id: PROFILE_ID,
    attached_at: CREATED_AT,
    support: support(),
    ...overrides,
  };
}

export function attachment(overrides: Partial<AttachmentSummary> = {}): AttachmentSummary {
  return {
    attachment_id: "eeeeeeee-0000-4000-8000-000000000001",
    person_profile_id: PROFILE_ID,
    social_account_id: ACCOUNT_ID,
    platform: "REDDIT",
    external_account_id: "Demo_Maintainer",
    attached_at: CREATED_AT,
    detached_at: null,
    active: true,
    ...overrides,
  };
}

export function assignment(overrides: Partial<AssignmentSummary> = {}): AssignmentSummary {
  return {
    support: support(),
    assignment_id: "ffffffff-0000-4000-8000-000000000001",
    project_id: PROJECT_ID,
    social_account_id: ACCOUNT_ID,
    platform: "REDDIT",
    external_account_id: "Demo_Maintainer",
    assigned_at: CREATED_AT,
    unassigned_at: null,
    active: true,
    ...overrides,
  };
}

export function frame(overrides: Partial<StudioFrame> = {}): StudioFrame {
  return {
    workspaces: [workspace(), workspace({ workspace_id: OTHER_WORKSPACE_ID, name: "Druga" })],
    workspace: workspace(),
    projects: [project()],
    ...overrides,
  };
}

export function results(overrides: Partial<WorkspaceResults> = {}): WorkspaceResults {
  return {
    communities: [],
    publications: [],
    totals: { communities: 0, threads: 0, published: 0, confirmed_visible: 0 },
    ...overrides,
  };
}

export const SESSION_ID = "11111111-0000-4000-8000-000000000001";

/**
 * Jedna rozmowa projektu (D-045).
 *
 * Z POLAMI `tytul`, `wynik_zdanie` i `rodzaj`, bo od wdrożenia z 20.09 control plane przysyła je
 * zawsze. `tytul` jest tu taki, jaki wyliczyłby serwer z tego celu (pierwsze zdanie), żeby atrapa
 * nie opisywała odpowiedzi, której API nie daje.
 */
export function session(overrides: Partial<SessionRow> = {}): SessionRow {
  return {
    session_id: SESSION_ID,
    project_id: PROJECT_ID,
    goal: "Sprawdź, dlaczego odzew jest mały. Potem zaproponuj zmianę.",
    tytul: "Sprawdź, dlaczego odzew jest mały",
    wynik_zdanie: null,
    rodzaj: "CONVERSATION",
    status: "WAITING_USER",
    engine_thread_id: "th_1",
    result_summary: null,
    result_refs: [],
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
    finished_at: null,
    ...overrides,
  };
}
