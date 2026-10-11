/**
 * A screen that reads nothing — and therefore renders no list.
 *
 * WHY THIS COMPONENT EXISTS. `/transitions` and `/approvals` are in the navigation because D-013
 * says the dashboard gets a screen for each entity F1 writes, and F1 does write state transitions
 * and the approval path. But at the time this package was built there was NO READER for either —
 * measured at commit 98570ed, see `lib/routes.ts` for the dated measurement and for the trigger
 * that turns these screens into list views. A LIST on them would therefore be an empty frame that
 * nobody read anything into, and this package's whole point is that "brak danych" is a claim about
 * a read that actually happened. Rendering an empty table there would repeat the mistake this
 * repository keeps paying for: a screen reporting a measured empty where nothing was measured.
 *
 * So these screens say where the fact lives today and which phase brings a screen for it. No
 * table, no empty state, no counter, no tick. `data-screen="signpost"` is the machine-readable
 * half, so a test can assert that this is not a list screen rather than infer it from prose.
 *
 * SHAPE (docs/DASHBOARD-DESIGN.md §1 zasada nr 1, §6): the FIRST sentence is on the surface, the
 * rest sits under a collapsed "Dlaczego"; the DESTINATIONS — cards linking to the screens that DO
 * read — are the thing to click. Every sentence is still a `<p>` (the suite counts them), and the
 * destinations are plain internal `<a>` and nothing else: no `<p>`, no table, no `data-state`.
 */
/**
 * The ONE thing the overview puts on the surface: a number that was actually read, as a link.
 *
 * `value: null` means the read FAILED, and then the tile says so with the reason instead of
 * showing a zero — a "0 do przejrzenia" over a failed read is `all([]) == True` with a number in
 * front of it, which is the mistake this package exists to make impossible.
 */
export interface Highlight {
  readonly href: string;
  readonly label: string;
  readonly value: number | null;
  readonly reason?: string;
}

export interface Destination {
  /** Internal path — one of `NAV_ROUTES`. */
  readonly href: string;
  /** Operator-facing label, usually `labelOf(routeId)`. */
  readonly label: string;
  /** One short sentence: what the operator finds there. */
  readonly note: string;
}

interface SignpostProps {
  readonly heading: string;
  /** Operator-facing paragraphs (Polish, D-010). At least one — a signpost with nothing on it. */
  readonly paragraphs: readonly string[];
  /** The nav route this screen belongs to; lands on the root as `data-route` (see app/layout.tsx). */
  readonly routeId?: string;
  /** Where to go instead. Rendered as link cards; internal navigation only. */
  readonly destinations?: readonly Destination[];
  /** The one measured number worth a tile. Omit when the screen measured nothing. */
  readonly highlight?: Highlight;
}

export function Signpost({ heading, paragraphs, routeId, destinations, highlight }: SignpostProps) {
  if (paragraphs.length === 0) {
    throw new Error(
      `ekran-drogowskaz '${heading}' nie ma ani jednego zdania. Pusty drogowskaz jest gorszy ` +
        "niż jego brak, bo czytający przestaje szukać",
    );
  }
  for (const destination of destinations ?? []) {
    if (!destination.href.startsWith("/")) {
      throw new Error(
        `drogowskaz '${heading}' kieruje do '${destination.href}'. Dozwolone są wyłącznie ścieżki ` +
          "wewnętrzne, bo panel nie prowadzi nigdzie poza pętlę zwrotną",
      );
    }
  }
  const [first, ...rest] = paragraphs;
  return (
    <section className="screen" data-route={routeId} data-screen="signpost">
      <header className="screen__head">
        <h1 className="screen__title">{heading}</h1>
      </header>
      {highlight === undefined ? null : (
        <a className="card decision" data-decision-tile="tak" href={highlight.href}>
          {highlight.value === null ? (
            <>
              <b className="decision__value" data-missing="tak">
                nie odczytano
              </b>
              <span className="decision__label">
                {highlight.label}: {highlight.reason ?? "powód nie został podany"}
              </span>
            </>
          ) : (
            <>
              <b className="decision__value">{highlight.value}</b>
              <span className="decision__label">{highlight.label}</span>
            </>
          )}
        </a>
      )}
      {destinations !== undefined && destinations.length > 0 ? (
        <ul className="dest">
          {destinations.map((destination) => (
            <li key={destination.href}>
              <a className="card dest__item" href={destination.href}>
                <strong className="dest__label">{destination.label}</strong>
                <span className="dest__note">{destination.note}</span>
              </a>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="card card--signpost">
        <span className="pill signpost__badge" data-tone="neutral">
          drogowskaz
        </span>
        <p className="screen__lede" key={(first ?? "").slice(0, 40)}>
          {first}
        </p>
        {rest.length > 0 ? (
          <details className="disclosure disclosure--inline">
            <summary className="disclosure__summary">Dlaczego</summary>
            <div className="disclosure__body">
              {rest.map((paragraph) => (
                <p className="screen__lede" key={paragraph.slice(0, 40)}>
                  {paragraph}
                </p>
              ))}
            </div>
          </details>
        ) : null}
      </div>
    </section>
  );
}
