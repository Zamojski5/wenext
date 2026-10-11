import { Pop } from "../../../../../components/studio/pop";
import { StudioShell } from "../../../../../components/studio/shell";
import { StudioMissing, StudioUnreachable } from "../../../../../components/studio/states";
import {
  getExports,
  getInvitations,
  getMembers,
  getOrganizations,
} from "../../../../../lib/control-plane";
import { requireOperator } from "../../../../../lib/guard";
import { roleLabel, roleNote } from "../../../../../lib/identity-schema";
import { organizationHref } from "../../../../../lib/routes";
import { loadStudioFrame } from "../../../../../lib/studio-frame";
import { moment } from "../../../../../lib/studio-labels";

/**
 * `GET /w/<workspace>/organization` — who works here, and what the customer may do with their data.
 *
 * P7 etap A. Everything on this screen is something that, before it, needed the founder to run
 * SQL: inviting a second person, taking access away, taking the data out, and deleting the
 * space. That is the milestone, so the screen is deliberately blunt about all four.
 *
 * WHAT IT SAYS THAT A PRETTIER SCREEN WOULD NOT:
 *
 *   * an invitation link is shown ONCE and is not sent anywhere. Etap A has no mail, and a
 *     screen implying otherwise would produce invitations that never arrive;
 *   * people who used to have access are listed with the moment it ended. "Who could see our
 *     data" is a question asked after something goes wrong, and a list of current members alone
 *     cannot answer it;
 *   * deleting asks for the password and states the two halves separately: access and secrets
 *     go at once, the data after the grace period;
 *   * an account with no address says so and asks for one. That is the founder's migrated
 *     login, and inventing an address for it would have been inventing data.
 */
export const dynamic = "force-dynamic";

export default async function OrganizationPage({
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
  const rawLink = parameters["link"];
  const link = typeof rawLink === "string" ? rawLink : undefined;

  const gate = await requireOperator(organizationHref(workspaceId));
  if (gate.kind === "unreachable") return <StudioUnreachable reason={gate.reason} />;

  const frame = await loadStudioFrame(gate.token, workspaceId);
  if (!frame.ok) return <StudioUnreachable reason={frame.reason} />;
  if (frame.value === null) return <StudioMissing what="workspace" />;

  const spaces = await getOrganizations(gate.token);
  const here = organizationHref(workspaceId);

  if (!spaces.ok) {
    return (
      <StudioShell
        active={{ kind: "organization" }}
        crumb={`${frame.value.workspace.name} / Przestrzeń`}
        frame={frame.value}
        info={info}
      >
        <h1 className="st__title">Przestrzeń</h1>
        <p className="st-tag" data-tone="absent">
          Nie udało się odczytać przestrzeni: {spaces.reason}
        </p>
      </StudioShell>
    );
  }

  const organizationId = spaces.value.current;
  const mine = spaces.value.organizations.find(
    (row) => row.organization_id === organizationId,
  );
  const owner = mine?.role === "OWNER";
  const members = owner ? await getMembers(gate.token, organizationId) : null;
  const invitations = owner ? await getInvitations(gate.token, organizationId) : null;
  const exports = owner ? await getExports(gate.token, organizationId) : null;

  return (
    <StudioShell
      active={{ kind: "organization" }}
      crumb={`${frame.value.workspace.name} / Przestrzeń`}
      frame={frame.value}
      info={info}
    >
      <h1 className="st__title">{mine?.name ?? "Przestrzeń"}</h1>
      <p className="st__lede">
        Kto tu pracuje, kogo zaprosiłeś i co możesz zrobić ze swoimi danymi. Wszystko bez pytania
        kogokolwiek o pomoc.
      </p>

      {spaces.value.legacy_operator ? (
        <section className="st__section" data-block="needs-email">
          <div className="st__section-head">To konto nie ma jeszcze adresu</div>
          <p className="st-note">
            Logujesz się hasłem operatora sprzed kont użytkowników. Twoje konto zostało
            przeniesione bez adresu, bo nikt go nigdy nie zapisał w bazie, a wpisanie zmyślonego
            byłoby wpisaniem nieprawdy. Podaj adres, a od następnego razu zalogujesz się nim.
          </p>
          <form action="/studio/organization" className="st-form" method="post">
            <input name="action" type="hidden" value="set_email" />
            <input name="workspace_id" type="hidden" value={workspaceId} />
            <input name="return_to" type="hidden" value={here} />
            <label className="st-field">
              <span>Twój adres e-mail</span>
              <input className="st-input" maxLength={320} name="email" required type="email" />
            </label>
            <button className="st-btn" data-kind="primary" type="submit">
              Zapisz adres
            </button>
          </form>
        </section>
      ) : null}

      {link !== undefined ? (
        <section className="st__section" data-block="invitation-link">
          <div className="st__section-head">Link z zaproszeniem</div>
          <p className="st-note">
            Pokazujemy go RAZ i nie wysyłamy mailem. Skopiuj i przekaż osobie, którą zapraszasz,
            kanałem, któremu ufasz. Link działa przez siedem dni i tylko jeden raz.
          </p>
          <p className="st-surface" data-invitation-link="1">
            <code>{link}</code>
          </p>
        </section>
      ) : null}

      <section className="st__section">
        <div className="st__section-head">
          Osoby
          {owner ? (
            <Pop label="Zaproś osobę" name="invite" side="right" title="Zaproś do przestrzeni">
              <form action="/studio/organization" className="st-form" method="post">
                <input name="action" type="hidden" value="invite" />
                <input name="workspace_id" type="hidden" value={workspaceId} />
                <input name="return_to" type="hidden" value={here} />
                <label className="st-field">
                  <span>Adres e-mail</span>
                  <input className="st-input" maxLength={320} name="email" required type="email" />
                </label>
                <p className="st-note">
                  Dostaniesz link do przekazania. Nie wysyłamy maili: w tym etapie produkt nie ma
                  skrzynki, a udawanie, że ma, kończy się zaproszeniami, które nie docierają.
                </p>
                <button className="st-btn" data-kind="primary" type="submit">
                  Utwórz zaproszenie
                </button>
              </form>
            </Pop>
          ) : null}
        </div>

        {!owner ? (
          <p className="st-note">
            Jesteś tu jako {roleLabel(mine?.role ?? "MEMBER")}: {roleNote(mine?.role ?? "MEMBER")}.
            Zaproszenia i ustawienia danych należą do właściciela przestrzeni.
          </p>
        ) : members === null || !members.ok ? (
          <p className="st-tag" data-tone="absent">
            Nie udało się odczytać osób{members === null ? "" : `: ${members.reason}`}
          </p>
        ) : (
          <div className="st-surface">
            {members.value.map((member) => (
              <div className="st-row" data-member={member.user_id} key={member.user_id}>
                <span aria-hidden="true" className="st-badge">
                  {(member.email ?? "??").slice(0, 2)}
                </span>
                <span className="st-row__body">
                  <strong>{member.email ?? "konto bez adresu (przeniesione)"}</strong>
                  <small>
                    {roleLabel(member.role)}, od {moment(member.joined_at)}
                    {member.revoked_at === null
                      ? ""
                      : `, dostęp odebrany ${moment(member.revoked_at)}`}
                  </small>
                </span>
                <span className="st-tag" data-tone={member.active ? "state" : "absent"}>
                  {member.active ? "ma dostęp" : "bez dostępu"}
                </span>
                {member.active && member.role !== "OWNER" ? (
                  <form action="/studio/organization" method="post">
                    <input name="action" type="hidden" value="revoke" />
                    <input name="workspace_id" type="hidden" value={workspaceId} />
                    <input name="user_id" type="hidden" value={member.user_id} />
                    <input name="return_to" type="hidden" value={here} />
                    <button className="st-btn" data-kind="quiet" type="submit">
                      Odbierz dostęp
                    </button>
                  </form>
                ) : null}
              </div>
            ))}
          </div>
        )}

        {invitations !== null && invitations.ok && invitations.value.length > 0 ? (
          <details className="st-note">
            <summary>Zaproszenia ({invitations.value.length})</summary>
            <ul>
              {invitations.value.map((invitation) => (
                <li key={invitation.invitation_id}>
                  {invitation.email},{" "}
                  {invitation.accepted_at !== null
                    ? `przyjęte ${moment(invitation.accepted_at)}`
                    : invitation.live
                      ? `ważne do ${moment(invitation.expires_at)}`
                      : "nieaktualne"}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </section>

      {owner ? (
        <section className="st__section" data-block="data">
          <div className="st__section-head">Twoje dane</div>
          <p className="st-note">
            Eksport to jeden plik JSON z tym, co tu wprowadziliście: projekty, profile, konta bez
            poświadczeń, materiały, ustalenia, sesje ze śladem i publikacje z widocznością.
            Przygotowuje go kolejka, plik żyje siedem dni.
          </p>
          <form action="/studio/organization" method="post">
            <input name="action" type="hidden" value="export" />
            <input name="workspace_id" type="hidden" value={workspaceId} />
            <input name="return_to" type="hidden" value={here} />
            <button className="st-btn" data-kind="primary" type="submit">
              Przygotuj eksport
            </button>
          </form>

          {exports !== null && exports.ok && exports.value.length > 0 ? (
            <div className="st-surface">
              {exports.value.map((row) => {
                const jobId = String(row["job_id"] ?? "");
                const status = String(row["status"] ?? "");
                return (
                  <div className="st-row" data-export={jobId} key={jobId}>
                    <span className="st-row__body">
                      <strong>{`eksport ${moment(String(row["requested_at"] ?? ""))}`}</strong>
                      <small>
                        {status}
                        {row["purge_after"] === null || row["purge_after"] === undefined
                          ? ""
                          : `, plik znika ${moment(String(row["purge_after"]))}`}
                      </small>
                      {row["detail"] === null || row["detail"] === undefined ? null : (
                        <small>{String(row["detail"])}</small>
                      )}
                    </span>
                    {status === "READY" ? (
                      <a
                        className="st-btn"
                        data-kind="quiet"
                        href={`/studio/organization/export?workspace_id=${encodeURIComponent(workspaceId)}&job_id=${encodeURIComponent(jobId)}`}
                      >
                        Pobierz
                      </a>
                    ) : (
                      <span className="st-tag" data-tone="absent">
                        jeszcze nie gotowy
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          ) : null}

          <Pop
            label="Usuń tę przestrzeń"
            name="delete-organization"
            side="right"
            title="Usuń przestrzeń"
          >
            <form action="/studio/organization" className="st-form" method="post">
              <input name="action" type="hidden" value="delete" />
              <input name="workspace_id" type="hidden" value={workspaceId} />
              <input name="return_to" type="hidden" value={here} />
              <p className="st-note">
                Dostęp i poświadczenia znikają NATYCHMIAST: nikt się już nie zaloguje, a hasła do
                platform i katalogi Codeksa zostają usunięte od razu. Same dane znikają po
                siedmiu dniach karencji. Do tego czasu można to cofnąć, ale wymaga to kontaktu
                z osobą prowadzącą to wdrożenie, bo logowanie jest już zablokowane.
              </p>
              <label className="st-field">
                <span>Potwierdź swoim hasłem</span>
                <input
                  autoComplete="current-password"
                  className="st-input"
                  name="password"
                  required
                  type="password"
                />
              </label>
              <button className="st-btn" data-kind="quiet" type="submit">
                Usuń przestrzeń
              </button>
            </form>
          </Pop>
        </section>
      ) : null}
    </StudioShell>
  );
}
