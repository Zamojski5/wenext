import { ResultsBlock } from "../../../../components/studio/results";
import { FirstRun } from "../../../../components/studio/first-run";
import { StudioShell } from "../../../../components/studio/shell";
import { getWorkspaceResults } from "../../../../lib/control-plane";
import { requireOperator } from "../../../../lib/guard";
import { projectHref, workspaceHref } from "../../../../lib/routes";
import { loadStudioFrame } from "../../../../lib/studio-frame";
import { modeLabel } from "../../../../lib/studio-labels";
import { StudioMissing, StudioUnreachable } from "../../../../components/studio/states";

/**
 * `GET /w/<workspace>` — the workspace with no project chosen yet.
 *
 * It is NOT a dashboard of counters. WIZJA-WORKSPACE says the centre is the work, and with no
 * project selected the work is choosing one; so the screen lists the projects with what each of
 * them actually is (goal, mode, paused), and then the results the whole workspace really has.
 *
 * Four outcomes, four screens, and none impersonates another: no session (redirect), control
 * plane unreachable (a sentence with the reason), 404 from the control plane (a measured "there
 * is no such workspace"), and the workspace itself.
 */
export const dynamic = "force-dynamic";

export default async function WorkspacePage({
  params,
  searchParams,
}: {
  readonly params: Promise<{ readonly workspaceId: string }>;
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { workspaceId } = await params;
  const parameters = await searchParams;
  const rawInfo = parameters["info"];
  const info = typeof rawInfo === "string" ? rawInfo : undefined;

  const gate = await requireOperator(workspaceHref(workspaceId));
  if (gate.kind === "unreachable") {
    return <StudioUnreachable reason={gate.reason} />;
  }

  const frame = await loadStudioFrame(gate.token, workspaceId);
  if (!frame.ok) {
    return <StudioUnreachable reason={frame.reason} />;
  }
  if (frame.value === null) {
    return <StudioMissing what="workspace" />;
  }

  const results = await getWorkspaceResults(gate.token, workspaceId);

  return (
    <StudioShell
      active={{ kind: "workspace" }}
      crumb={frame.value.workspace.name}
      frame={frame.value}
      info={info}
    >
      <h1 className="st__title">{frame.value.workspace.name}</h1>
      <p className="st__lede">
        Wybierz projekt po lewej albo załóż nowy. Projekt niesie cel, tryb pracy i konta, pod
        którymi agent działa.
      </p>

      <section className="st__section">
        <div className="st__section-head">Projekty</div>
        {frame.value.projects.length === 0 ? (
          <>
            <p className="st-empty">
              Nie ma jeszcze projektu. Odczyt się udał, po prostu nic tu nie założono.
            </p>
            <FirstRun step={1} />
          </>
        ) : (
          <div className="st-surface">
            {frame.value.projects.map((project) => (
              <a
                className="st-row"
                href={projectHref(workspaceId, project.project_id)}
                key={project.project_id}
              >
                <span className="st-row__body">
                  <strong>{project.name}</strong>
                  <small>{project.goal === "" ? "Cel jeszcze nieopisany" : project.goal}</small>
                </span>
                <span className="st-tag">{modeLabel(project.mode)}</span>
                {project.paused ? (
                  <span className="st-tag" data-tone="absent">
                    wstrzymany
                  </span>
                ) : null}
              </a>
            ))}
          </div>
        )}
      </section>

      <ResultsBlock
        reason={results.ok ? undefined : results.reason}
        results={results.ok && results.value !== null ? results.value : undefined}
      />
    </StudioShell>
  );
}
