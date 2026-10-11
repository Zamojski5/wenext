import { FirstRun } from "../../../../../../../components/studio/first-run";
import { PlannedReturns } from "../../../../../../../components/studio/planned-returns";
import { Pop } from "../../../../../../../components/studio/pop";
import { ReturnsBlock } from "../../../../../../../components/studio/returns";
import { StudioShell } from "../../../../../../../components/studio/shell";
import { StudioMissing, StudioUnreachable } from "../../../../../../../components/studio/states";
import { getProject, getProjectReturn } from "../../../../../../../lib/control-plane";
import { projectConversations } from "../../../../../../../lib/conversations";
import { requireOperator } from "../../../../../../../lib/guard";
import {
  getDecisions,
  getPresence,
  getServiceStatus,
} from "../../../../../../../lib/presence-client";
import {
  DEFAULT_DELEGATION_EXPIRY_HOURS,
  DELEGATION_EXPIRY_CHOICES,
  accountCapabilityNote,
  collectorSentence,
  deadlineSentence,
  delegationHasExpired,
  reasonLabel,
  skipSentence,
  stateSentence,
  type Wakeup,
} from "../../../../../../../lib/presence-schema";
import { presenceHref, projectHref } from "../../../../../../../lib/routes";
import { loadStudioFrame } from "../../../../../../../lib/studio-frame";
import { moment, warsawMoment } from "../../../../../../../lib/studio-labels";

/**
 * `GET /w/<workspace>/p/<project>/presence` — "Obecność": what happened while nobody was here.
 *
 * THE ORDER IS THE ANSWER TO "DO I HAVE TO DO ANYTHING". One sentence about the state, then what
 * waits for a person, then what the agent has spent, then the log of wake-ups. Counters do not
 * open the screen, and neither does the log: somebody opening the laptop in the morning wants a
 * sentence, and the rows are for when the sentence is not enough.
 *
 * WHAT THIS SCREEN REFUSES TO IMPLY:
 *
 *   * a QUIET tick is not a missing tick. Wake-ups that did not run the model are listed with
 *     their reason, because a peaceful night and a stopped collector look identical without them;
 *   * a delegation that named no publication limit is NOT unlimited. The limits block shows the
 *     numbers that actually bind and marks the ones nobody chose, so the person can see the pace
 *     they did not set;
 *   * pausing is not revoking. Two buttons, two sentences, and the revoke asks again, because a
 *     pause comes back on the same signature and a revocation never comes back;
 *   * a proposal waiting for a person is not a failure of the agent. In REVIEW mode it is the
 *     whole workflow, and the block says which mode this project is in.
 *
 * TEXT WRITTEN BY THE AGENT IS DATA. Drafts in the decision queue are rendered as text; nothing
 * on this screen follows an instruction found inside one.
 *
 * `force-dynamic`, like the rest of the studio: a cached page would be a second opinion about
 * whether the agent is working right now.
 */
export const dynamic = "force-dynamic";

/** One wake-up. Rendered the same whether it ran the model or said why it did not. */
function WakeupRow({ wakeup }: { readonly wakeup: Wakeup }) {
  const ran = wakeup.session_id !== null;
  return (
    <li className="st-row" data-tone={ran ? "state" : "absent"}>
      <span className="st-row__body">
        <strong>{`${moment(wakeup.created_at)}, ${reasonLabel(wakeup.reason)}`}</strong>
        <small>{ran ? "uruchomił model" : skipSentence(wakeup)}</small>
        {wakeup.cost === null ? null : (
          <small>
            {Object.entries(wakeup.cost)
              .map(([key, value]) => `${key}: ${String(value)}`)
              .join(", ")}
          </small>
        )}
      </span>
    </li>
  );
}

export default async function PresencePage({
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

  const gate = await requireOperator(presenceHref(workspaceId, projectId));
  if (gate.kind === "unreachable") {
    return <StudioUnreachable reason={gate.reason} />;
  }

  const frame = await loadStudioFrame(gate.token, workspaceId);
  if (!frame.ok) return <StudioUnreachable reason={frame.reason} />;
  if (frame.value === null) return <StudioMissing what="workspace" />;

  const detail = await getProject(gate.token, workspaceId, projectId);
  if (!detail.ok) return <StudioUnreachable reason={detail.reason} />;
  if (detail.value === null) return <StudioMissing what="projekt" />;
  const project = detail.value.project;
  // Rozmowy TYLKO dla paska: ten ekran ich nie pokazuje, ale pasek ma trzymać
  // kontekst projektu, w którym stoimy (odbiór 1, różnica nr 5).
  const conversations = await projectConversations(gate.token, workspaceId, projectId);

  const accounts = detail.value.assignments.filter((one) => one.active);
  const presence = await getPresence(gate.token, workspaceId, projectId);
  const decisions = await getDecisions(gate.token, workspaceId, projectId);
  // P7: „agent czuwa" i „usługa działa" to dwa różne zdania. Projekt może czuwać, bo nic się nie
  // dzieje, albo dlatego, że kolektor nie żyje od czterech godzin; bez tego paska wyglądają
  // identycznie, a to jest właśnie ten ekran, na który ktoś wchodzi, gdy praca stanęła.
  const service = await getServiceStatus(gate.token, workspaceId);
  // P5: „co się wydarzyło od Twojej ostatniej wizyty" stało do 20.09 na WEJŚCIU do projektu i
  // razem z resztą bloków administracyjnych zamieniało je w raport (element E2). Sekcja nie
  // znika, tylko wraca tam, gdzie pytanie „czy muszę coś zrobić" już mieszka: pozycja
  // „Aktywność" w pasku. Odczyt jest projektowy, więc zakres się nie zmienia.
  const back = await getProjectReturn(gate.token, workspaceId, projectId);
  const here = presenceHref(workspaceId, projectId);

  if (!presence.ok) {
    return (
      <StudioShell
        active={{ kind: "presence", projectId }}
        conversations={conversations}
        crumb={`${frame.value.workspace.name} / ${project.name} / Obecność`}
        frame={frame.value}
        info={info}
        service={service}
      >
        <h1 className="st__title">Obecność</h1>
        {/* A broken read is NOT a quiet agent. The screen says which one this is. */}
        <p className="st-tag" data-tone="absent">
          Nie udało się odczytać stanu obecności: {presence.reason}
        </p>
      </StudioShell>
    );
  }

  const { state, delegation, budget, wakeups, planned, collector } = presence.value;
  const autonomous = delegation?.live === true && delegation.mode === "AUTONOMOUS";
  const deadline = delegation === null ? null : deadlineSentence(delegation);
  // AN EXPIRED DELEGATION IS NO DELEGATION IN FORCE (26.09): the screen offers signing again,
  // by conversation or by hand, exactly as for a project never entrusted. The backend closes the
  // expired signature inside the new one's transaction, so nothing has to be revoked first.
  const expired = delegationHasExpired(presence.value);
  // After a deadline the way back is the conversation, like the first signature: the agent
  // proposes a scope and the person signs it on its card.
  const signAgainHref = `${projectHref(workspaceId, projectId)}?prompt=${encodeURIComponent(
    "Powierzenie tego projektu wygasło. Pomóż mi ustalić nowy zakres pracy w tle: cel, " +
      "działania, częstotliwość, konta i termin. Najpierw ustalmy zakres w rozmowie.",
  )}`;
  const waiting = decisions.ok ? decisions.value : [];
  const ran = wakeups.filter((one) => one.session_id !== null);

  return (
    <StudioShell
      active={{ kind: "presence", projectId }}
      conversations={conversations}
      crumb={`${frame.value.workspace.name} / ${project.name} / Obecność`}
      frame={frame.value}
      info={info}
      service={service}
    >
      <h1 className="st__title">Obecność</h1>
      {/* The sentence FIRST. Everything below it is detail for when it is not enough. */}
      <p className="st__lede" data-state={state}>
        {stateSentence(presence.value)}{" "}
        <a href={projectHref(workspaceId, projectId)}>Wróć do projektu</a>
      </p>
      {/* DRUGIE ZDANIE, O KOLEKTORZE, i nie jest ozdobą. Zdanie wyżej mówi o agencie TEGO
          projektu; „czuwa" czyta się jak „nic się nie dzieje", a w tym samym czasie kolektor
          pobiera wątki dla całej przestrzeni. Brak pola (starszy control plane) znaczy „tego nie
          liczymy" i wtedy nie piszemy nic — zdanie o kolektorze bez pomiaru byłoby zgadywaniem. */}
      {collector === null ? null : (
        <p className="st-note" data-collector={collector.obserwowanych}>
          {collectorSentence(collector)}
        </p>
      )}

      {/* NAD LICZNIKAMI, jak na wejściu do projektu: człowiek po powrocie czyta najpierw, co się
          wydarzyło, a dopiero potem, ile tego było. */}
      <ReturnsBlock
        autonomous={autonomous}
        projectId={projectId}
        reason={back.ok ? undefined : back.reason}
        returnTo={here}
        view={back.ok && back.value !== null ? back.value : undefined}
        workspaceId={workspaceId}
      />

      {/* One string per counter rather than `text {value}`: React puts a comment separator
          between a text node and an expression, so `obudzenia: 3` would not be in the served
          HTML at all when written that way (the same trap the Materiały screen records). */}
      <div className="st__meta">
        <span className="st-tag" data-count="wakeups" data-tone="state">
          {`obudzenia: ${wakeups.length}`}
        </span>
        <span className="st-tag" data-count="ran" data-tone="state">
          {`z sesją modelu: ${ran.length}`}
        </span>
        <span className="st-tag" data-count="waiting" data-tone={waiting.length > 0 ? "warn" : "state"}>
          {`${autonomous ? "przygotowane szkice" : "czeka na Ciebie"}: ${waiting.length}`}
        </span>
      </div>

      {delegation === null || expired ? (
        <section className="st-block" data-block="sign" data-after-expiry={expired ? "tak" : undefined}>
          <h2 className="st-block__title">
            {expired ? "Powierz projekt ponownie" : "Powierz projekt agentowi"}
          </h2>
          <p className="st-note">
            {expired
              ? "Poprzednie powierzenie wygasło. Ustal w rozmowie nowy zakres i termin albo ustaw je ręcznie; nowy podpis zamyka poprzedni."
              : "Ustal w rozmowie, jaki cel agent ma realizować, kiedy wracać i z jakich kont korzystać."}
          </p>
          <a className="st-btn" data-kind="primary" href={expired ? signAgainHref : `${projectHref(workspaceId, projectId)}?prompt=${encodeURIComponent("Pomóż mi ustawić pracę projektu w tle. Na podstawie naszych ustaleń zaproponuj cel, działania, częstotliwość i potrzebne konta. Najpierw ustalmy zakres w rozmowie.")}`}>
            Ustal w rozmowie
          </a>
          <details className="st__section">
            <summary>Ustaw ręcznie</summary>
            {/* The first-run guide belongs to the first signature, not to the one after expiry. */}
            {expired ? null : <FirstRun step={5} />}
          <form action="/studio/presence" method="post">
            <input name="action" type="hidden" value="sign" />
            <input name="workspace_id" type="hidden" value={workspaceId} />
            <input name="project_id" type="hidden" value={projectId} />
            <input name="return_to" type="hidden" value={here} />

            <label className="st-field">
              <span>Tryb</span>
              <select name="mode">
                <option value="REVIEW">REVIEW: każdy tekst pokazuje mi do zatwierdzenia</option>
                <option value="AUTONOMOUS">AUTONOMOUS: publikuje sam w tym zakresie</option>
                <option value="RESEARCH">RESEARCH: tylko czyta i zapamiętuje</option>
              </select>
            </label>

            {/* THE DEADLINE IS PART OF THE SIGNATURE (26.09), so it stands with the mode and has
                no "bez terminu" option: after it passes the agent stops, and a new scope has to be
                signed. The handler accepts only these values, from the same list. */}
            <label className="st-field">
              <span>Ważne przez</span>
              <select
                defaultValue={String(DEFAULT_DELEGATION_EXPIRY_HOURS)}
                name="expires_in_hours"
                required
              >
                {DELEGATION_EXPIRY_CHOICES.map((choice) => (
                  <option key={choice.hours} value={String(choice.hours)}>
                    {choice.label}
                  </option>
                ))}
              </select>
            </label>

            <fieldset className="st-field">
              <legend>Konta, z których wolno pisać</legend>
              {accounts.length === 0 ? (
                <p className="st-note">
                  Ten projekt nie ma przypisanych kont. Bez konta agent nie opublikuje niczego, a
                  powierzenie i tak można podpisać: będzie czytał i przygotowywał.
                </p>
              ) : (
                accounts.map((one) => (
                  <label className="st-check" key={one.social_account_id}>
                    <input name="accounts" type="checkbox" value={one.social_account_id} />
                    <span>{`${one.external_account_id} (${one.platform})`}</span>
                    {/* P6: what this account can ACTUALLY do, before the signature rather than
                        after it. A person may tick REPLY for an X account in good faith; no
                        adapter here can carry that out, and the delegated decision would refuse
                        it later with a sentence naming the platform. Saying it here is what
                        keeps the signature meaningful. */}
                    <small>{accountCapabilityNote(one.support)}</small>
                  </label>
                ))
              )}
            </fieldset>

            <label className="st-field">
              <span>Społeczności, po przecinku</span>
              <input name="communities" placeholder="r/kubernetes, r/devops" type="text" />
            </label>

            <fieldset className="st-field">
              <legend>Działania</legend>
              <label className="st-check">
                <input name="actions" type="checkbox" value="REPLY" />
                <span>REPLY: odpowiedzi pod cudzymi wątkami</span>
              </label>
              <label className="st-check">
                <input name="actions" type="checkbox" value="OWN_POST" />
                <span>OWN_POST: własne posty</span>
              </label>
              <label className="st-check">
                <input name="actions" type="checkbox" value="RESEARCH" />
                <span>RESEARCH: czytanie i zapamiętywanie</span>
              </label>
            </fieldset>

            {/* Blank is NOT zero. Left empty, the limit becomes the conservative default, and the
                panel above shows which numbers nobody chose. Zero here would mean "nigdy". */}
            <label className="st-field">
              <span>Publikacje na dobę (puste: domyślne)</span>
              <input min="0" name="publications_per_day" type="number" />
            </label>
            <label className="st-field">
              <span>Publikacje na godzinę (puste: domyślne)</span>
              <input min="0" name="publications_per_hour" type="number" />
            </label>
            {/* Własne posty mają swój sufit (0023). Pole jest tu, bo panel wyżej mówi, że ten
                limit jest domyślny, a limit nazwany domyślnym bez miejsca na wybór to zdanie,
                którego nie da się odwrócić. */}
            <label className="st-field">
              <span>Własne posty na dobę (puste: domyślne)</span>
              <input min="0" name="own_posts_per_day" type="number" />
            </label>
            <label className="st-field">
              <span>Tury modelu na dobę (puste: bez limitu)</span>
              <input min="0" name="model_turns_per_day" type="number" />
            </label>

            <button className="st-btn" data-kind="primary" type="submit">
              Podpisuję powierzenie
            </button>
          </form>
          </details>
        </section>
      ) : null}

      {/* The expired signature stays visible BELOW the offer to sign again: what ran out, and
          until when, is what the person weighs while choosing the next scope. */}
      {delegation === null ? null : (
        <section className="st-block" data-block="delegation">
          <h2 className="st-block__title">Powierzenie</h2>
          {/* One string, deadline included: React separates a text node from an expression with
              a comment, and a sentence split that way is not in the served HTML as one piece. */}
          {/* Both moments on the reader's wall clock (27.09): the deadline already was, and a
              signature shown in UTC beside it read as signed two hours before the person did. */}
          <p className="st-note" data-deadline={delegation.expires_at ?? undefined}>
            {`Tryb ${delegation.mode}, stan: ${delegation.state}. Podpisano ${warsawMoment(
              delegation.signed_at,
            )}.${deadline === null ? "" : ` ${deadline}`}`}
          </p>
          {/* THE DIGEST IS ON THE SCREEN, not only in the database. It is what a decision cites
              as "this is what was agreed to", so a person has to be able to see that the
              signature they are looking at is the one the publications refer to, and that
              wstrzymanie and wznowienie did not quietly produce a different one. */}
          <p className="st-note" data-digest={delegation.scope_sha256}>
            <small>{`odcisk podpisanego zakresu: ${delegation.scope_sha256}`}</small>
          </p>
          <ul className="st__list">
            <li className="st-row">
              <span className="st-row__body">
                <strong>Zakres</strong>
                <small>{`konta: ${delegation.accounts.length}, społeczności: ${
                  delegation.communities.join(", ") || "żadne"
                }`}</small>
                <small>{`działania: ${delegation.actions.join(", ") || "żadne"}`}</small>
              </span>
            </li>
            <li className="st-row">
              <span className="st-row__body">
                <strong>Limity, które obowiązują</strong>
                {/* Both numbers, always. A limit nobody chose is still a limit, and hiding it
                    would let somebody sign a scope whose real pace is invisible to them. */}
                <small>
                  {`publikacje: ${delegation.limits.publikacje_godzina}/h, ${delegation.limits.publikacje_doba}/dobę`}
                </small>
                {/* Własne posty mają SWÓJ sufit (0023) i on wiąże niezależnie od publikacji:
                    limit publikacji może być jeszcze daleki, a własnych postów już nie ma. */}
                <small>{`własne posty: ${delegation.limits.wlasne_posty_doba}/dobę`}</small>
                <small>
                  {delegation.limits.tury_modelu_doba === null
                    ? "tury modelu: bez limitu"
                    : `tury modelu: ${delegation.limits.tury_modelu_doba}/dobę`}
                </small>
                {delegation.limits.domyslne.length === 0 ? null : (
                  <small data-tone="warn">
                    {`domyślne, bo nie wpisano ich w podpisie: ${delegation.limits.domyslne.join(
                      ", ",
                    )}`}
                  </small>
                )}
              </span>
            </li>
          </ul>

          <div className="st__actions">
            {/* AN EXPIRED SIGNATURE DOES NOT COME BACK BY RESUMING IT (26.09). "Wznów" here would
                promise that one click restores the work, and the deadline is exactly what must not
                be extended by a click: the way back is the new signature offered above. "Odwołaj"
                stays, as agreed with the backend. */}
            {expired ? null : delegation.live ? (
              <form action="/studio/presence" method="post">
                <input name="action" type="hidden" value="pause" />
                <input name="workspace_id" type="hidden" value={workspaceId} />
                <input name="project_id" type="hidden" value={projectId} />
                <input name="return_to" type="hidden" value={here} />
                <button className="st-btn" data-kind="secondary" type="submit">
                  Wstrzymaj
                </button>
              </form>
            ) : (
              <form action="/studio/presence" method="post">
                <input name="action" type="hidden" value="resume" />
                <input name="workspace_id" type="hidden" value={workspaceId} />
                <input name="project_id" type="hidden" value={projectId} />
                <input name="return_to" type="hidden" value={here} />
                <button className="st-btn" data-kind="primary" type="submit">
                  Wznów
                </button>
              </form>
            )}
            {/* Revoking asks again. A pause comes back on the same signature; this does not. */}
            <Pop
              label="Odwołaj powierzenie"
              name="revoke"
              text="Odwołaj powierzenie"
              title="Odwołanie jest ostateczne"
              variant="button"
            >
              <form action="/studio/presence" method="post">
                <input name="action" type="hidden" value="revoke" />
                <input name="workspace_id" type="hidden" value={workspaceId} />
                <input name="project_id" type="hidden" value={projectId} />
                <input name="return_to" type="hidden" value={here} />
                <p className="st-note">
                  Odwołanie jest ostateczne. Agent przestaje podejmować pracę, a propozycje, które
                  już czekają, przestają być wydawane. Żeby wrócić, trzeba podpisać nowy zakres.
                </p>
                <button className="st-btn" data-kind="danger" type="submit">
                  Odwołuję
                </button>
              </form>
            </Pop>
          </div>
        </section>
      )}

      {budget === null ? null : (
        <section className="st-block" data-block="budget">
          <h2 className="st-block__title">Co wydał</h2>
          <p className="st-note">
            {`tury modelu: ${budget.tury_modelu_doba}${
              budget.limit_tur_modelu === null ? "" : ` z ${budget.limit_tur_modelu}`
            }, publikacje: ${budget.publikacje_doba} z ${budget.limit_publikacji_doba} w dobie, ${
              budget.publikacje_godzina
            } z ${budget.limit_publikacji_godzina} w godzinie.`}
          </p>
          {budget.wyczerpany === null ? null : (
            /* WHICH limit, not "budget exhausted": the first tells a person when to come back. */
            <p className="st-tag" data-tone="warn">
              {`Budżet wyczerpany, ${budget.wyczerpany}.`}
            </p>
          )}
        </section>
      )}

      <section className="st-block" data-block="decisions">
        <h2 className="st-block__title">{autonomous ? "Przygotowane szkice" : "Czeka na Twoją decyzję"}</h2>
        {autonomous ? <p className="st-note">Szkic nie oznacza prośby o akceptację. Agent może go wydać w powierzonym zakresie, gdy pozwolą na to limity.</p> : null}
        {!decisions.ok ? (
          <p className="st-tag" data-tone="absent">
            Nie udało się odczytać kolejki decyzji: {decisions.reason}
          </p>
        ) : waiting.length === 0 ? (
          <p className="st-note">
            {autonomous ? "Nie ma przygotowanych szkiców." : "Nie ma propozycji czekających na Twoją decyzję."}
          </p>
        ) : (
          <ul className="st__list">
            {waiting.map((one) => (
              <li className="st-row" data-tone="warn" key={one.revision_id}>
                <span className="st-row__body">
                  <strong>{`${one.account}, cel ${one.target_external_id}`}</strong>
                  {/* Agent text, rendered as text. */}
                  <small>{one.text}</small>
                  <small>{`przygotowane ${moment(one.created_at)}, zgoda ważna do ${moment(
                    one.expires_at,
                  )}`}</small>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <PlannedReturns
        planned={planned}
        workspaceId={workspaceId}
        projectId={projectId}
        here={here}
      />

      <section className="st-block" data-block="wakeups">
        <h2 className="st-block__title">Obudzenia</h2>
        {wakeups.length === 0 ? (
          <p className="st-note">
            W tym projekcie nie uruchomiono jeszcze pracy w tle. Odczyty społeczności pokazujemy wyżej.
          </p>
        ) : (
          <ul className="st__list">
            {wakeups.map((one) => (
              <WakeupRow key={one.wakeup_id} wakeup={one} />
            ))}
          </ul>
        )}
      </section>
    </StudioShell>
  );
}
