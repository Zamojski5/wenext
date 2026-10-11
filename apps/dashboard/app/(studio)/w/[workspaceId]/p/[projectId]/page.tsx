import { ActivityLine } from "../../../../../../components/studio/activity-line";
import { AssignmentLink } from "../../../../../../components/studio/assignment";
import { Composer } from "../../../../../../components/studio/composer";
import { ConversationList } from "../../../../../../components/studio/conversation-list";
import { FirstRun } from "../../../../../../components/studio/first-run";
import { StudioShell } from "../../../../../../components/studio/shell";
import { StudioMissing, StudioUnreachable } from "../../../../../../components/studio/states";
import {
  getCodexConnections,
  getPeople,
  getProjectReturn,
  getProject,
} from "../../../../../../lib/control-plane";
import { requireOperator } from "../../../../../../lib/guard";
import { projectHref } from "../../../../../../lib/routes";
import { conversationsOf } from "../../../../../../lib/conversations";
import {
  getRecentConversations,
  getSessions,
} from "../../../../../../lib/sessions-client";
import { loadStudioFrame } from "../../../../../../lib/studio-frame";

/**
 * `GET /w/<workspace>/p/<project>` — one project: its goal, its mode, its accounts, its results.
 *
 * THE ORDER ON THIS SCREEN IS THE ORDER OF THE QUESTIONS THE USER ACTUALLY HAS (Z1 to Z4 in
 * ZAKRES-FUNKCJONALNY §1): what did I ask for, how is it allowed to work, under which identities,
 * what came of it — and then the field where the direction gets changed. Counters do not open
 * the screen; the goal does.
 *
 * WHAT AN ASSIGNED ACCOUNT SHOWS. Its platform's real capabilities, from the backend's matrix.
 * An account on X or LinkedIn is listed with "integracja niedostępna" next to it, because P1 says
 * outright that such an account must not pass for a working integration — and the place that
 * would let it pass is exactly here, in a list of "accounts this project works with".
 *
 * THE RETURN BLOCK SITS ABOVE THE CONVERSATION (P4 i P5). Somebody who was away for two days
 * needs one answer before any other: what happened while I was gone, and does anything wait for
 * me. It is counted from rows, never written by a model, and every line leads to its source. It
 * is deliberately NOT inside the conversation or the presence screen: the conversation is what
 * was said, presence is what the agent may do, and this is what happened.
 *
 * THE CONVERSATION IS THE LAST BLOCK AND IT IS REAL SINCE P2. The newest session of this project
 * is rendered with its whole trace: what was said, which tools the agent reached for and what
 * they answered. When there is no session yet, the composer entrusts one; when there is no Codex
 * connection, the composer says so instead of accepting a goal nothing would answer.
 *
 * THE PAGE IS `force-dynamic` AND DOES NOT POLL. A turn takes minutes and the trace is read from
 * the database on every render, so a reload is the refresh. A poll would be a second way to be
 * wrong about the state, and the state is worth exactly one source.
 */
export const dynamic = "force-dynamic";

export default async function ProjectPage({
  params,
  searchParams,
}: {
  readonly params: Promise<{ readonly workspaceId: string; readonly projectId: string }>;
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { workspaceId, projectId } = await params;
  const parameters = await searchParams;
  const rawInfo = parameters["info"];
  const info = typeof rawInfo === "string" ? rawInfo : undefined;

  const initialGoal = typeof parameters["prompt"] === "string" ? parameters["prompt"].slice(0, 4000) : "";
  const promptQuery = initialGoal ? `?${new URLSearchParams({ prompt: initialGoal })}` : "";
  const gate = await requireOperator(`${projectHref(workspaceId, projectId)}${promptQuery}`);
  if (gate.kind === "unreachable") {
    return <StudioUnreachable reason={gate.reason} />;
  }

  const frame = await loadStudioFrame(gate.token, workspaceId);
  if (!frame.ok) return <StudioUnreachable reason={frame.reason} />;
  if (frame.value === null) return <StudioMissing what="workspace" />;

  const detail = await getProject(gate.token, workspaceId, projectId);
  if (!detail.ok) return <StudioUnreachable reason={detail.reason} />;
  if (detail.value === null) return <StudioMissing what="projekt" />;

  const { project, assignments, candidates } = detail.value;
  const active = assignments.filter((row) => row.active);
  // Zdjęte przypisania. Nie znikają, bo control plane zapisuje `unassigned_at`, a ekran, który
  // by je pominął, twierdziłby, że tego konta nigdy tu nie było.
  const past = assignments.filter((row) => !row.active);
  const here = projectHref(workspaceId, projectId);
  // 0026: liczby domyślne przychodzą z control plane, żeby ekran nie znał ich na pamięć. Gdy
  // ich nie przyśle (starszy obraz api), etykieta MILCZY o domyślnej zamiast zmyślać liczbę.
  const domyslneTury =
    project.conversation_budget_default?.max_turns === undefined
      ? ""
      : ` (domyślnie ${project.conversation_budget_default.max_turns})`;
  const domyslneNarzedzia =
    project.conversation_budget_default?.max_tool_calls === undefined
      ? ""
      : ` (domyślnie ${project.conversation_budget_default.max_tool_calls})`;



  // D-045: STRONA PROJEKTU NIE JEST JUŻ ROZMOWĄ. Przedtem doczytywała tu ślad najnowszej sesji
  // i renderowała ją pod spodem, przez co starsza rozmowa znikała z ekranu w chwili, gdy ktoś
  // zaczął nową. Teraz to jest lista rozmów plus pytanie „co robimy", a pojedyncza rozmowa ma
  // własny adres `…/c/<session>`. Znika stąd jeden odczyt śladu na każde wejście.
  //
  // Nieudany odczyt listy NIE jest renderowany jako „nie ma rozmów": `undefined` znaczy „nie
  // wiadomo", a pusta tablica znaczy „sprawdzone, nie ma".
  const sessions = await getSessions(gate.token, workspaceId, projectId, true);
  const conversations = sessions.ok ? conversationsOf(sessions.value) : undefined;
  const recent = await getRecentConversations(gate.token, workspaceId);

  // Connections are the reason a session can start at all, so the composer is given the real
  // list rather than a boolean: with several, the person chooses which quota and identity runs.
  // Osoby przestrzeni, żeby konta w oknie przypisania stały w sekcjach po osobie, jak w
  // referencji (odbiór 1, różnica nr 4). Nieudany odczyt znaczy „bez sekcji", a nie pusty ekran:
  // wiersze kont i tak są, tylko bez nagłówka osoby.
  const people = await getPeople(gate.token, workspaceId);
  // Linia aktywności nad polem (E-H): jedno zdanie o tym, co się tu działo, odkąd człowieka nie
  // było. Odczyt jest ten sam, z którego żyje „Aktywność"; ekran bierze z niego dwie liczby i
  // godzinę, a nie cały raport.
  const back = await getProjectReturn(gate.token, workspaceId, projectId);
  const codex = await getCodexConnections(gate.token);
  const connections = codex.ok
    ? codex.value.connections
        .filter((connection) => connection.active)
        .map((connection) => ({ id: connection.connection_id, label: connection.label }))
    : [];

  return (
    <StudioShell
      active={{ kind: "project", projectId }}
      conversations={conversations}
      crumb={`${frame.value.workspace.name} / ${project.name}`}
      frame={frame.value}
      info={info}
      mode={project.paused ? "PAUSED" : project.mode}
      recent={recent.ok ? recent.value : null}
    >
      {/* D-045: pierwsze, co widać na projekcie, to pytanie i pole. Nazwa projektu jest w tym
          pytaniu, więc osobny nagłówek byłby tą samą nazwą dwa razy pod sobą. */}
      <Composer
        initialGoal={initialGoal}
        connections={connections}
        conversationCount={conversations?.length ?? 0}
        activity={
          <ActivityLine
            projectId={projectId}
            view={back.ok && back.value !== null ? back.value : undefined}
            workspaceId={workspaceId}
          />
        }
        meta={
          <AssignmentLink
            active={active}
            candidates={candidates}
            past={past}
            people={people.ok && people.value !== null ? people.value.profiles : []}
            projectName={project.name}
            projectId={projectId}
            returnTo={here}
            workspaceId={workspaceId}
          />
        }
        mode={project.mode}
        paused={project.paused}
        projectId={projectId}
        projectName={project.name}
        returnTo={here}
        workspaceId={workspaceId}
      >
        {/* Lista rozmów W KOLUMNIE, nad polem: pole stoi na dnie kolumny (`wm-bottom`), więc to,
            co ma być nad nim, jest jego rodzeństwem w tej samej kolumnie, a nie sekcją pod nim.
            Pasek jest do przeskakiwania, a ta lista niesie wynik jednym zdaniem, czyli to, po co
            się do rozmowy wraca. */}
        <ConversationList
          conversations={conversations}
          projectId={projectId}
          reason={sessions.ok ? undefined : sessions.reason}
          workspaceId={workspaceId}
        />
      </Composer>
      {/* CO STĄD ZNIKNĘŁO I DOKĄD POSZŁO (audyt UX z 20.09, element E2).
          Opis celu, opis trybu, ustawienia projektu, konta z datą przypisania, dokumenty,
          podsumowanie wizyty, drugie pole korekty i wyniki całej przestrzeni stały POD polem
          rozmowy i zamieniały wejście do projektu w ekran administracyjny: lista rozmów
          zaczynała się dopiero pod tym wszystkim.

          Tryb jest teraz chipem w nagłówku ramy, bo to jest stan projektu, a nie akapit.
          Reszta ma własne wejścia w pasku, w sekcji „W projekcie": ustalenia i dokumenty w
          „Wiedzy", obecność, powroty i wyniki w „Aktywności", konta w „Profilach i kontach".
          Tutaj zostaje pytanie, pole i rozmowy, czyli to, po co człowiek tu wchodzi. */}

      {/* Lista rozmów tego projektu, w treści, nie tylko w pasku. Pasek jest do przeskakiwania,
          a ta lista niesie wynik jednym zdaniem, czyli to, po co się do rozmowy wraca. */}
    </StudioShell>
  );
}
