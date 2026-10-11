import { Fragment } from "react";

import type { Read } from "../../lib/control-plane";
import { confidenceWord, counted, describeAge, describeDay, formatInstant, plural, type Tone } from "../../lib/format";
import type {
  CommunityPicture,
  CommunitySummary,
  JudgmentSummary,
  JudgmentTotals,
  UserProfile,
} from "../../lib/observe-schema";
import type { CapsuleStatus } from "../../lib/capsule-schema";
import { labelOf } from "../../lib/routes";
import { EmptyState } from "../empty-state";
import { CapsuleCard } from "./capsule-card";

/**
 * `/community` — Outcome A on one screen (`docs/OUTCOMES.md` §3), built for CLICKING, NOT READING
 * (docs/DASHBOARD-DESIGN.md §1, zasada nr 1, rewizja 3 po feedbacku założyciela).
 *
 * WHAT IS ON THE SURFACE AND WHAT IS ONE CLICK AWAY. On the surface: the decisions waiting for
 * the operator (ENGAGE cards, one after another — thread title, what the agent proposes, three
 * buttons), the state of observation as pills and number tiles, the balance of decisions and
 * answers, the picture's summary. One click away, inside collapsed `<details>` (no JavaScript):
 * every identifier, key, enum, hash, model name and interval; the agent's reasoning and
 * confidence; the IGNORE list; the profile form; the technical facts of a community. Nothing is
 * removed from the DOM — the texts the suites assert on stay in the HTML, word for word, in the
 * details bodies or in fine print under the human sentence.
 *
 * ORDER OF THE SCREEN IS THE ORDER OF ATTENTION: what needs the operator (review) → what is
 * happening (community) → the balance → what the agent set aside (collapsed) → what it understood
 * (the picture) → who it speaks for (profile) → the one maintenance action (resume).
 *
 * SEVEN SECTIONS, EACH WITH ITS OWN READ AND ITS OWN THREE STATES. The rule of `EntityScreen`
 * applies to every one of them separately: `undefined` (the read failed — say so with the
 * reason), an empty result (measured, say "brak" — one human sentence, one action, the exact
 * sentence in fine print), or data. A community list that failed to load must not make the
 * judgments look empty, and vice versa.
 *
 * FORMS, WITHOUT JAVASCRIPT, FILLED IN BY DEFAULT. Plain HTML posting to route handlers under
 * `app/community/` (D-016). "Dodaj community" comes prefilled with Hacker News and `ask`, so the
 * first observation is one click; the profile form is collapsed once a profile exists. None of the
 * forms publishes anything anywhere — Outcome A is observation only.
 *
 * WHAT A JUDGMENT CARD SHOWS. The thread as a link (the evidence anyone can open), the decision as
 * a human pill, WHAT THE AGENT PROPOSES as the main text, the three answers as one segmented
 * control, and the operator's recorded answer as a pill. Reasoning, confidence, profile version,
 * model and the literal "Twoja odpowiedź: …" sentence sit under "Dlaczego agent tak uważa".
 * `IGNORE` rows get the same control, because "you should have flagged this" is data too
 * (`docs/KIERUNEK-PRODUKTU.md` §9).
 */
export type CommunitiesRead = Read<readonly CommunitySummary[]>;

export interface PictureFor {
  readonly community: CommunitySummary;
  readonly picture: Read<CommunityPicture | null>;
}

export interface JudgmentsSection {
  readonly rows: readonly JudgmentSummary[];
  readonly truncated: boolean;
  readonly totals: JudgmentTotals;
}

export interface CommunityViewProps {
  readonly now: Date;
  readonly communities: CommunitiesRead;
  readonly pictures: readonly PictureFor[];
  readonly profile: Read<UserProfile | null>;
  readonly engage: Read<JudgmentsSection>;
  readonly recentIgnored: Read<JudgmentsSection>;
  /** The capsule's browser — the sign-in a platform behind a login wall needs from the human. */
  readonly capsule: Read<CapsuleStatus>;
  /** One sentence after a form POST, from `?info=`. Operator-facing, Polish. */
  readonly flash: string | null;
}

/** The three answers. `value` is the control plane's enum; `label` is what the operator clicks. */
export const VERDICTS: readonly { readonly value: string; readonly label: string; readonly short: string }[] = [
  { value: "AGREE", label: "Tak, mam coś do dodania", short: "Tak" },
  { value: "DISAGREE", label: "Nie", short: "Nie" },
  { value: "IRRELEVANT", label: "Nietrafione", short: "Nietrafione" },
];

export const PLATFORMS: readonly { readonly value: string; readonly label: string; readonly hint: string }[] = [
  { value: "HACKER_NEWS", label: "Hacker News", hint: "klucz: ask, show, new albo front" },
  { value: "REDDIT", label: "Reddit (wymaga aplikacji OAuth, D-023)", hint: "klucz: r/nazwa" },
];

/** Human words for the agent's decision. The enum stays in `data-decision`. */
const DECISION_LABELS: Readonly<Record<string, string>> = {
  ENGAGE: "warto się odezwać",
  IGNORE: "pominięty",
  WAIT: "poczekaj",
};

/** Human words for how a community is read. The enum stays in the technical details. */
const ACCESS_LABELS: Readonly<Record<string, string>> = {
  PUBLIC_API_NO_ACCOUNT: "publiczne API, bez konta",
  PUBLIC_WEB_NO_ACCOUNT: "publiczna strona, bez konta",
  ACCOUNT_REQUIRED: "wymaga konta",
  MEMBERSHIP_REQUIRED: "wymaga członkostwa",
  UNKNOWN: "dostęp nieznany",
};

/** ENGAGE is the one decision that asks something of the operator — it gets the accent. */
function decisionTone(decision: string): Tone {
  if (decision === "ENGAGE") return "accent";
  if (decision === "WAIT") return "warn";
  return "neutral";
}

function decisionLabel(decision: string): string {
  // An unknown decision is shown as it came, never dressed as one of the three.
  return DECISION_LABELS[decision] ?? decision;
}

function verdictShort(verdict: string): string {
  return VERDICTS.find((candidate) => candidate.value === verdict)?.short ?? verdict;
}

function accessLabel(accessMode: string): string {
  return ACCESS_LABELS[accessMode] ?? accessMode;
}

function pollLabel(seconds: number): string {
  return seconds % 60 === 0 ? `co ${seconds / 60} min` : `co ${seconds} s`;
}

function asStringList(value: unknown): readonly string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function asRecordList(value: unknown): readonly Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          typeof item === "object" && item !== null && !Array.isArray(item),
      )
    : [];
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

/**
 * A measured empty, said for clicking: one human sentence, one action, and the exact sentence
 * the suites assert on in fine print underneath — visible, not hidden, just quieter.
 */
function QuietAction({
  sentence,
  fine,
  action,
}: {
  readonly sentence: string;
  /** The exact phrasing the suites assert on, kept as fine print under the human sentence. */
  readonly fine: string;
  /** One action, never two. An `#anchor` points at a form that is already open on this screen. */
  readonly action?: { readonly href: string; readonly label: string };
}) {
  return (
    <div className="quiet quiet--action">
      <p className="quiet__sentence">{sentence}</p>
      <p className="quiet__fine">{fine}</p>
      {action === undefined ? null : (
        <a className="btn btn--small" href={action.href}>
          {action.label}
        </a>
      )}
    </div>
  );
}

/** Evidence references, collapsed: the word "dowody" on the surface, the refs one click away. */
function Refs({ refs }: { readonly refs: readonly string[] }) {
  if (refs.length === 0) {
    return <span className="picture__refs">bez odwołań</span>;
  }
  return (
    <details className="evidence">
      <summary className="evidence__summary">dowody ({refs.length})</summary>
      <span className="picture__refs">
        dowody:{" "}
        {refs.map((ref, index) => (
          <Fragment key={ref}>
            {index > 0 ? ", " : ""}
            <code>{ref}</code>
          </Fragment>
        ))}
      </span>
    </details>
  );
}

/**
 * One number tile. The label is DECLINED for the number (`plural`), because "1 wątków" is the kind
 * of detail that makes a screen read like a database dump.
 */
function Stat({
  value,
  forms,
  tone,
}: {
  readonly value: number;
  readonly forms: readonly [string, string, string];
  readonly tone?: Tone;
}) {
  return (
    <li className="stat" data-tone={tone}>
      <b className="stat__value">{value}</b> <span className="stat__label">{plural(value, forms)}</span>
    </li>
  );
}

/**
 * Czy wolno nam zakładać WŁASNE wątki w tej społeczności (0025).
 *
 * TRZY STANY, TRZY ZDANIA, I ŻADNE Z NICH NIE JEST DOMYŚLNE. „Nikt nie zdecydował" to nie to
 * samo co „nie wolno": pierwsze jest pytaniem do człowieka, drugie jego odpowiedzią. Ekran, na
 * którym brak decyzji wygląda jak odmowa, nigdy nie doczeka się decyzji, bo nikt nie zauważy,
 * że jest o co pytać.
 *
 * KARTA GŁOSU NIE JEST TU ROZSTRZYGNIĘCIEM. To, co model wyczytał ze społeczności, stoi wyżej
 * na tym samym ekranie jako kontekst; zgodę na publikowanie od siebie wydaje człowiek i tylko
 * ona ma znaczenie w bramce (`presence/own_posts.py`).
 */
export function OwnPostsDecision({ community }: { readonly community: CommunitySummary }) {
  const state = community.own_posts_allowed;
  const label =
    state === true
      ? "wolno zakładać własne wątki"
      : state === false
        ? "własnych wątków nie zakładamy"
        : "nikt jeszcze nie zdecydował";
  return (
    <div className="community__ownposts" data-ownposts={String(state)}>
      <p className="community__meta">
        <b>Własne wątki w tej społeczności:</b> {label}
        {community.own_posts_decided_at === null
          ? " (agent odpowiada tylko w cudzych wątkach)"
          : ` (zdecydowano ${formatInstant(community.own_posts_decided_at)})`}
      </p>
      <form action="/community/own-posts" className="form-row" method="post">
        <input name="community_id" type="hidden" value={community.community_id} />
        <button className="btn" disabled={state === true} name="allowed" type="submit" value="yes">
          Wolno
        </button>
        <button className="btn" disabled={state === false} name="allowed" type="submit" value="no">
          Nie wolno
        </button>
        <button
          className="btn"
          disabled={state === null}
          name="allowed"
          type="submit"
          value="undecided"
        >
          Cofnij decyzję
        </button>
      </form>
    </div>
  );
}

function CommunityCard({ community, now }: { readonly community: CommunitySummary; readonly now: Date }) {
  return (
    <li className="card community" data-community={community.community_id}>
      <div className="community__head">
        <div>
          <h3 className="community__name">{community.display_name}</h3>
          <p className="community__meta">
            Ostatni odczyt:{" "}
            {community.last_polled_at === null ? "jeszcze żadnego" : describeAge(community.last_polled_at, now)} ·
            odczyt {pollLabel(community.poll_interval_seconds)}
          </p>
        </div>
        <div className="community__pills">
          <span className="pill" data-tone={community.observe_enabled ? "accent" : "warn"}>
            {community.observe_enabled ? "obserwacja włączona" : "obserwacja WYŁĄCZONA"}
          </span>
          <span className="pill" data-tone="neutral">
            {accessLabel(community.access_mode)}
          </span>
        </div>
      </div>
      {community.counts === null ? (
        <p className="quiet quiet--inline">Liczników nie odczytano.</p>
      ) : (
        <ul className="stats">
          <Stat forms={["wątek", "wątki", "wątków"]} value={community.counts.threads} />
          <Stat forms={["komentarz", "komentarze", "komentarzy"]} value={community.counts.comments} />
          <Stat forms={["pomiar", "pomiary", "pomiarów"]} value={community.counts.snapshots} />
          <Stat forms={["osąd", "osądy", "osądów"]} value={community.counts.judgments} />
          <Stat forms={["obraz", "obrazy", "obrazów"]} value={community.counts.pictures} />
        </ul>
      )}
      <OwnPostsDecision community={community} />
      <details className="disclosure disclosure--inline">
        <summary className="disclosure__summary">Szczegóły techniczne</summary>
        <dl className="facts facts--plain">
          <div className="facts__pair">
            <dt className="facts__term">ref</dt>
            <dd className="facts__value">
              <code>{community.ref}</code>
            </dd>
          </div>
          <div className="facts__pair">
            <dt className="facts__term">access_mode</dt>
            <dd className="facts__value">
              <code>{community.access_mode}</code>
            </dd>
          </div>
          <div className="facts__pair">
            <dt className="facts__term">poll_interval_seconds</dt>
            <dd className="facts__value">{community.poll_interval_seconds}</dd>
          </div>
          <div className="facts__pair">
            <dt className="facts__term">last_polled_at</dt>
            <dd className="facts__value">
              {community.last_polled_at === null ? "jeszcze żadnego odczytu" : formatInstant(community.last_polled_at)}
            </dd>
          </div>
          <div className="facts__pair">
            <dt className="facts__term">watermark_created_at</dt>
            <dd className="facts__value">
              {community.watermark_created_at === null ? "brak" : formatInstant(community.watermark_created_at)}
            </dd>
          </div>
          <div className="facts__pair">
            <dt className="facts__term">community_id</dt>
            <dd className="facts__value">
              <code>{community.community_id}</code>
            </dd>
          </div>
        </dl>
      </details>
    </li>
  );
}

/** Prefilled so the first observation is one click: Hacker News, `ask`. */
function EnableForm({ open }: { readonly open: boolean }) {
  return (
    <details className="disclosure" id="dodaj-community" open={open}>
      <summary className="disclosure__summary">Dodaj community</summary>
      <form action="/community/enable" className="disclosure__body enable form-row" method="post">
        <div className="field">
          <label className="field__label" htmlFor="platform">
            Platforma
          </label>
          <select className="field__select" defaultValue="HACKER_NEWS" id="platform" name="platform" required>
            {PLATFORMS.map((platform) => (
              <option key={platform.value} value={platform.value}>
                {platform.label}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label className="field__label" htmlFor="community_key">
            Co obserwować
          </label>
          <span className="field__hint">Hacker News: ask, show, new albo front · Reddit: r/nazwa</span>
          <input className="field__input" defaultValue="ask" id="community_key" name="community_key" required type="text" />
        </div>
        <button className="btn btn--primary" type="submit">
          Włącz obserwację
        </button>
      </form>
    </details>
  );
}

function PictureSection({ community, picture, now }: PictureFor & { readonly now: Date }) {
  if (!picture.ok) {
    return (
      <EmptyState
        what={`Obraz community ${community.display_name}`}
        measured={false}
        reason={picture.reason}
      />
    );
  }
  if (picture.value === null) {
    return (
      <p className="quiet" data-picture="none">
        {community.display_name}: jeszcze bez obrazu. Pojawi się po pierwszym dniu obserwacji.
      </p>
    );
  }
  const content = picture.value.content;
  const topics = asRecordList(content["topics"]);
  const culture = asRecordList(content["durable_culture"]);
  const current = asRecordList(content["current_state"]);
  const problems = asRecordList(content["recurring_problems"]);
  const openings = asRecordList(content["where_user_could_contribute"]);
  return (
    <article className="card picture" data-picture={picture.value.picture_id}>
      <h3 className="picture__title">
        {community.display_name}
        <span className="pill" data-tone="neutral">
          obraz z {describeDay(picture.value.created_at, now)}
        </span>
      </h3>
      {picture.value.invalid_ref_count > 0 ? (
        <p className="notice" data-tone="warn">
          Odrzucono {counted(picture.value.invalid_ref_count, ["obserwację", "obserwacje", "obserwacji"])}{": "}
          {plural(picture.value.invalid_ref_count, ["wątek", "wątki", "wątków"])}, na{" "}
          {plural(picture.value.invalid_ref_count, ["który", "które", "które"])} powołał się model, nie
          istnieje.
        </p>
      ) : null}
      <p className="picture__summary">{text(content["summary"]) ?? "obraz nie ma podsumowania"}</p>

      <div className="picture__grid">
        <section className="picture__section">
          <h4 className="picture__heading">Tematy</h4>
          {topics.length === 0 ? (
            <p className="picture__none">obraz nie wskazuje tematów</p>
          ) : (
            <ul className="picture__list">
              {topics.map((topic, index) => (
                <li key={`${text(topic["name"]) ?? "temat"}-${index}`}>
                  <strong>{text(topic["name"]) ?? "(bez nazwy)"}</strong>: {text(topic["why_it_matters"]) ?? ""}{" "}
                  <Refs refs={asStringList(topic["thread_refs"])} />
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="picture__section">
          <h4 className="picture__heading">Powtarzające się problemy</h4>
          {problems.length === 0 ? (
            <p className="picture__none">obraz nie wskazuje powtarzających się problemów</p>
          ) : (
            <ul className="picture__list">
              {problems.map((problem, index) => (
                <li key={`${text(problem["problem"]) ?? "problem"}-${index}`}>
                  {text(problem["problem"]) ?? "(bez opisu)"} <Refs refs={asStringList(problem["thread_refs"])} />
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="picture__section">
          <h4 className="picture__heading">Gdzie mógłbyś coś dodać</h4>
          {openings.length === 0 ? (
            <p className="picture__none">obraz nie wskazuje otwartych miejsc</p>
          ) : (
            <ul className="picture__list">
              {openings.map((item, index) => (
                <li key={`${text(item["thread_ref"]) ?? "ref"}-${index}`}>
                  {text(item["why"]) ?? ""} <Refs refs={asStringList([item["thread_ref"]])} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {/* Three more sections, one click away: true, useful, and not what the eye needs first. */}
      <details className="disclosure disclosure--inline">
        <summary className="disclosure__summary">Więcej z obrazu</summary>
        <div className="picture__grid">
        <section className="picture__section">
          <h4 className="picture__heading">Co uruchamia rozmowę, a co jest ignorowane</h4>
          <p className="picture__pair">
            <strong>rozmowa:</strong> {text(content["what_gets_conversation"]) ?? "obraz nie mówi"}
          </p>
          <p className="picture__pair">
            <strong>ignorowane:</strong> {text(content["what_gets_ignored"]) ?? "obraz nie mówi"}
          </p>
        </section>

        <section className="picture__section">
          <h4 className="picture__heading">Kultura trwała</h4>
          {culture.length === 0 ? (
            <p className="picture__none">brak obserwacji o kulturze z dowodami. Jedno okno to słaby dowód</p>
          ) : (
            <ul className="picture__list">
              {culture.map((item, index) => (
                <li key={`${text(item["observation"]) ?? "kultura"}-${index}`}>
                  {text(item["observation"]) ?? "(bez opisu)"}{" "}
                  {/* The word on the surface, the number in `data-confidence` — a reader deciding
                      whether to trust one sentence does not need two decimal places to do it. */}
                  <span
                    className="picture__confidence"
                    data-confidence={typeof item["confidence"] === "number" ? item["confidence"].toFixed(2) : undefined}
                  >
                    pewność{" "}
                    {typeof item["confidence"] === "number" ? confidenceWord(item["confidence"]) : "nie podano"}
                  </span>{" "}
                  <Refs refs={asStringList(item["thread_refs"])} />
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="picture__section">
          <h4 className="picture__heading">Stan bieżący</h4>
          {current.length === 0 ? (
            <p className="picture__none">obraz nie wskazuje niczego specyficznego dla tego okna</p>
          ) : (
            <ul className="picture__list">
              {current.map((item, index) => (
                <li key={`${text(item["observation"]) ?? "stan"}-${index}`}>
                  {text(item["observation"]) ?? "(bez opisu)"} <Refs refs={asStringList(item["thread_refs"])} />
                </li>
              ))}
            </ul>
          )}
        </section>

        </div>
      </details>

      <details className="disclosure disclosure--inline">
        <summary className="disclosure__summary">Szczegóły obrazu</summary>
        <p className="picture__meta">
          wersja {picture.value.version} · okno {formatInstant(picture.value.window_start)} →{" "}
          {formatInstant(picture.value.window_end)} ·{" "}
          {counted(picture.value.thread_count, ["wątek", "wątki", "wątków"])} · model{" "}
          {picture.value.model}/{picture.value.effort} · prompt{" "}
          {picture.value.prompt_name}@{picture.value.prompt_sha256.slice(0, 12)} · narysowany{" "}
          {formatInstant(picture.value.created_at)}
        </p>
      </details>
    </article>
  );
}

/** Beyond this the proposal is cut on the surface; the whole of it stays one click away. */
const CONTRIBUTION_CHARS = 160;

/**
 * What the agent proposes — THE ONE TEXT on this screen the operator is meant to read, which is
 * exactly why it is capped at two lines. The full version is in the DOM, behind "pokaż całość", so
 * nothing is lost and nothing is a wall.
 */
function Contribution({ text: proposal }: { readonly text: string }) {
  if (proposal.length <= CONTRIBUTION_CHARS) {
    return (
      <p className="judgment__contribution">
        <strong>Co mógłbyś dodać:</strong> {proposal}
      </p>
    );
  }
  return (
    <div className="judgment__contribution">
      <p>
        <strong>Co mógłbyś dodać:</strong> {`${proposal.slice(0, CONTRIBUTION_CHARS)}…`}
      </p>
      <details className="disclosure disclosure--inline">
        <summary className="disclosure__summary">pokaż całość</summary>
        <p className="judgment__contribution-full">{proposal}</p>
      </details>
    </div>
  );
}

function JudgmentCard({
  judgment,
  now,
  compact,
}: {
  readonly judgment: JudgmentSummary;
  readonly now: Date;
  readonly compact: boolean;
}) {
  return (
    <li
      className={compact ? "card judgment judgment--compact" : "card judgment"}
      data-decision={judgment.decision}
      data-judgment={judgment.judgment_id}
    >
      <div className="judgment__head">
        <a className="judgment__thread" href={judgment.thread_url} rel="noreferrer">
          {judgment.thread_title}
        </a>
        <span className="pill judgment__decision" data-tone={decisionTone(judgment.decision)}>
          {decisionLabel(judgment.decision)}
        </span>
      </div>
      {judgment.what_user_could_add !== null ? (
        <Contribution text={judgment.what_user_could_add} />
      ) : (
        <p className="judgment__reasoning">{judgment.reasoning}</p>
      )}
      <div className="judgment__answer">
        {judgment.latest_feedback !== null ? (
          <span className="pill judgment__answered" data-tone="neutral">
            Odpowiedziałeś: {verdictShort(judgment.latest_feedback)}
          </span>
        ) : (
          <span className="judgment__unanswered">Czekam na Twoją odpowiedź</span>
        )}
        <form action="/community/feedback" className="judgment__form verdicts" method="post">
          <input name="judgment_id" type="hidden" value={judgment.judgment_id} />
          {VERDICTS.map((verdict) => (
            <button
              className="verdicts__item"
              key={verdict.value}
              name="verdict"
              type="submit"
              value={verdict.value}
            >
              {verdict.label}
            </button>
          ))}
        </form>
      </div>
      <details className="disclosure disclosure--inline judgment__details">
        <summary className="disclosure__summary">Dlaczego agent tak uważa</summary>
        <div className="disclosure__body">
          {judgment.what_user_could_add !== null ? (
            <p className="judgment__reasoning">{judgment.reasoning}</p>
          ) : null}
          <p className="judgment__meta">
            {judgment.community_display_name} · {describeAge(judgment.created_at, now)} · pewność{" "}
            {judgment.confidence.toFixed(2)} · profil v{judgment.user_profile_version} · model {judgment.model}
          </p>
          {judgment.latest_feedback !== null ? (
            <p className="judgment__feedback" data-feedback={judgment.latest_feedback}>
              Twoja odpowiedź: <strong>{judgment.latest_feedback}</strong>
              {judgment.latest_feedback_note ? `. Notatka: ${judgment.latest_feedback_note}` : ""}. Możesz zmienić
              zdanie; historia zostaje.
            </p>
          ) : (
            <p className="judgment__feedback" data-feedback="none">
              Bez Twojej odpowiedzi.
            </p>
          )}
        </div>
      </details>
    </li>
  );
}

export function CommunityView({
  now,
  communities,
  pictures,
  profile,
  engage,
  recentIgnored,
  capsule,
  flash,
}: CommunityViewProps) {
  const hasCommunity = communities.ok && communities.value.length > 0;
  const hasProfile = profile.ok && profile.value !== null;
  const toReview = engage.ok ? engage.value.rows.length : 0;
  const ignoredCount = recentIgnored.ok ? recentIgnored.value.rows.length : 0;

  return (
    <section className="screen" data-route="community" data-screen="community">
      <header className="screen__head">
        <h1 className="screen__title">{labelOf("community")}</h1>
        <details className="disclosure disclosure--inline">
          <summary className="disclosure__summary">Co tu widzę</summary>
          <div className="disclosure__body">
            <p>
              Agent obserwuje jedno prawdziwe community, raz dziennie rysuje jego obraz i dla każdego
              nowego wątku decyduje, czy warto się odezwać. Nic stąd nie jest publikowane; Twoje
              odpowiedzi przy osądach uczą agenta.
            </p>
          </div>
        </details>
      </header>
      {flash !== null ? (
        <p className="notice" data-flash="tak" data-tone="accent">
          {flash}
        </p>
      ) : null}

      {/* 1. Do przejrzenia — to, co wymaga człowieka */}
      <section className="block judgments" data-judgments="Osądy ENGAGE">
        <h2 className="block__title">
          Do przejrzenia{" "}
          {toReview > 0 ? (
            <span className="pill" data-tone="accent">
              {toReview}
            </span>
          ) : null}
        </h2>
        {!engage.ok ? (
          <EmptyState what="Osądy ENGAGE" measured={false} reason={engage.reason} />
        ) : engage.value.rows.length === 0 ? (
          <QuietAction
            action={{ href: "/jobs", label: "Zobacz, co robi agent" }}
            fine="Osądy ENGAGE: brak."
            sentence="Nic do przejrzenia. Agent nie wskazał jeszcze rozmowy dla Ciebie."
          />
        ) : (
          <>
            <ul className="judgments__list">
              {engage.value.rows.map((judgment) => (
                <JudgmentCard compact={false} judgment={judgment} key={judgment.judgment_id} now={now} />
              ))}
            </ul>
            {engage.value.truncated ? (
              <p className="screen__pagenote">Strona wypełniła się, więc może być więcej osądów niż widać.</p>
            ) : null}
          </>
        )}
      </section>

      {/* 2. Stan obserwacji */}
      <section className="block communities" data-communities="tak">
        <h2 className="block__title">Obserwowane community</h2>
        {!communities.ok ? (
          <EmptyState what="Obserwowane community" measured={false} reason={communities.reason} />
        ) : communities.value.length === 0 ? (
          <QuietAction
            action={{ href: "#dodaj-community", label: "Dodaj community" }}
            fine="Obserwowane community: brak."
            sentence="Jeszcze nic nie obserwujesz."
          />
        ) : (
          <ul className="communities__list">
            {communities.value.map((community) => (
              <CommunityCard community={community} key={community.community_id} now={now} />
            ))}
          </ul>
        )}
        <EnableForm open={communities.ok && !hasCommunity} />
      </section>

      {/* 2a. Dostęp do platformy za login wallem — jedyne miejsce, gdzie człowiek loguje agenta. */}
      <section className="block">
        <CapsuleCard capsule={capsule} now={now} />
      </section>

      {/* 3. Bilans */}
      {engage.ok ? (
        <section className="block">
          <h2 className="block__title">Bilans</h2>
          <ul className="stats stats--totals" data-totals="tak">
            <li className="stat" data-total="decision_ENGAGE" data-tone="accent">
              <b className="stat__value">{engage.value.totals.decision_ENGAGE}</b>{" "}
              <span className="stat__label">warto się odezwać</span>
            </li>
            <li className="stat" data-total="decision_IGNORE">
              <b className="stat__value">{engage.value.totals.decision_IGNORE}</b>{" "}
              <span className="stat__label">pominięte</span>
            </li>
            <li className="stat" data-total="decision_WAIT" data-tone="warn">
              <b className="stat__value">{engage.value.totals.decision_WAIT}</b>{" "}
              <span className="stat__label">poczekaj</span>
            </li>
            <li className="stat" data-total="feedback_AGREE">
              <b className="stat__value">{engage.value.totals.feedback_AGREE}</b>{" "}
              <span className="stat__label">Twoje tak</span>
            </li>
            <li className="stat" data-total="feedback_DISAGREE">
              <b className="stat__value">{engage.value.totals.feedback_DISAGREE}</b>{" "}
              <span className="stat__label">Twoje nie</span>
            </li>
            <li className="stat" data-total="feedback_IRRELEVANT">
              <b className="stat__value">{engage.value.totals.feedback_IRRELEVANT}</b>{" "}
              <span className="stat__label">nietrafione</span>
            </li>
          </ul>
        </section>
      ) : null}

      {/* 4. Pominięte przez agenta — zwinięte, do kontroli przeoczeń */}
      <section className="block judgments" data-judgments="Ostatnie osądy IGNORE">
        <details className="disclosure">
          <summary className="disclosure__summary">
            <span>
              Pominięte przez agenta{" "}
              {recentIgnored.ok ? (
                <span className="pill" data-tone="neutral">
                  {ignoredCount}
                </span>
              ) : null}{" "}
              <small className="block__hint">sprawdź, czy nie przegapił czegoś Twojego</small>
            </span>
          </summary>
          <div className="disclosure__body">
            {!recentIgnored.ok ? (
              <EmptyState what="Ostatnie osądy IGNORE" measured={false} reason={recentIgnored.reason} />
            ) : recentIgnored.value.rows.length === 0 ? (
              <p className="quiet quiet--inline">
                Ostatnie osądy IGNORE: brak. Jeszcze żadnych osądów. Czekają na profil, na model albo
                na pierwsze wątki.
              </p>
            ) : (
              <>
                <ul className="judgments__list">
                  {recentIgnored.value.rows.map((judgment) => (
                    <JudgmentCard compact judgment={judgment} key={judgment.judgment_id} now={now} />
                  ))}
                </ul>
                {recentIgnored.value.truncated ? (
                  <p className="screen__pagenote">Strona wypełniła się, więc może być więcej osądów niż widać.</p>
                ) : null}
              </>
            )}
          </div>
        </details>
      </section>

      {/* 5. Obraz community */}
      <section className="block pictures" data-pictures={String(pictures.length)}>
        <h2 className="block__title">Obraz community</h2>
        {pictures.length === 0 ? (
          <p className="quiet">Bez obserwowanego community nie ma czego rysować.</p>
        ) : (
          pictures.map((entry) => (
            <PictureSection community={entry.community} key={entry.community.community_id} now={now} picture={entry.picture} />
          ))
        )}
      </section>

      {/* 6. Profil */}
      <section className="block profile" data-profile={profile.ok ? (profile.value === null ? "none" : "present") : "unread"}>
        <h2 className="block__title">Twój profil</h2>
        {!profile.ok ? (
          <EmptyState what="Profil użytkownika" measured={false} reason={profile.reason} />
        ) : profile.value === null ? (
          <QuietAction
            action={{ href: "#profil", label: "Wypełnij profil" }}
            fine="Brak profilu."
            sentence="Bez profilu agent nie wie, kogo reprezentuje. Osądy czekają."
          />
        ) : (
          <p className="profile__current">
            Profil v{profile.value.version}, zapisany {describeAge(profile.value.created_at, now)}.
          </p>
        )}
        <details className="disclosure" id="profil" open={profile.ok && !hasProfile}>
          <summary className="disclosure__summary">{hasProfile ? "Edytuj profil" : "Wypełnij profil"}</summary>
          <form action="/community/profile" className="disclosure__body profile__form form-stack" method="post">
            <div className="field">
              <label className="field__label" htmlFor="knows_deeply">
                Co wiem głęboko
              </label>
              <span className="field__hint">tematy, w których mam realne doświadczenie</span>
              <textarea className="field__textarea" defaultValue={profile.ok && profile.value ? profile.value.knows_deeply : ""} id="knows_deeply" name="knows_deeply" required rows={4} />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="positions">
                Moje stanowiska
              </label>
              <span className="field__hint">co uważam i dlaczego</span>
              <textarea className="field__textarea" defaultValue={profile.ok && profile.value ? profile.value.positions : ""} id="positions" name="positions" required rows={4} />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="would_never_claim">
                Czego nigdy bym nie twierdził
              </label>
              <span className="field__hint">granice, których agent nie przekracza</span>
              <textarea className="field__textarea" defaultValue={profile.ok && profile.value ? profile.value.would_never_claim : ""} id="would_never_claim" name="would_never_claim" required rows={3} />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="voice_samples">
                Próbki mojego głosu
              </label>
              <span className="field__hint">2 albo 3 krótkie fragmenty, opcjonalnie</span>
              <textarea className="field__textarea" defaultValue={profile.ok && profile.value ? profile.value.voice_samples : ""} id="voice_samples" name="voice_samples" rows={3} />
            </div>
            <div className="form__actions">
              <button className="btn btn--primary" type="submit">
                {hasProfile ? "Zapisz nową wersję" : "Zapisz profil"}
              </button>
            </div>
          </form>
        </details>
      </section>

      {/* 7. Jedyna akcja utrzymaniowa */}
      <section className="block">
        <div className="card settings">
          <div>
            <h2 className="block__title">Zatrzymane zadania</h2>
            <p className="settings__text">
              Gdy zalogujesz model albo uzupełnisz profil, wznów je jednym kliknięciem.
            </p>
          </div>
          <form action="/community/resume" className="resume" method="post">
            <button className="btn btn--primary" type="submit">
              Wznów zatrzymane zadania
            </button>
          </form>
        </div>
      </section>
    </section>
  );
}
