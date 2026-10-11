/**
 * `GET /register` — the front door of the product for somebody who has never been here.
 *
 * OUTSIDE THE AUTH BOUNDARY, like `/login`, and for the same reason: this is where a credential
 * is created rather than checked. `lib/routes.ts` lists it with `gated: false`, which is the
 * ONE place that fact is declared, and `tests/source_scan.test.ts` walks the tree to make sure
 * no other page joins it by accident.
 *
 * PLAIN HTML, NO CLIENT JAVASCRIPT. The password travels in a normal form POST to this server,
 * which presents it to the control plane server-side; the browser never talks to the control
 * plane (D-016). A registration screen that needs a bundle to work is a registration screen that
 * does not work when the bundle fails.
 *
 * WHAT THE REFUSALS MAY SAY. "Ten adres jest zajęty" is deliberately NOT one of them: this route
 * is reachable by anybody, and a message distinguishing a taken address from a wrong one is a
 * free account oracle. The control plane refuses with one sentence for both, and this screen
 * prints that sentence rather than inventing a friendlier one.
 */
const REFUSED = "odmowa";
const UNREACHABLE = "niedostepne";
const CLOSED = "zamkniete";

export default async function RegisterPage({
  searchParams,
}: {
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const parameters = await searchParams;
  const failure = parameters["blad"];
  const detail = parameters["powod"];

  return (
    <section className="screen screen--login" data-route="register">
      <div className="card login-card">
        <p className="login-card__brand">
          <span aria-hidden="true" className="shell__logo">
            w
          </span>
          wenext
        </p>
        <h1 className="screen__title">Załóż własną przestrzeń</h1>
        <p className="notice" data-tone="plain">
          Przestrzeń jest Twoja: projekty, materiały i konta widzisz tylko Ty i osoby, które
          zaprosisz. Nikt z zewnątrz nie musi niczego zakładać za Ciebie.
        </p>

        {failure === REFUSED ? (
          <p className="notice" data-register-error={REFUSED} data-tone="danger">
            {typeof detail === "string" && detail !== ""
              ? detail
              : "Nie udało się założyć przestrzeni. Sprawdź adres i hasło."}
          </p>
        ) : null}
        {failure === CLOSED ? (
          <p className="notice" data-register-error={CLOSED} data-tone="warn">
            To wdrożenie nie przyjmuje nowych przestrzeni. Poproś o zaproszenie osobę, która już
            tu pracuje.
          </p>
        ) : null}
        {failure === UNREACHABLE ? (
          <p className="notice" data-register-error={UNREACHABLE} data-tone="warn">
            Nie udało się porozmawiać z control plane. To nie jest informacja o Twoich danych:
            zapisu nie było.
          </p>
        ) : null}

        <form action="/register/submit" className="login" method="post">
          <div className="field">
            <label className="field__label" htmlFor="organization_name">
              Nazwa przestrzeni
            </label>
            <input
              className="field__input"
              id="organization_name"
              maxLength={200}
              name="organization_name"
              required
              type="text"
            />
          </div>
          <div className="field">
            <label className="field__label" htmlFor="email">
              Twój adres e-mail
            </label>
            <input
              autoComplete="email"
              className="field__input"
              id="email"
              maxLength={320}
              name="email"
              required
              type="email"
            />
          </div>
          <div className="field">
            <label className="field__label" htmlFor="password">
              Hasło (co najmniej 8 znaków)
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
          <button className="btn btn--primary" type="submit">
            Załóż przestrzeń
          </button>
        </form>
        <p className="notice" data-tone="plain">
          Masz już konto? <a href="/login">Zaloguj się</a>.
        </p>
      </div>
    </section>
  );
}
