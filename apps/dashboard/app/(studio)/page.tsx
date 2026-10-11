import { redirect } from "next/navigation";

import { getWorkspaces } from "../../lib/control-plane";
import { requireOperator } from "../../lib/guard";
import { workspaceHref } from "../../lib/routes";
import { infoFor } from "../../lib/studio-info";

/**
 * `GET /` — the front door. It picks a workspace, or asks for the first one.
 *
 * THREE OUTCOMES, AND NONE MAY IMPERSONATE ANOTHER:
 *
 *   | outcome                    | what happens                                            |
 *   | -------------------------- | ------------------------------------------------------- |
 *   | no session                 | the guard redirects to `/login`                          |
 *   | control plane unreachable  | a sentence with the reason. NOT a login prompt, NOT an   |
 *   |                            | invitation to create a workspace                         |
 *   | the read succeeded         | redirect into the first workspace, or the empty state    |
 *
 * The middle row is the one this file exists to get right. "No workspaces" and "we could not ask"
 * look identical on a screen that renders a create form for both, and the user would be invited
 * to create a second workspace on top of the one they already have.
 *
 * The redirect goes to the FIRST workspace by creation time, which is the control plane's order.
 * There is no "last used workspace" and deliberately so: it would be a piece of state nobody can
 * link to, and the URL is what makes a workspace shareable and bookmarkable.
 */
export const dynamic = "force-dynamic";

export default async function StudioEntryPage({
  searchParams,
}: {
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const parameters = await searchParams;
  const rawInfo = parameters["info"];
  const message = infoFor(typeof rawInfo === "string" ? rawInfo : undefined);

  const gate = await requireOperator();
  if (gate.kind === "unreachable") {
    return (
      <main className="st" data-st-route="entry">
        <section className="st__main">
          <div className="st__content">
            <h1 className="st__title">Brak odpowiedzi</h1>
            <p className="st__lede">
              Nie udało się odczytać workspace&apos;ów z control plane, więc ten ekran nie wie,
              czy jakieś istnieją. Powód: {gate.reason}
            </p>
          </div>
        </section>
      </main>
    );
  }

  const workspaces = await getWorkspaces(gate.token);
  if (!workspaces.ok) {
    return (
      <main className="st" data-st-route="entry">
        <section className="st__main">
          <div className="st__content">
            <h1 className="st__title">Brak odpowiedzi</h1>
            <p className="st__lede">
              Odczyt listy workspace&apos;ów się nie udał, więc nie wiadomo, czy jakieś są.
              Powód: {workspaces.reason}
            </p>
          </div>
        </section>
      </main>
    );
  }

  const first = workspaces.value.workspaces[0];
  if (first !== undefined) {
    redirect(workspaceHref(first.workspace_id));
  }

  // A MEASURED empty: the read succeeded and this organization has no workspace yet.
  return (
    <main className="st" data-st-route="entry">
      <section className="st__main">
        <div className="st__content">
          <h1 className="st__title">Zacznij od własnej przestrzeni</h1>
          <p className="st__lede">
            Workspace trzyma projekty, profile osób, konta platform i wiedzę. Dopiero w nim
            zakładasz projekt i powierzasz agentowi cel.
          </p>
          {/* P7: onboarding, and it is four sentences rather than demo data. A new space seeded
              with examples looks busy and teaches nothing about what is real; these are the four
              steps in the order they have to happen, each naming the screen it lives on. */}
          <ol className="st-note" data-block="onboarding">
            <li>
              <strong>Przestrzeń robocza</strong>: tu, poniżej. Jedna wystarczy na start.
            </li>
            <li>
              <strong>Projekt</strong>: cel, który powierzasz agentowi, i tryb pracy. Zakłada się
              go w przestrzeni, zaraz po jej utworzeniu.
            </li>
            <li>
              <strong>Profil osoby i konto platformy</strong>: pod jaką tożsamością agent się
              odzywa. Ekran „Profile i konta".
            </li>
            <li>
              <strong>Połączenie Codex</strong>: dostęp do modelu, który wykonuje pracę. Ekran
              „Połączenia Codex". Bez niego sesja nie ruszy i produkt powie to wprost.
            </li>
          </ol>
          <p className="st-note">
            Nie zakładamy niczego za Ciebie i nie wsypujemy danych na próbę: pusta przestrzeń,
            którą sam wypełnisz, jest czytelniejsza niż przykłady, których nie da się odróżnić od
            własnych.
          </p>
          {message !== null ? (
            <p className="st-info" data-tone={message.tone} role="status">
              {message.text}
            </p>
          ) : null}
          <div className="st__section">
            <form action="/studio/workspace" className="st-form" method="post">
              <input name="return_to" type="hidden" value="/" />
              <label className="st-field">
                <span>Nazwa przestrzeni</span>
                <input
                  className="st-input"
                  maxLength={120}
                  name="name"
                  placeholder="np. Studio"
                  required
                  type="text"
                />
              </label>
              <div>
                <button className="st-btn" data-kind="primary" type="submit">
                  Utwórz workspace
                </button>
              </div>
            </form>
          </div>
        </div>
      </section>
    </main>
  );
}
