/**
 * Every screen of the dashboard, declared once, in two groups that are not the same product.
 *
 * SINCE THE PIVOT (D-038 to D-040, docs/ROADMAP-PIVOT.md) THERE ARE TWO SHELLS:
 *
 *   * **studio** — what the product is: a workspace on the left, its projects, the people and
 *     accounts behind them, and the conversation in the middle. Its paths carry the workspace in
 *     the URL (`/w/<id>/…`), because the workspace is the scope of everything below it and a
 *     "current workspace" remembered on the server is a scope nobody can link to or bookmark;
 *   * **operations** — the operator panel this repository had before the pivot: the job queue,
 *     the audit log, the observed community, the two signposts. It is NOT deleted (the founder's
 *     brief says so outright) and it is NOT the front door any more. It hangs off the studio
 *     under one entry, "Operacje", and lives in the `(operations)` route group so that it can
 *     keep its own chrome without the studio inheriting it.
 *
 * The route group parentheses do NOT appear in the URL, which is why `href` and `file` are two
 * separate fields here. They used to be derivable from each other; after the pivot they are not,
 * and deriving one from the other is how a test would start asserting against a path that does
 * not exist.
 *
 * `href`, `id` and `file` are English — they are code (D-010). `label` is Polish: the founder
 * reads it.
 */

/** One page file under `app/`, whether or not it appears in a navigation. */
export interface PageRoute {
  /** Stable identifier, used by the views and by `labelOf`. */
  readonly id: string;
  /** URL pattern. `[param]` segments are Next.js dynamic segments. */
  readonly href: string;
  /** Path of the file under `app/`, route group parentheses included. */
  readonly file: string;
  /** `false` for the ONE screen that must be reachable without a session: the login. */
  readonly gated: boolean;
}

/** A page that also appears in a navigation, with the label the founder sees. */
export interface NavRoute extends PageRoute {
  readonly label: string;
  /**
   * `true` when the screen reads the control plane. `false` marks a SIGNPOST — a screen that
   * measures nothing and therefore must never render an empty list.
   */
  readonly reads: boolean;
}

/**
 * The studio's own pages. Only `people` and `codex` are fixed entries in the sidebar; the
 * workspace root and a project are reached by choosing one, so they carry a label for the
 * heading and are not rendered as nav items.
 *
 * `codex` reads the control plane since P1 etap 2 (migration 0014): it lists the connections of
 * the organization and reports what the configured `CODEX_HOME` really holds. Before that it was
 * a signpost with `reads: false`, which was the honest flag while nothing behind it existed.
 */
export const STUDIO_ROUTES: readonly NavRoute[] = [
  { id: "studio", href: "/", file: "(studio)/page.tsx", gated: true, label: "Studio", reads: true },
  {
    id: "workspace",
    href: "/w/[workspaceId]",
    file: "(studio)/w/[workspaceId]/page.tsx",
    gated: true,
    label: "Przegląd workspace'u",
    reads: true,
  },
  {
    id: "project",
    href: "/w/[workspaceId]/p/[projectId]",
    file: "(studio)/w/[workspaceId]/p/[projectId]/page.tsx",
    gated: true,
    label: "Projekt",
    reads: true,
  },
  {
    // D-045: projekt jest zbiorem rozmów, więc rozmowa ma własny adres. Bez niego dwie rozmowy
    // jednego projektu są nie do rozróżnienia linkiem, a otwarta zawsze jest ta najnowsza.
    id: "conversation",
    href: "/w/[workspaceId]/p/[projectId]/c/[sessionId]",
    file: "(studio)/w/[workspaceId]/p/[projectId]/c/[sessionId]/page.tsx",
    gated: true,
    label: "Rozmowa",
    reads: true,
  },
  {
    /**
     * E2: pasek boczny referencji ma w projekcie pięć pozycji, a my mieliśmy cztery, w tym jedną
     * wymyśloną („Dokumenty" jako kotwica). Publikacje i Społeczności to te dwie brakujące.
     *
     * OBIE SĄ POD PROJEKTEM, nie pod przestrzenią, i to jest ustalenie z audytu UX (§6): wyniki
     * całej przestrzeni pokazane na ekranie projektu wyglądają jak praca tego agenta. Adres
     * niesie projekt, żeby dzień, w którym odczyt nauczy się filtrować po projekcie, nie był
     * dniem przenoszenia ekranu pod inną trasę.
     */
    id: "publications",
    href: "/w/[workspaceId]/p/[projectId]/publications",
    file: "(studio)/w/[workspaceId]/p/[projectId]/publications/page.tsx",
    gated: true,
    label: "Publikacje",
    // `reads: true` od E6 i to jest DRUGI stan tej flagi w ciągu jednego wieczoru. Przez kilka
    // godzin ekran był drogowskazem, bo odczyt publikacji filtrował po przestrzeni, nie po
    // projekcie; wtedy `reads: false` było jedyną prawdą. Trasa `…/projects/{id}/results`
    // (wenext-84) daje publikacje TEGO projektu, więc flaga wraca razem z zapytaniem, a nie
    // „przy okazji".
    reads: true,
  },
  {
    /**
     * D-055: "Pokój", the screen the founder opens from the Codex plugin. Real people who write
     * right now about the problem the project solves, replies ready in the founder's voice, and
     * what already went out, in numbers. Under the project like its siblings: it is the answer to
     * ONE project's goal, and a room of the whole workspace would show one project's people under
     * another project's name (the §6 finding of the UX audit, again).
     */
    id: "room",
    href: "/w/[workspaceId]/p/[projectId]/room",
    file: "(studio)/w/[workspaceId]/p/[projectId]/room/page.tsx",
    gated: true,
    label: "Pokój",
    reads: true,
  },
  {
    id: "communities",
    href: "/w/[workspaceId]/p/[projectId]/communities",
    file: "(studio)/w/[workspaceId]/p/[projectId]/communities/page.tsx",
    gated: true,
    label: "Społeczności",
    reads: true,
  },
  {
    id: "people",
    href: "/w/[workspaceId]/people",
    file: "(studio)/w/[workspaceId]/people/page.tsx",
    gated: true,
    label: "Profile i konta",
    reads: true,
  },
  {
    id: "codex",
    href: "/w/[workspaceId]/codex",
    file: "(studio)/w/[workspaceId]/codex/page.tsx",
    gated: true,
    label: "Połączenia Codex",
    reads: true,
  },
  {
    id: "organization",
    href: "/w/[workspaceId]/organization",
    file: "(studio)/w/[workspaceId]/organization/page.tsx",
    gated: true,
    label: "Przestrzeń",
    reads: true,
  },
  {
    id: "knowledge",
    href: "/w/[workspaceId]/p/[projectId]/knowledge",
    file: "(studio)/w/[workspaceId]/p/[projectId]/knowledge/page.tsx",
    gated: true,
    label: "Materiały",
    reads: true,
  },
  {
    id: "presence",
    href: "/w/[workspaceId]/p/[projectId]/presence",
    file: "(studio)/w/[workspaceId]/p/[projectId]/presence/page.tsx",
    gated: true,
    label: "Obecność",
    reads: true,
  },
];

/**
 * The operator panel's navigation, unchanged in content since before the pivot.
 *
 * WHY `/transitions` AND `/approvals` STILL READ NOTHING: measured at commit 98570ed and still
 * true, `control_plane/api/` mounts no route listing state transitions or the approval path. A
 * table on those screens could only be an empty frame nobody read anything into. The check is
 * `grep -rn "APIRouter" services/control_plane/control_plane/api/`; when a reader appears, the
 * signpost stops being honest and becomes a list view.
 */
export const NAV_ROUTES: readonly NavRoute[] = [
  {
    id: "overview",
    href: "/operations",
    file: "(operations)/operations/page.tsx",
    gated: true,
    label: "Przegląd",
    reads: true,
  },
  {
    id: "community",
    href: "/community",
    file: "(operations)/community/page.tsx",
    gated: true,
    label: "Community",
    reads: true,
  },
  {
    id: "jobs",
    href: "/jobs",
    file: "(operations)/jobs/page.tsx",
    gated: true,
    label: "Kolejka jobów",
    reads: true,
  },
  {
    id: "audit",
    href: "/audit",
    file: "(operations)/audit/page.tsx",
    gated: true,
    label: "Audit events",
    reads: true,
  },
  {
    id: "transitions",
    href: "/transitions",
    file: "(operations)/transitions/page.tsx",
    gated: true,
    label: "Przejścia stanów",
    reads: false,
  },
  {
    id: "approvals",
    href: "/approvals",
    file: "(operations)/approvals/page.tsx",
    gated: true,
    label: "Ścieżka zgody",
    reads: false,
  },
];

/**
 * Pages that exist but are in no navigation.
 *
 * `gated: false` is the ONLY exception to the auth boundary and there is exactly one of it.
 * `tests/source_scan.test.ts` walks `app/` and requires every page file to be declared in one of
 * the three lists, and every gated one to call `requireOperator` — a page added later without
 * the call is the hole a hand-written list of protected routes always eventually has.
 */
export const NON_NAV_ROUTES: readonly PageRoute[] = [
  { id: "login", href: "/login", file: "login/page.tsx", gated: false },
  // P7: the two other doors of a product with more than one customer. `gated: false` for the
  // same reason as the login: this is where a credential is CREATED, so a session cannot be
  // required first. Three ungated pages is the complete list, and `source_scan` walks the tree
  // to keep it that way.
  { id: "register", href: "/register", file: "register/page.tsx", gated: false },
  { id: "invitation", href: "/invitation", file: "invitation/page.tsx", gated: false },
  {
    id: "job-detail",
    href: "/jobs/[jobId]",
    file: "(operations)/jobs/[jobId]/page.tsx",
    gated: true,
  },
];

/** Every declared page, in one list, so a scan has a single thing to compare the disk against. */
export const ALL_PAGES: readonly PageRoute[] = [
  ...STUDIO_ROUTES,
  ...NAV_ROUTES,
  ...NON_NAV_ROUTES,
];

/**
 * The operator-facing label of a declared route. THROWS on an unknown id.
 *
 * A view rendering an empty heading is the same class of defect as a nav entry pointing at a
 * route with no page: wrong, and invisible. The views call this instead of writing their own
 * heading, so a label renamed here is renamed on the screen too.
 */
export function labelOf(routeId: string): string {
  const route = [...STUDIO_ROUTES, ...NAV_ROUTES].find((candidate) => candidate.id === routeId);
  if (route === undefined) {
    throw new Error(
      `Ekran odwołuje się do trasy '${routeId}', której nie ma w lib/routes.ts. ` +
        "nawigacja i ekrany muszą pochodzić z tej samej listy",
    );
  }
  return route.label;
}

/** Where an unauthenticated request is sent. One constant, used by the guard and by the tests. */
export const LOGIN_PATH = "/login";

/** Where a successful login lands: the studio, which is what the product is now. */
export const AFTER_LOGIN_PATH = "/";

/** The operator panel's entry point, linked from the studio sidebar under "Operacje". */
export const OPERATIONS_PATH = "/operations";

// ---------------------------------------------------------------------------
// Studio paths, built in ONE place
// ---------------------------------------------------------------------------
//
// Every one of these carries the workspace. A helper rather than template literals scattered
// through the screens: a link that lost the workspace segment would silently land on the studio
// root, which picks the FIRST workspace — so the bug would look like "it opened the wrong
// workspace" rather than like a broken link.

export function workspaceHref(workspaceId: string): string {
  return `/w/${encodeURIComponent(workspaceId)}`;
}

export function projectHref(workspaceId: string, projectId: string): string {
  return `${workspaceHref(workspaceId)}/p/${encodeURIComponent(projectId)}`;
}

/**
 * Jedna rozmowa projektu (D-045).
 *
 * Adres niesie projekt ORAZ rozmowę, choć sam identyfikator rozmowy wystarczyłby do odczytu.
 * Powód jest po stronie człowieka, nie bazy: po tym adresie widać, do którego projektu się
 * wraca, a pasek boczny umie zaznaczyć właściwy projekt bez dodatkowego odczytu.
 */
export function conversationHref(
  workspaceId: string,
  projectId: string,
  sessionId: string,
): string {
  return `${projectHref(workspaceId, projectId)}/c/${encodeURIComponent(sessionId)}`;
}

/**
 * Co ten projekt wysłał w świat, i gdzie.
 *
 * Dwa osobne ekrany zamiast jednej listy „rezultaty", bo społeczność i publikacja to dwa różne
 * pytania: gdzie jesteśmy, a co tam powiedzieliśmy. Jedna lista odpowiadała na oba naraz i przez
 * to na żadne (audyt UX §6).
 */
export function publicationsHref(workspaceId: string, projectId: string): string {
  return `${projectHref(workspaceId, projectId)}/publications`;
}

/**
 * "Pokój": opportunities, ready replies and results of one project, on one screen (D-055).
 *
 * The address the plugin opens and the control plane's `pokoj` field names; built here so no screen
 * types it by hand.
 */
export function roomHref(workspaceId: string, projectId: string): string {
  return `${projectHref(workspaceId, projectId)}/room`;
}

export function communitiesHref(workspaceId: string, projectId: string): string {
  return `${projectHref(workspaceId, projectId)}/communities`;
}

export function peopleHref(workspaceId: string): string {
  return `${workspaceHref(workspaceId)}/people`;
}

export function codexHref(workspaceId: string): string {
  return `${workspaceHref(workspaceId)}/codex`;
}

/** Who works in this space, and what the customer may do with their own data (P7). */
export function organizationHref(workspaceId: string): string {
  return `${workspaceHref(workspaceId)}/organization`;
}

/**
 * Materials and findings of one project.
 *
 * Under the project rather than under the workspace, and that is the product decision P3 makes:
 * what the agent read is read FOR something. A workspace-wide pile of materials would be a
 * library nobody maintains; this list belongs to the goal it serves.
 */
export function knowledgeHref(workspaceId: string, projectId: string): string {
  return `${projectHref(workspaceId, projectId)}/knowledge`;
}

/**
 * What the agent did while nobody was watching, and what still waits for a person.
 *
 * One screen and not two, although it answers two questions (P4). They belong together: a
 * decision waiting is only urgent BECAUSE the agent is working, and a queue of refusals on its
 * own screen is a list nobody opens.
 */
export function presenceHref(workspaceId: string, projectId: string): string {
  return `${projectHref(workspaceId, projectId)}/presence`;
}
