import { FirstRun } from "../../../../../components/studio/first-run";
import { Pop } from "../../../../../components/studio/pop";
import { StudioShell } from "../../../../../components/studio/shell";
import { StudioMissing, StudioUnreachable } from "../../../../../components/studio/states";
import { getCodexConnections } from "../../../../../lib/control-plane";
import { requireOperator } from "../../../../../lib/guard";
import { codexHref } from "../../../../../lib/routes";
import { loadStudioFrame } from "../../../../../lib/studio-frame";
import { moment } from "../../../../../lib/studio-labels";
import type { CodexConnection } from "../../../../../lib/codex-schema";

/**
 * `GET /w/<workspace>/codex` — "Połączenia Codex": access to the ENGINE.
 *
 * NOT a social account, and the screen keeps saying so. ZAKRES-FUNKCJONALNY §2 names this as the
 * first distinction the interface may not blur, because mixing them costs the user money or a
 * public post under the wrong identity. So this screen mentions no platform account, and the
 * "Profile i konta" screen mentions no connection.
 *
 * WHAT IT REFUSES TO SHOW:
 *
 *   * **a zero instead of "nie wiemy".** §3E: "Jeżeli limitów nie da się odczytać, interfejs
 *     mówi wprost, że nie wie. Nie pokazuje zera, nie pokazuje szacunku, nie ukrywa pola." Both
 *     windows are listed even when the engine reported neither, each with the reason;
 *   * **a model catalogue that stopped early, presented as the whole list.** `complete: false`
 *     is printed, because routing on an incomplete catalogue is forbidden (L2189);
 *   * **the configured `CODEX_HOME` as an existing connection.** It is reported as adoptable
 *     with a button, because a row nobody created would claim somebody registered it.
 *
 * `has_auth` is labelled as a FILE on disk, not as a session: it is the cheap check made without
 * starting an app-server, and an `auth.json` can hold an expired login.
 */
export const dynamic = "force-dynamic";

const STATUS_LABELS: Readonly<Record<string, string>> = {
  PENDING_LOGIN: "czeka na zalogowanie",
  CONNECTED: "połączone",
  RATE_LIMITED: "limit wyczerpany",
  DISCONNECTED: "odłączone",
  ERROR: "błąd",
};

function statusTone(status: string): string {
  if (status === "CONNECTED") return "state";
  if (status === "ERROR") return "danger";
  return "absent";
}

function Limits({ connection }: { readonly connection: CodexConnection }) {
  const { limits } = connection;
  if (!connection.engine_read.enabled) {
    // Not "we tried and failed": we decided not to open an app-server on this directory, and the
    // sentence says which directory and why.
    return (
      <p className="st-note" data-tone="absent">
        {connection.engine_read.reason}
      </p>
    );
  }
  if (!limits.read) {
    return (
      <p className="st-note" data-tone="absent">
        Limity: nie odczytano. {limits.reason ?? "Powód nieznany."}
      </p>
    );
  }
  return (
    <div className="st__meta">
      {limits.windows.map((window) => (
        <span
          className="st-tag"
          data-tone={window.present ? undefined : "absent"}
          key={window.name}
        >
          {window.name === "primary" ? "okno główne" : "okno dodatkowe"}:{" "}
          {window.present && window.used_percent !== null
            ? `${window.used_percent}% zużyte`
            : "brak danych"}
          {window.resets_at === null
            ? ", reset: nie podano"
            : `, reset: ${moment(window.resets_at)}`}
        </span>
      ))}
      <span className="st-note">Odczytano: {moment(limits.at)}</span>
    </div>
  );
}

export default async function CodexConnectionsPage({
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

  const gate = await requireOperator(codexHref(workspaceId));
  if (gate.kind === "unreachable") return <StudioUnreachable reason={gate.reason} />;

  const frame = await loadStudioFrame(gate.token, workspaceId);
  if (!frame.ok) return <StudioUnreachable reason={frame.reason} />;
  if (frame.value === null) return <StudioMissing what="workspace" />;

  const view = await getCodexConnections(gate.token);
  const here = codexHref(workspaceId);

  return (
    <StudioShell
      active={{ kind: "codex" }}
      crumb={`${frame.value.workspace.name} / Połączenia Codex`}
      frame={frame.value}
      info={info}
    >
      <h1 className="st__title">Połączenia Codex</h1>
      <p className="st__lede">
        Połączenie Codex to dostęp do silnika, który wykonuje pracę agentów. To nie jest konto
        społecznościowe: konta, pod którymi agent pisze publicznie, są w widoku Profile i konta.
      </p>

      {!view.ok ? (
        <p className="st-note" data-tone="absent">
          Nie odczytano połączeń. {view.reason}
        </p>
      ) : (
        <>
          <section className="st__section">
            <div className="st__section-head">
              Połączenia
              <Pop
                label="Dodaj połączenie Codex"
                name="new-codex"
                side="right"
                title="Nowe połączenie"
              >
                {!view.value.root.configured ? (
                  <p className="st-note" data-tone="absent">
                    To wdrożenie nie ma skonfigurowanego katalogu Codeksa, więc nowe połączenie
                    nie miałoby gdzie trzymać swoich danych.
                  </p>
                ) : (
                  <form action="/studio/codex" className="st-form" method="post">
                    <input name="action" type="hidden" value="create" />
                    <input name="return_to" type="hidden" value={here} />
                    <label className="st-field">
                      <span>Nazwa połączenia</span>
                      <input
                        className="st-input"
                        maxLength={120}
                        name="label"
                        placeholder="np. Konto Jana"
                        required
                        type="text"
                      />
                    </label>
                    <label className="st-field">
                      <span>Zakres</span>
                      <select className="st-select" defaultValue="" name="workspace_id">
                        <option value="">cała organizacja</option>
                        <option value={workspaceId}>tylko ten workspace</option>
                      </select>
                    </label>
                    <p className="st-note">
                      Po utworzeniu app-server wyda kod device-code. Trzeba go przepisać na
                      wskazanej stronie w ciągu piętnastu minut.
                    </p>
                    <button className="st-btn" data-kind="primary" type="submit">
                      Utwórz i zacznij logowanie
                    </button>
                  </form>
                )}
              </Pop>
            </div>

            {view.value.connections.length === 0 ? (
              <>
                <p className="st-empty">
                  Nie ma jeszcze żadnego połączenia. Odczyt się udał, po prostu nic tu nie
                  podłączono.
                </p>
                <FirstRun step={4} />
              </>
            ) : (
              <div className="st-surface">
                {view.value.connections.map((connection) => (
                  <div
                    className="st-row"
                    data-connection={connection.connection_id}
                    key={connection.connection_id}
                  >
                    <span className="st-row__body">
                      <strong>{connection.label}</strong>
                      <small>
                        {connection.scope === "organization"
                          ? "dostępne dla całej organizacji"
                          : "tylko dla jednego workspace'u"}
                        , katalog: {connection.home_ref === "." ? "korzeń wolumenu" : connection.home_ref}
                        {/* `email: null` is legal WITH a logged-in account in the pinned
                            contract, so the absence is worded, never read as "not logged in". */}
                        , konto:{" "}
                        {connection.account_email ?? "e-mail nieujawniony"}
                        {connection.plan_type === null ? "" : `, plan: ${connection.plan_type}`}
                        , ostatni odczyt: {moment(connection.last_read_at)}
                      </small>

                      {connection.login !== null ? (
                        <small>
                          Otwórz {connection.login.verification_url} i wpisz kod{" "}
                          <strong>{connection.login.user_code}</strong>
                          {connection.login.deadline_at === null
                            ? ""
                            : `. Ważny do ${moment(connection.login.deadline_at)}`}
                        </small>
                      ) : null}

                      {connection.last_error !== null ? (
                        <small>Błąd: {connection.last_error}</small>
                      ) : null}

                      {!connection.engine_read.enabled ? null : connection.models.read ? (
                        <small>
                          Modele: {connection.models.count}
                          {connection.models.complete
                            ? ""
                            : " (katalog niekompletny, routing nie ma na czym się oprzeć)"}
                          {connection.models.names.length === 0
                            ? ""
                            : `, np. ${connection.models.names.slice(0, 3).join(", ")}`}
                        </small>
                      ) : (
                        <small>Modele: nie odczytano.</small>
                      )}

                      <Limits connection={connection} />
                    </span>

                    {/* A connection we never open has no measured state, so the tag says that
                        instead of the stored status. "czeka na zalogowanie" next to an account
                        that is signed in and running the pilot would be the wrong sentence. */}
                    <span
                      className="st-tag"
                      data-tone={
                        connection.engine_read.enabled ? statusTone(connection.status) : "absent"
                      }
                    >
                      {connection.engine_read.enabled
                        ? (STATUS_LABELS[connection.status] ?? connection.status)
                        : "odczyt wyłączony"}
                    </span>

                    {connection.active && connection.engine_read.enabled ? (
                      <>
                        <form action="/studio/codex" method="post">
                          <input name="action" type="hidden" value="refresh" />
                          <input
                            name="connection_id"
                            type="hidden"
                            value={connection.connection_id}
                          />
                          <input name="return_to" type="hidden" value={here} />
                          <button className="st-btn" data-kind="quiet" type="submit">
                            Odczytaj stan
                          </button>
                        </form>
                        {connection.status === "CONNECTED" ? null : (
                          <form action="/studio/codex" method="post">
                            <input name="action" type="hidden" value="login" />
                            <input
                              name="connection_id"
                              type="hidden"
                              value={connection.connection_id}
                            />
                            <input name="return_to" type="hidden" value={here} />
                            <button className="st-btn" data-kind="quiet" type="submit">
                              Zaloguj ponownie
                            </button>
                          </form>
                        )}
                        <form action="/studio/codex" method="post">
                          <input name="action" type="hidden" value="disconnect" />
                          <input
                            name="connection_id"
                            type="hidden"
                            value={connection.connection_id}
                          />
                          <input name="return_to" type="hidden" value={here} />
                          <button className="st-btn" data-kind="quiet" type="submit">
                            Odłącz
                          </button>
                        </form>
                      </>
                    ) : connection.active ? (
                      // Active, but we do not open it. Only the disconnect stays: it changes a
                      // row, not the directory.
                      <form action="/studio/codex" method="post">
                        <input name="action" type="hidden" value="disconnect" />
                        <input
                          name="connection_id"
                          type="hidden"
                          value={connection.connection_id}
                        />
                        <input name="return_to" type="hidden" value={here} />
                        <button className="st-btn" data-kind="quiet" type="submit">
                          Odłącz
                        </button>
                      </form>
                    ) : (
                      <span className="st-note">
                        odłączone {moment(connection.disconnected_at)}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="st__section">
            <div className="st__section-head">Katalog z konfiguracji</div>
            {!view.value.root.configured ? (
              <p className="st-note" data-tone="absent">
                To wdrożenie nie ma zmiennej WENEXT_CODEX_HOME, więc nie ma katalogu, w którym
                mogłyby mieszkać połączenia. To błąd konfiguracji, nie brak połączeń.
              </p>
            ) : view.value.root.adopted ? (
              <p className="st-note">
                Katalog {view.value.root.path} jest już przyjęty jako połączenie powyżej.
              </p>
            ) : (
              <>
                <p className="st-note">
                  Wdrożenie używa katalogu {view.value.root.path}. Plik auth.json{" "}
                  {view.value.root.has_auth ? "w nim jest" : "go nie ma"}; to fakt o pliku, nie o
                  ważności sesji. Połączenie nie powstaje samo, bo wiersz utworzony automatycznie
                  twierdziłby, że ktoś je zarejestrował.
                </p>
                <form action="/studio/codex" className="st-form" method="post">
                  <input name="action" type="hidden" value="adopt_root" />
                  <input name="return_to" type="hidden" value={here} />
                  <label className="st-field">
                    <span>Nazwa</span>
                    <input
                      className="st-input"
                      defaultValue="Konto z konfiguracji"
                      maxLength={120}
                      name="label"
                      required
                      type="text"
                    />
                  </label>
                  <div>
                    <button className="st-btn" data-kind="primary" type="submit">
                      Przyjmij jako połączenie
                    </button>
                  </div>
                </form>
              </>
            )}
          </section>

          <p className="st-note">
            Odłączenie zatrzymuje korzystanie z połączenia w tym produkcie. Nie wylogowuje konta i
            nie kasuje jego katalogu ani historii.
          </p>
        </>
      )}
    </StudioShell>
  );
}
