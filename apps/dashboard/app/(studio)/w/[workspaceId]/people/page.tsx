import { FirstRun } from "../../../../../components/studio/first-run";
import { Pop } from "../../../../../components/studio/pop";
import { StudioShell } from "../../../../../components/studio/shell";
import { VoicePanel } from "../../../../../components/studio/voice-panel";
import { StudioMissing, StudioUnreachable } from "../../../../../components/studio/states";
import {
  getPeople,
  getPersonVoice,
  type PersonVoiceView,
} from "../../../../../lib/control-plane";
import { requireOperator } from "../../../../../lib/guard";
import {
  AUDIENCE_SUM_IS_NOT_PEOPLE,
  audienceNote,
} from "../../../../../lib/presence-schema";
import { peopleHref } from "../../../../../lib/routes";
import { loadStudioFrame } from "../../../../../lib/studio-frame";
import { kindLabel, moment } from "../../../../../lib/studio-labels";

/**
 * `GET /w/<workspace>/people` — "Profile i konta": who the agent represents and with what.
 *
 * THE TWO DISTINCTIONS ZAKRES-FUNKCJONALNY §2 SAYS THE INTERFACE MAY NOT BLUR, AND HOW THIS
 * SCREEN KEEPS THEM:
 *
 *   1. **A social account is not an engine connection.** They are on different screens, reached
 *      by different sidebar entries, and neither one mentions the other. Confusing them costs
 *      the user real money or a public post under the wrong identity.
 *   2. **An account's followers are not a community's members.** Neither number is shown here,
 *      and that is correct rather than missing: nothing in this product reads follower counts on
 *      any platform yet (`audience_metrics` is false across the matrix), and §6 rule 2 says
 *      missing data is shown as missing, never as a zero.
 *
 * THE VOICE PANEL (0022) IS WHERE A PERSON IS DESCRIBED, AND IT IS THE SAME FOUR QUESTIONS THE
 * pilot answered for the founder: what they know deeply, what they hold, what they will never
 * claim, how they sound. It is a panel and not a page because it belongs to the profile it
 * describes, and it shows the HISTORY because a voice version is the evidence of how a comment
 * from last week came to sound the way it does.
 *
 * THE PILOT'S CONTENT IS OFFERED, NEVER COPIED. When a profile has no voice yet, has an account
 * attached and the workspace has a pilot profile, the panel shows that content in a second form
 * with its date, and the person presses "Przejmij". Nothing is written until they do: the pilot
 * profile describes a specific human being, and only that human can say it is also the voice of
 * this profile.
 *
 * DETACHED ACCOUNTS STAY VISIBLE, under the profile they used to belong to. P1 says detaching
 * must not delete history, and a history nothing reads is not a history — it is also how the user
 * finds an account again in order to move it to another profile.
 */
export const dynamic = "force-dynamic";

export default async function PeoplePage({
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

  const gate = await requireOperator(peopleHref(workspaceId));
  if (gate.kind === "unreachable") return <StudioUnreachable reason={gate.reason} />;

  const frame = await loadStudioFrame(gate.token, workspaceId);
  if (!frame.ok) return <StudioUnreachable reason={frame.reason} />;
  if (frame.value === null) return <StudioMissing what="workspace" />;

  const people = await getPeople(gate.token, workspaceId);
  if (!people.ok) return <StudioUnreachable reason={people.reason} />;
  if (people.value === null) return <StudioMissing what="workspace" />;

  const { profiles, attachments, assignments, accounts, projects, platforms, audience } =
    people.value;
  const here = peopleHref(workspaceId);

  // Jedno zapytanie o głos na osobę, wszystkie równolegle. Osób w przestrzeni są jednostki,
  // a zbiorcza trasa byłaby drugim miejscem z regułą zakresu osoby, czyli drugim miejscem,
  // w którym można ją pomylić. Nieudany odczyt głosu NIE przewraca całego ekranu: reszta
  // widoku jest poprawna, a przy tym profilu staje zdanie, że głosu nie udało się przeczytać.
  const voiceReads = await Promise.all(
    profiles.map((profile) => getPersonVoice(gate.token, workspaceId, profile.person_profile_id)),
  );
  const voices = new Map<string, PersonVoiceView>();
  const voiceFailures = new Map<string, string>();
  profiles.forEach((profile, index) => {
    const read = voiceReads[index]!;
    if (!read.ok) voiceFailures.set(profile.person_profile_id, read.reason);
    else if (read.value !== null) voices.set(profile.person_profile_id, read.value);
  });

  const voiceStateOf = (profileId: string): string => {
    if (voiceFailures.has(profileId)) return "brak odpowiedzi";
    const voice = voices.get(profileId)?.voice ?? null;
    return voice === null ? "nie opisany" : "opisany";
  };
  const voiceSentenceOf = (profileId: string): string => {
    const failed = voiceFailures.get(profileId);
    if (failed !== undefined) return `Głos: nie udało się odczytać (${failed}).`;
    const voice = voices.get(profileId)?.voice ?? null;
    if (voice === null) {
      return "Głos: nie opisany. Sesja agenta dostanie odmowę zamiast cudzego stylu.";
    }
    return `Głos: wersja ${voice.version}, zapisana ${moment(voice.created_at)}.`;
  };
  const unattached = accounts.filter((account) => account.person_profile_id === null);
  const supportOf = (platform: string) => platforms.find((row) => row.platform === platform);
  const readingOf = (accountId: string) =>
    audience.find((row) => row.social_account_id === accountId) ?? null;

  return (
    <StudioShell
      active={{ kind: "people" }}
      crumb={`${frame.value.workspace.name} / Profile i konta`}
      frame={frame.value}
      info={info}
    >
      <h1 className="st__title">Profile i konta</h1>
      <p className="st__lede">
        Profil to reprezentowana tożsamość, na przykład Anna albo Jan. Pod profilem wiszą
        jego konta platform, a projekty korzystają z wybranych kont.{" "}
        {AUDIENCE_SUM_IS_NOT_PEOPLE}
      </p>

      <div className="st__meta">
        <Pop label="Dodaj profil osoby lub marki" name="new-profile" title="Nowy profil">
          <form action="/studio/people" className="st-form" method="post">
            <input name="action" type="hidden" value="create_profile" />
            <input name="workspace_id" type="hidden" value={workspaceId} />
            <input name="return_to" type="hidden" value={here} />
            <label className="st-field">
              <span>Imię lub nazwa marki</span>
              <input
                className="st-input"
                maxLength={120}
                name="name"
                placeholder="np. Anna"
                required
                type="text"
              />
            </label>
            <label className="st-field">
              <span>Rodzaj</span>
              <select className="st-select" defaultValue="PERSON" name="kind">
                <option value="PERSON">osoba</option>
                <option value="BRAND">marka</option>
              </select>
            </label>
            <button className="st-btn" data-kind="primary" type="submit">
              Dodaj profil
            </button>
          </form>
        </Pop>
      </div>

      {profiles.length === 0 ? (
        <>
          <p className="st-empty">
            Nie ma jeszcze żadnego profilu. Odczyt się udał, po prostu nikogo tu nie dodano.
          </p>
          <FirstRun step={2} />
        </>
      ) : null}

      {profiles.map((profile) => {
        const mine = attachments.filter(
          (row) => row.person_profile_id === profile.person_profile_id,
        );
        const activeAccounts = mine.filter((row) => row.active);
        const detached = mine.filter((row) => !row.active);
        return (
          <section
            className="st__section"
            data-profile={profile.person_profile_id}
            data-profile-name={profile.name}
            key={profile.person_profile_id}
          >
            <div className="st__section-head">
              {profile.name} ({kindLabel(profile.kind)})
              <Pop
                label={`Dodaj konto platformy pod profilem ${profile.name}`}
                name={`add-account-${profile.person_profile_id}`}
                side="right"
                title={`Konto dla: ${profile.name}`}
              >
                <form action="/studio/people" className="st-form" method="post">
                  <input name="action" type="hidden" value="add_account" />
                  <input name="workspace_id" type="hidden" value={workspaceId} />
                  <input
                    name="person_profile_id"
                    type="hidden"
                    value={profile.person_profile_id}
                  />
                  <input name="return_to" type="hidden" value={here} />
                  <label className="st-field">
                    <span>Platforma</span>
                    <select className="st-select" defaultValue="REDDIT" name="platform">
                      {platforms.map((platform) => (
                        <option key={platform.platform} value={platform.platform}>
                          {platform.label}
                          {platform.integrated ? "" : " (integracja niedostępna)"}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="st-field">
                    <span>Nazwa konta na platformie</span>
                    <input
                      className="st-input"
                      maxLength={200}
                      name="external_account_id"
                      placeholder="np. Demo_Maintainer"
                      required
                      type="text"
                    />
                  </label>
                  <button className="st-btn" data-kind="primary" type="submit">
                    Dodaj konto
                  </button>
                </form>

                {unattached.length > 0 ? (
                  <form action="/studio/people" className="st-form" method="post">
                    <input name="action" type="hidden" value="attach_account" />
                    <input name="workspace_id" type="hidden" value={workspaceId} />
                    <input
                      name="person_profile_id"
                      type="hidden"
                      value={profile.person_profile_id}
                    />
                    <input name="return_to" type="hidden" value={here} />
                    <label className="st-field">
                      <span>Albo przypnij konto, które nie ma profilu</span>
                      <select className="st-select" name="social_account_id">
                        {unattached.map((account) => (
                          <option
                            key={account.social_account_id}
                            value={account.social_account_id}
                          >
                            {account.platform}: {account.external_account_id}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button className="st-btn" type="submit">
                      Przypnij
                    </button>
                  </form>
                ) : null}
              </Pop>
              <VoicePanel
                failure={voiceFailures.get(profile.person_profile_id)}
                profileId={profile.person_profile_id}
                profileName={profile.name}
                returnTo={here}
                view={voices.get(profile.person_profile_id)}
                workspaceId={workspaceId}
              />
            </div>

            {/* Stan głosu widoczny BEZ otwierania panelu: to jest pierwsza rzecz, o którą
                pyta się przy komentarzu brzmiącym nie jak ta osoba, a panel zamknięty
                wygląda tak samo, gdy głos jest, jak wtedy, gdy go nie ma. */}
            <p className="st-note" data-voice-state={voiceStateOf(profile.person_profile_id)}>
              {voiceSentenceOf(profile.person_profile_id)}
            </p>

            {activeAccounts.length === 0 ? (
              <>
                <p className="st-empty">Ten profil nie ma jeszcze konta na żadnej platformie.</p>
                {/* Podpowiedź kroku tylko wtedy, gdy w CAŁEJ przestrzeni nie ma jeszcze ani
                    jednego konta. Przy pięciu profilach i jednym bez konta to nie jest pierwsze
                    uruchomienie, tylko zwykły brak, a powtórzona instrukcja pod każdym pustym
                    profilem zamienia pomoc w szum. */}
                {accounts.length === 0 ? <FirstRun step={2} /> : null}
              </>
            ) : (
              <div className="st-surface">
                {activeAccounts.map((attachment) => {
                  const support = supportOf(attachment.platform);
                  const usedBy = assignments.filter(
                    (row) => row.active && row.social_account_id === attachment.social_account_id,
                  );
                  return (
                    <div
                      className="st-row"
                      data-account={attachment.social_account_id}
                      key={attachment.attachment_id}
                    >
                      <span aria-hidden="true" className="st-badge">
                        {attachment.platform.slice(0, 2)}
                      </span>
                      <span className="st-row__body">
                        <strong>{attachment.external_account_id}</strong>
                        <small>
                          {attachment.platform}, przypięte: {moment(attachment.attached_at)}
                          {usedBy.length === 0
                            ? ", brak przypisania do projektu"
                            : `, projekty: ${usedBy
                                .map(
                                  (row) =>
                                    projects.find(
                                      (project) => project.project_id === row.project_id,
                                    )?.name ?? row.project_id,
                                )
                                .join(", ")}`}
                        </small>
                        {/* P6: co wiemy o zasięgu tego konta. Bez odczytu to zdanie, a nie
                            zero: zero obserwujących czyta się jak fakt o koncie, a jest faktem
                            o nas. Od 27.09 X ma odczyt co godzinę z profilu konta (tabela 0019,
                            ze źródłem i momentem odczytu); inne platformy nadal nic. */}
                        <small data-audience={attachment.social_account_id}>
                          {audienceNote(support ?? null, readingOf(attachment.social_account_id))}
                        </small>
                      </span>
                      {support === undefined || support.integrated ? null : (
                        <span className="st-tag" data-tone="absent">
                          {support.note}
                        </span>
                      )}
                      <form action="/studio/people" method="post">
                        <input name="action" type="hidden" value="detach_account" />
                        <input name="workspace_id" type="hidden" value={workspaceId} />
                        <input
                          name="person_profile_id"
                          type="hidden"
                          value={profile.person_profile_id}
                        />
                        <input
                          name="social_account_id"
                          type="hidden"
                          value={attachment.social_account_id}
                        />
                        <input name="return_to" type="hidden" value={here} />
                        <button className="st-btn" data-kind="quiet" type="submit">
                          Odepnij
                        </button>
                      </form>
                    </div>
                  );
                })}
              </div>
            )}

            {detached.length > 0 ? (
              <details className="st-note">
                <summary>Odpięte wcześniej ({detached.length})</summary>
                <ul>
                  {detached.map((attachment) => (
                    <li key={attachment.attachment_id}>
                      {attachment.platform}: {attachment.external_account_id}, odpięte{" "}
                      {moment(attachment.detached_at)}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </section>
        );
      })}

      <section className="st__section">
        <div className="st__section-head">Konta bez profilu</div>
        {unattached.length === 0 ? (
          <p className="st-empty">Każde konto w tym workspace ma swój profil.</p>
        ) : (
          <div className="st-surface">
            {unattached.map((account) => (
              <div className="st-row" key={account.social_account_id}>
                <span aria-hidden="true" className="st-badge">
                  {account.platform.slice(0, 2)}
                </span>
                <span className="st-row__body">
                  <strong>{account.external_account_id}</strong>
                  <small>
                    {account.platform}, stan konta: {account.state}. Bez profilu nie da się go
                    przypisać do projektu.
                  </small>
                </span>
                {account.support === null || account.support.integrated ? null : (
                  <span className="st-tag" data-tone="absent">
                    {account.support.note}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="st__section">
        <div className="st__section-head">Co produkt umie na której platformie</div>
        <div className="st-surface">
          {platforms.map((platform) => (
            <div className="st-row" key={platform.platform}>
              <span className="st-row__body">
                <strong>{platform.label}</strong>
                <small>
                  {platform.integrated
                    ? [
                        platform.read ? "czyta" : null,
                        platform.comment ? "komentuje" : null,
                        platform.own_post ? "publikuje własne posty" : null,
                        platform.effect_readback ? "sprawdza skutek" : null,
                      ]
                        .filter((entry) => entry !== null)
                        .join(", ")
                    : "konto można dodać, ale nic się na nim nie wydarzy"}
                  {/* Z flagi macierzy, nie z nazwy platformy: od 27.09 X ma odczyt co godzinę. */}
                  {platform.audience_metrics
                    ? ". Liczby obserwujących: odczyt co godzinę z profilu konta."
                    : ". Liczby obserwujących: brak danych, nic ich nie odczytuje."}
                </small>
              </span>
              <span className="st-tag" data-tone={platform.integrated ? "state" : "absent"}>
                {platform.integrated ? "integracja działa" : platform.note}
              </span>
            </div>
          ))}
        </div>
      </section>
    </StudioShell>
  );
}
