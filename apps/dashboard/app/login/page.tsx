/**
 * `GET /login` — the one screen outside the auth boundary, and the only one with a password field.
 *
 * THE FORM IS PLAIN HTML AND POSTS TO A ROUTE HANDLER. No `"use client"`, no Server Action, no
 * `fetch` from the browser. Three consequences, all of them wanted:
 *
 *   1. the password travels in a normal form POST to THIS server, which then presents it to the
 *      control plane server-side. The browser never talks to the control plane (D-016), and there
 *      is no client bundle here that could be made to;
 *   2. the screen works with JavaScript switched off, which for a login screen is the difference
 *      between "the panel is broken" and "the panel works";
 *   3. the whole tree stays renderable by `react-dom/server`, so the suite reads the real form.
 *
 * WHAT THE ERROR MESSAGE MAY AND MAY NOT SAY. `?blad=odmowa` means the control plane refused the
 * credential — it does NOT distinguish a wrong password from "no operator is provisioned", because
 * the control plane deliberately does not distinguish them either (`auth.verify_credential`: both
 * zero and many enabled rows are refusals, "and the caller learns nothing about which").
 * `?blad=niedostepne` means the read itself did not happen, which is not a statement about the
 * password at all: telling an operator "wrong password" when the API is down sends them looking
 * for a typo that does not exist.
 *
 * `data-route="login"` on the root is what makes the layout's sidebar disappear on this screen —
 * CSS only, see app/layout.tsx. The layout itself still knows nothing about the request.
 */
import { loginNext } from "../../lib/login-next";
import { headers } from "next/headers";
import { redirect } from "next/navigation";

import { LOCAL_MODE_BANNER, hostIsLoopback, localModeWho } from "../../lib/local-access";

const REFUSED = "odmowa";
const UNREACHABLE = "niedostepne";

export default async function LoginPage({
  searchParams,
}: {
  // Next 15+ hands search params as a promise; awaiting it is not optional.
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const parameters = await searchParams;
  const failure = parameters["blad"];
  const next = loginNext(parameters["next"]);

  // TRYB LOKALNY. Włączony przełącznik i żądanie z pętli zwrotnej znaczą, że hasło na tej
  // maszynie niczego nie chroni: kto siedzi przy tym komputerze, i tak ma dostęp do bazy.
  // Wejście idzie wtedy bez pytania, Z JEDNYM WYJĄTKIEM: zaraz po wylogowaniu ekran zostaje
  // i pokazuje przycisk. Bez tego wyjątku „wyloguj" byłoby przyciskiem, który nic nie robi,
  // i nie dałoby się zalogować drugim kontem na własnym laptopie.
  const host = (await headers()).get("host");
  const localMode = localModeWho() !== "" && hostIsLoopback(host);
  const justLoggedOut = parameters["wylogowano"] === "1";
  if (localMode && !justLoggedOut && failure === undefined) {
    redirect(`/login/local?next=${encodeURIComponent(next)}`);
  }

  return (
    <section className="screen screen--login" data-route="login">
      <div className="card login-card">
        <p className="login-card__brand">
          <span aria-hidden="true" className="shell__logo">
            w
          </span>
          wenext: panel operatora
        </p>
        <h1 className="screen__title">Zaloguj się</h1>

        {localMode ? (
          <p className="notice" data-local-mode="1" data-tone="warn">
            {LOCAL_MODE_BANNER} Możesz wejść bez hasła jako właściciel tej maszyny albo zalogować
            się zwykłym kontem poniżej.{" "}
            <a href={`/login/local?next=${encodeURIComponent(next)}`}>Wejdź bez hasła</a>
          </p>
        ) : null}

        {failure === REFUSED ? (
          <p className="notice" data-login-error={REFUSED} data-tone="danger">
            Control plane odmówił. Ten komunikat nie mówi, czy hasło jest złe, czy operator nie jest w ogóle
            założony. Control plane też tego nie rozróżnia, i to jest celowe.
          </p>
        ) : null}
        {failure === UNREACHABLE ? (
          <p className="notice" data-login-error={UNREACHABLE} data-tone="warn">
            Nie udało się porozmawiać z control plane. To nie jest informacja o haśle: odczytu nie
            było, więc o poświadczeniu nie wiadomo nic.
          </p>
        ) : null}

        {/* P7: the ordinary door. Email and password of a user account, which is how everybody
            except the founder's original login gets in. */}
        <form action="/login/submit" className="login" method="post">
          <input name="next" type="hidden" value={next} />
          <input name="kind" type="hidden" value="user" />
          <div className="field">
            <label className="field__label" htmlFor="email">
              Adres e-mail
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
            <label className="field__label" htmlFor="user-password">
              Hasło
            </label>
            <input
              autoComplete="current-password"
              className="field__input"
              id="user-password"
              name="password"
              required
              type="password"
            />
          </div>
          <button className="btn btn--primary" type="submit">
            Zaloguj
          </button>
        </form>

        <p className="notice" data-tone="plain">
          Nie masz jeszcze przestrzeni? <a href="/register">Załóż własną</a>.
        </p>

        {/* The founder's original login. KEPT, not replaced: migration 0018 moved that account
            to `users` without an address, so until it names itself this is the only way in for
            it. `details` rather than a second visible form: it is one deployment's legacy, not
            a choice most people have. */}
        <details className="notice" data-login-form="operator">
          <summary>Logowanie hasłem operatora (wdrożenie sprzed kont)</summary>
          <form action="/login/submit" className="login" method="post">
            <input name="next" type="hidden" value={next} />
          <input name="kind" type="hidden" value="operator" />
            <div className="field">
              <label className="field__label" htmlFor="password">
                Hasło operatora
              </label>
              <input
                autoComplete="current-password"
                className="field__input"
                id="password"
                name="password"
                required
                type="password"
              />
            </div>
            <button className="btn" type="submit">
              Zaloguj jako operator
            </button>
          </form>
        </details>
      </div>
    </section>
  );
}
