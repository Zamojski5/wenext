import type { ReactNode } from "react";

import { NavIcon } from "../../components/nav-icon";
import { NAV_ROUTES } from "../../lib/routes";

/**
 * The operator panel's shell: the sidebar it has had since E1.F1, now one level down.
 *
 * It used to be `app/layout.tsx`. After the pivot (D-038 to D-040) the studio is the product and
 * this panel is one entry inside it, so the chrome moved here and the root layout became the
 * document and nothing else. THE CONTENT OF THIS SHELL IS UNCHANGED ON PURPOSE — the founder's
 * brief says the operator screens stay available, and a "port" that quietly dropped the audit
 * link would be a deletion wearing a refactor's clothes.
 *
 * THE NAVIGATION IS GENERATED FROM `lib/routes.ts`, never written out here: a hand-written copy
 * would drift from the routes, and a nav entry with no page behind it is a 404 dressed as a
 * feature. `tests/layout_routes.test.tsx` checks both directions and the page files on disk.
 *
 * PLAIN `<a>` RATHER THAN `next/link`. Two reasons, and the second is load-bearing: prefetching
 * would have the BROWSER issue background requests for gated screens, every one of them
 * redirected to the login screen — a hover turned into a stream of pointless round trips. And
 * because there is no `next/link` anywhere, the whole tree renders under `react-dom/server`,
 * which is what lets the suite read the real navigation instead of a stub.
 *
 * THIS LAYOUT READS NO COOKIE AND NO URL, AND THAT IS DELIBERATE. `cookies()` and `headers()`
 * only work inside a request scope, so a layout that called them could not be rendered by the
 * suite at all and the navigation would lose its only test. The ACTIVE item is highlighted by
 * CSS instead, driven by the `data-route` attribute each screen puts on its own root element
 * (`body:has(.shell__main [data-route=…]) .shell__nav-item[data-nav=…]`). The nav links carry
 * `data-nav`, not `data-route`, so the selector cannot match the navigation itself — measured:
 * with `data-route` on both, every item lit up.
 *
 * Nothing here decides who is logged in; following any link without a session lands on the login
 * screen, which is what the guard is for.
 */
export default function OperationsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="shell">
      <aside className="shell__side">
        {/* Outside <nav> on purpose: the navigation must contain exactly the declared routes and
            nothing else (tests/layout_routes.test.tsx). The way back to the studio is not a route
            of the operator panel, so it is not in the list either. */}
        <a className="shell__brand" href="/">
          <span aria-hidden="true" className="shell__logo">
            w
          </span>
          <span className="shell__name">Operacje</span>
          <span className="shell__sub">wróć do studia</span>
        </a>

        <nav aria-label="Ekrany panelu operatora" className="shell__nav">
          {NAV_ROUTES.map((route) => (
            <a
              className="shell__nav-item"
              data-nav={route.id}
              data-reads={route.reads ? "tak" : "nie"}
              href={route.href}
              key={route.id}
            >
              <NavIcon routeId={route.id} />
              {route.label}
            </a>
          ))}
        </nav>

        <div className="shell__foot">
          {/* Said on every screen, on purpose. An empty list is a normal state here, not a
              diagnosis, and the operator should not have to remember that. */}
          <p className="shell__note">Ten panel nic nie publikuje.</p>
          {/* Ends the control plane session server-side and drops the local handle. */}
          <form action="/logout" className="shell__logout" method="post">
            <button className="btn btn--ghost btn--small" type="submit">
              Wyloguj
            </button>
          </form>
        </div>
      </aside>

      <main className="shell__main">{children}</main>
    </div>
  );
}
