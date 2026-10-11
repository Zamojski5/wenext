/**
 * `GET /invitation?token=…` — accepting an invitation to somebody else's space.
 *
 * OUTSIDE THE AUTH BOUNDARY, because the person accepting has no session yet and, usually, no
 * account. Declared in `lib/routes.ts` with `gated: false` next to `/login` and `/register`,
 * which is the only place that fact lives.
 *
 * THE TOKEN ARRIVES IN THE QUERY AND IS PUT STRAIGHT BACK INTO A HIDDEN FIELD, never into a
 * cookie and never into a log line this package writes. It is a one-time credential: the
 * control plane keeps only its SHA-256, and accepting spends it.
 *
 * THE PASSWORD IS CHOSEN HERE. An invitation that carried a password would be a credential
 * travelling in a link somebody forwards; whoever invited you does not learn this one.
 */
const REFUSED = "odmowa";
const UNREACHABLE = "niedostepne";

export default async function InvitationPage({
  searchParams,
}: {
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const parameters = await searchParams;
  const rawToken = parameters["token"];
  const token = typeof rawToken === "string" ? rawToken : "";
  const failure = parameters["blad"];
  const detail = parameters["powod"];

  return (
    <section className="screen screen--login" data-route="invitation">
      <div className="card login-card">
        <p className="login-card__brand">
          <span aria-hidden="true" className="shell__logo">
            w
          </span>
          wenext
        </p>
        <h1 className="screen__title">Dołącz do przestrzeni</h1>

        {token === "" ? (
          <p className="notice" data-invitation-error="brak-tokenu" data-tone="danger">
            Ten link nie zawiera zaproszenia. Poproś osobę, która Cię zaprasza, o pełny adres.
          </p>
        ) : null}
        {failure === REFUSED ? (
          <p className="notice" data-invitation-error={REFUSED} data-tone="danger">
            {typeof detail === "string" && detail !== ""
              ? detail
              : "Nie udało się przyjąć zaproszenia."}
          </p>
        ) : null}
        {failure === UNREACHABLE ? (
          <p className="notice" data-invitation-error={UNREACHABLE} data-tone="warn">
            Nie udało się porozmawiać z control plane. Zapisu nie było, spróbuj ponownie.
          </p>
        ) : null}

        {token === "" ? null : (
          <form action="/invitation/submit" className="login" method="post">
            <input name="token" type="hidden" value={token} />
            <div className="field">
              <label className="field__label" htmlFor="password">
                Ustaw swoje hasło (co najmniej 8 znaków)
              </label>
              <input
                autoComplete="new-password"
                className="field__input"
                id="password"
                minLength={8}
                name="password"
                required
                type="password"
              />
            </div>
            <p className="notice" data-tone="plain">
              Hasło ustawiasz tutaj i nikt inny go nie zna. Jeśli masz już konto w wenext, zostaje
              Twoje dotychczasowe hasło, a to pole możesz wypełnić czymkolwiek.
            </p>
            <button className="btn btn--primary" type="submit">
              Dołącz
            </button>
          </form>
        )}
      </div>
    </section>
  );
}
