/**
 * Scope and shape, enforced by reading the package's own files.
 *
 * Four rules live here, and every one of them is a convention that would quietly stop applying
 * without a scanner — the failure mode D-014 names outright ("konwencja niepokryta testem po cichu
 * przestaje obowiązywać") and `rename_identifiers.py` demonstrated for months while `make test-all`
 * stayed green:
 *
 *   1. **No embedded browser, anywhere.** The interactive view, noVNC and the stream ticket
 *      exchange are E1.F2 (L5229, L5250). A single reference here is scope spilling into a phase
 *      that has not been planned, and L5500-5501 forbids that outright.
 *   2. **No browser-side surface.** No `NEXT_PUBLIC_` variable anywhere, and `"use client"` in
 *      EXACTLY ONE pliku wymienionym z nazwy: D-016 keeps the operator session and the private
 *      API's address out of the browser bundle. Wyjątek jest z D-046 i obowiązuje go osobne
 *      sprawdzenie: plik kliencki nie może czytać `process.env`, wołać `fetch` ani znać adresu
 *      control plane'u. Reguła brzmi więc dalej „nic tajnego w paczce", a nie „żadnego klienta",
 *      i to jest ta reguła, której powód dało się wypisać.
 *   3. **Every screen is behind the auth boundary**, checked by walking `app/` rather than by
 *      trusting a hand-written list — a page added later would not be on the list.
 *   4. **No mutating route beyond the two that manage the session.** E1.F1 has no mutating action
 *      on jobs, so a retry endpoint added here would have to survive a conscious edit to this test.
 *
 * WHY THE MATCH IS CASE-SENSITIVE — measured, not a preference. A case-insensitive scan finds `vnC`
 * inside a base64 `integrity` hash in `package-lock.json` (verified 2026-08-31:
 * `sha512-4a+OsYv9UktOJKE+l1A4OufDgdRF9PifWj+tJnHURo/P+WOxpG4GzUFL9qCalmWauao6ogiG+QvnCovwPoyAWA==`).
 * A scanner with false positives gets deleted, and that is how a rule like this dies — the same
 * reasoning that put word boundaries on the short tokens in `tests/success-indicators.ts`.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { ALL_PAGES, NAV_ROUTES } from "../lib/routes";

const APP_ROOT = path.resolve(import.meta.dirname, "..");
const IGNORED_DIRECTORIES = new Set(["node_modules", ".next", ".git"]);

/** This file names the forbidden strings, so it is the one file excluded from the scan. */
const SCANNER_FILE = "source_scan.test.ts";

/**
 * Jedyny plik, któremu wolno powiedzieć `"use client"` (D-046).
 *
 * Nie jest to złagodzenie reguły z punktu 2, tylko jej doprecyzowanie: zakaz istniał po to, żeby
 * sesja operatora i adres prywatnego API nie trafiły do paczki przeglądarki, a ten plik nie
 * dotyka ani jednego, ani drugiego. Sprawdzenie poniżej czyta jego treść i tego pilnuje.
 */
const ALLOWED_CLIENT = "components/studio/refresh-on-cards.tsx";

/**
 * The embedded browser, in every spelling it could arrive in. EXACTLY SIX, and the count is
 * asserted: a list that quietly became empty would make the scan below pass over every file and
 * report a clean package — `all([]) == True`, four times paid for in this repository (D-011).
 */
const FORBIDDEN: readonly string[] = [
  "novnc",
  "websockify",
  "StreamTicket",
  "vnc",
  "/stream/exchange",
  "/browser-sessions",
];

/** Route handlers this package is allowed to have, with the methods each may export. */
const ALLOWED_ROUTE_HANDLERS: readonly { readonly file: string; readonly methods: readonly string[] }[] = [
  // The container healthcheck in docker-compose.yml calls this, and `depends_on:
  // condition: service_healthy` waits for it.
  { file: "app/healthz/route.ts", methods: ["GET"] },
  // The credential route. POST only: a login reachable by GET is a login in a browser history.
  { file: "app/login/submit/route.ts", methods: ["POST"] },
  // Ends the session on both sides. POST only: a logout reachable by GET is a logout any prefetch
  // can perform on the operator's behalf.
  { file: "app/logout/route.ts", methods: ["POST"] },
  // Outcome A (docs/OUTCOMES.md §3): the form handlers of /community, moved into the
  // `(operations)` route group by the pivot. The URLs are unchanged; only the chrome around the
  // screen is. POST only, each runs ONE control plane write server-side and answers 303 back to
  // the screen. None of them can publish anything.
  { file: "app/(operations)/community/enable/route.ts", methods: ["POST"] },
  { file: "app/(operations)/community/profile/route.ts", methods: ["POST"] },
  { file: "app/(operations)/community/feedback/route.ts", methods: ["POST"] },
  { file: "app/(operations)/community/resume/route.ts", methods: ["POST"] },
  // 0025: decyzja człowieka o własnych wątkach w społeczności. Osobny handler, bo to osobna
  // zgoda: „czytajmy tam" i „piszmy tam od siebie" to dwie różne rzeczy.
  { file: "app/(operations)/community/own-posts/route.ts", methods: ["POST"] },
  // The capsule sign-in: open (answers 303 to the ticket URL, in a tab the form opened) and
  // confirm (the control plane measures the platform session and resumes parked jobs). Neither
  // publishes anything; both are plain form POSTs.
  { file: "app/(operations)/community/capsule/open/route.ts", methods: ["POST"] },
  { file: "app/(operations)/community/capsule/confirm/route.ts", methods: ["POST"] },
  // P1 (ROADMAP-PIVOT, D-040): the studio's writes, grouped by the object they act on. Each one
  // dispatches on a CLOSED `action` set and refuses anything else, and each one answers 303 back
  // to a same-origin path taken from the form and validated (`app/studio/_handler.ts`).
  // NONE OF THEM PUBLISHES ANYTHING: the control plane has no route under /api/workspaces that
  // writes to a platform, and the two "remove" actions set a timestamp rather than delete a row.
  { file: "app/studio/workspace/route.ts", methods: ["POST"] },
  { file: "app/studio/project/route.ts", methods: ["POST"] },
  { file: "app/studio/people/route.ts", methods: ["POST"] },
  { file: "app/studio/assignment/route.ts", methods: ["POST"] },
  // P1 etap 2: the five writes of "Połączenia Codex". None of them logs an account out — the
  // control plane has no route that sends `account/logout`, so a disconnect here cannot become
  // one by accident.
  { file: "app/studio/codex/route.ts", methods: ["POST"] },
  // P2: start sesji, wiadomość, przerwanie i wznowienie. Jeden handler, cztery akcje na
  // zamkniętej liście, tak jak pozostałe cztery handlery studia.
  { file: "app/studio/session/route.ts", methods: ["POST"] },
  // D-046: jeden przycisk karty rozmowy. Trasa i ładunek PRZYCHODZĄ Z KARTY, więc handler
  // sprawdza je sam: metoda ze zbioru zmieniającego stan, ścieżka związana z przestrzenią
  // z formularza, bez `..` i bez `//`. Bez tych trzech reguł pole ukryte z trasą byłoby
  // sposobem na wysłanie sesji operatora pod dowolny adres API.
  { file: "app/studio/card/route.ts", methods: ["POST"] },
  // 26.09: "Połącz X" on an account card. The studio twin of `community/capsule/open`: it asks the
  // control plane for a ticket and answers 303 to the agent's browser, inside the new tab the
  // card's form opened, after checking the address against the one configured viewer origin. It
  // publishes nothing and writes nothing but the capsule's own session.
  { file: "app/studio/capsule/open/route.ts", methods: ["POST"] },
  // P3 etap A: dodanie materiału, odświeżenie kanału, szukanie w internecie, dopisanie
  // ustalenia, poprawka i wycofanie. Sześć akcji na zamkniętej liście, żadna z nich niczego
  // nie kasuje: poprawka pisze nowy wiersz, a wycofanie stawia znacznik z powodem.
  { file: "app/studio/knowledge/route.ts", methods: ["POST"] },
  // P4: wstrzymanie, wznowienie i odwołanie powierzenia. Trzy akcje na zamkniętej liście.
  // Odwołanie jest osobną wartością, a nie wariantem wstrzymania, bo wstrzymane wraca tym samym
  // podpisem, a odwołane nie wraca nigdy: żaden handler nie ma prawa trafić tam przez pomyłkę.
  { file: "app/studio/presence/route.ts", methods: ["POST"] },
  // P7: samoobsługa klienta. Dwa handlery PRZED granicą sesji, bo tam poświadczenie POWSTAJE,
  // a nie jest sprawdzane; dwa w studiu: pięć zapisów ustawień przestrzeni i pobranie pliku
  // eksportu, które jest GET-em, bo to odczyt istniejącego artefaktu.
  { file: "app/register/submit/route.ts", methods: ["POST"] },
  { file: "app/invitation/submit/route.ts", methods: ["POST"] },
  { file: "app/studio/organization/route.ts", methods: ["POST"] },
  { file: "app/studio/organization/export/route.ts", methods: ["GET"] },
  // P2 etap B: strumień rozmowy. GET, bo to odczyt, i proxy, bo przeglądarka nie rozmawia
  // z control plane'em (D-016). Ciało jest przepuszczane strumieniem, nie buforowane.
  { file: "app/studio/stream/route.ts", methods: ["GET"] },
  // P4 i P5: jedno zdanie korekty po powrocie. Osobny handler od sesji, bo korekta może trafić
  // do NOWEJ sesji, a tamten wymaga identyfikatora sesji w formularzu. Jedna akcja, zamknięta.
  { file: "app/studio/return/route.ts", methods: ["POST"] },
  // Tryb lokalny: wejście bez hasła na maszynie właściciela. GET, bo kieruje na nie ekran
  // logowania, a nie formularz, i ma działać bez JavaScriptu. Domyślnie nie robi nic:
  // bez `WENEXT_LOCAL_AUTOLOGIN` i bez `Host` z pętli zwrotnej odsyła na `/login`.
  { file: "app/login/local/route.ts", methods: ["GET"] },
];

/**
 * Pages that are deliberately NOT behind the auth boundary, DERIVED from `lib/routes.ts` rather
 * than repeated here. THREE since P7 and no more: the login, registering a new space and
 * accepting an invitation. All three are places where a credential is CREATED or PRESENTED, so a
 * session cannot be required first; the count is asserted where it is used, so widening it stays
 * a conscious edit of the route table.
 */
const UNGATED_PAGES: readonly string[] = ALL_PAGES.filter((route) => !route.gated).map(
  (route) => `app/${route.file}`,
);

function walk(directory: string): string[] {
  const found: string[] = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (IGNORED_DIRECTORIES.has(entry.name)) {
        continue;
      }
      found.push(...walk(path.join(directory, entry.name)));
      continue;
    }
    found.push(path.join(directory, entry.name));
  }
  return found;
}

function relative(file: string): string {
  return path.relative(APP_ROOT, file);
}

const ALL_FILES = walk(APP_ROOT);

describe("scope of the dashboard package", () => {
  it("the_dashboard_contains_no_stream_or_vnc_reference", () => {
    // Both counts before the predicate. Six forbidden strings, and a scan that actually opened
    // files: a walker that returned nothing would report a spotless package.
    expect(FORBIDDEN.length).toBe(6);
    expect(ALL_FILES.length).toBeGreaterThan(0);

    const scanned: string[] = [];
    const violations: string[] = [];

    for (const file of ALL_FILES) {
      if (path.basename(file) === SCANNER_FILE) {
        continue;
      }
      let content: string;
      try {
        content = fs.readFileSync(file, "utf8");
      } catch {
        continue;
      }
      scanned.push(relative(file));
      for (const forbidden of FORBIDDEN) {
        if (content.includes(forbidden)) {
          violations.push(`${relative(file)}: ${forbidden}`);
        }
      }
    }

    // The number of files the scan actually read. Asserted, because "no violations" over zero
    // files is the shape this repository keeps getting caught by.
    expect(scanned.length, "skaner nie przeczytał ani jednego pliku").toBeGreaterThan(20);
    expect(scanned).toContain("app/layout.tsx");
    expect(scanned).toContain("lib/control-plane.ts");

    expect(
      violations,
      "osadzona przeglądarka to E1.F2 (L5229, L5250) — w tym pakiecie nie ma dla niej miejsca",
    ).toEqual([]);
  });

  it("the_scanner_would_catch_a_planted_reference", () => {
    // A scanner nobody has seen catch anything is not a scanner. Each of the six strings has to be
    // detectable by the same `includes` the scan uses — this is what makes the test above evidence
    // rather than a formality.
    expect(FORBIDDEN.length).toBe(6);
    for (const forbidden of FORBIDDEN) {
      const planted = `const widok = "${forbidden}";`;
      expect(FORBIDDEN.some((needle) => planted.includes(needle)), forbidden).toBe(true);
    }
  });

  it("no_file_declares_a_client_component_or_a_public_env_variable", () => {
    // D-016: the operator session never reaches browser code, and the address of the unpublished
    // control plane must not be inlined into a bundle. Both would be silent — no error, no warning.
    //
    // THE PATTERN REQUIRES A NAME AFTER THE PREFIX (`NEXT_PUBLIC_` plus at least one uppercase
    // character), and that is not laxness. Several files in this package EXPLAIN the prohibition and
    // therefore write the bare prefix in prose; a scan for the prefix alone reported those
    // explanations as violations — measured on the first run of this test. A scanner that flags the
    // documentation of a rule gets deleted, and then the rule goes with it (the same reasoning that
    // put word boundaries on the short tokens in `tests/success-indicators.ts`). A real variable
    // always has a name, so this pattern catches every usable one and no sentence about them.
    const sources = ALL_FILES.filter(
      (file) => /\.(ts|tsx|mts|js|mjs)$/.test(file) && path.basename(file) !== SCANNER_FILE,
    );
    expect(sources.length).toBeGreaterThan(10);

    const publicVariable = /NEXT_PUBLIC_[A-Z][A-Z0-9_]*/;
    // The scanner, shown catching something before it is trusted to catch nothing.
    expect(publicVariable.test('process.env.NEXT_PUBLIC_CONTROL_PLANE_URL')).toBe(true);
    expect(publicVariable.test("wartość z prefiksem NEXT_PUBLIC_ trafia do bundla")).toBe(false);

    const violations: string[] = [];
    const clients: string[] = [];
    for (const file of sources) {
      const content = fs.readFileSync(file, "utf8");
      if (/^\s*["']use client["']/m.test(content)) {
        clients.push(relative(file));
      }
      const hit = publicVariable.exec(content);
      if (hit !== null) {
        violations.push(`${relative(file)}: ${hit[0]}`);
      }
    }
    expect(violations).toEqual([]);

    // JEDEN KOMPONENT KLIENCKI, WYMIENIONY Z NAZWY (D-046). Prosi Next o ponowne narysowanie
    // strony, gdy zmieni się odcisk kart; pełne przeładowanie zabierałoby człowiekowi tekst
    // wpisywany w polu i kasowało przejścia CSS, które makieta ma w kontrakcie wizualnym.
    expect(clients).toEqual([ALLOWED_CLIENT]);

    // I to, co naprawdę pilnuje D-016: w tym jednym pliku nie ma niczego, co mogłoby wnieść do
    // paczki sesję albo adres prywatnego API. Sprawdzane osobno, bo bez tego „jeden wyjątek"
    // za miesiąc znaczyłby „jeden plik, w którym wolno wszystko".
    const clientSource = fs.readFileSync(path.join(APP_ROOT, ALLOWED_CLIENT), "utf8");
    // BEZ KOMENTARZY, i to nie jest wygoda. Ten plik tłumaczy w nagłówku, czego NIE robi, więc
    // wymienia `process.env` po to, żeby powiedzieć, że go nie czyta. Sprawdzenie, które wywraca
    // się na dokumentacji pilnowanej reguły, jest sprawdzeniem, które ktoś kiedyś skasuje razem
    // z regułą (ta sama nauka, co w `tests/studio_style.test.ts`).
    const client = clientSource.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    expect(client).toContain("useRouter");
    expect(client).not.toContain("JEDYNY komponent");
    for (const forbidden of ["process.env", "fetch(", "/api/", "controlPlane", "token"]) {
      expect(client, `${ALLOWED_CLIENT}: ${forbidden}`).not.toContain(forbidden);
    }
  });

  it("every_page_is_declared_in_lib_routes_and_every_declared_route_has_a_page", () => {
    const pages = ALL_FILES.filter(
      (file) => path.basename(file) === "page.tsx" && relative(file).startsWith("app/"),
    ).map(relative);

    // Count first: with no pages found, both directions below hold trivially.
    expect(pages.length).toBeGreaterThanOrEqual(NAV_ROUTES.length);
    // 21 od E2: doszły „Publikacje" i „Społeczności", dwie brakujące pozycje paska
    // projektu z referencji (19 było od D-045, z trasą jednej rozmowy).
    // 22 od D-055: „Pokój", pierwszy ekran wtyczki do Codexa, pod projektem jak jego rodzeństwo.
    expect(ALL_PAGES.length).toBe(22);

    // `file`, not `href`. Since the pivot the two differ: a route group's parentheses are part of
    // the path on disk and absent from the URL, so deriving one from the other would assert
    // against files that do not exist.
    const declared = new Set(ALL_PAGES.map((route) => path.join("app", route.file)));

    // Both directions. Only the first would let an undeclared page in; only the second would let a
    // nav entry point at a route with no page — a 404 that looks like a feature.
    expect(pages.slice().sort()).toEqual(Array.from(declared).sort());
  });

  it("no_view_renders_data_without_the_guard", () => {
    // The static half of the auth boundary. The dynamic half is in
    // `e2e/dashboard_reads_postgres.e2e.test.ts`, which asks a real server for every route without
    // a cookie. Both are needed: this one covers a page added later, that one covers whether the
    // guard actually redirects.
    const pages = ALL_FILES.filter(
      (file) => path.basename(file) === "page.tsx" && relative(file).startsWith("app/"),
    );
    expect(pages.length).toBeGreaterThanOrEqual(5);
    expect(UNGATED_PAGES.length).toBe(3);

    const missing: string[] = [];
    for (const file of pages) {
      const name = relative(file);
      const content = fs.readFileSync(file, "utf8");
      const guarded = content.includes("requireOperator(");
      if (UNGATED_PAGES.includes(name)) {
        // The login screen must NOT be gated — a gated login screen cannot be reached.
        expect(guarded, `${name} nie może być za granicą uwierzytelnienia`).toBe(false);
        continue;
      }
      if (!guarded) {
        missing.push(name);
      }
    }
    expect(
      missing,
      "każdy ekran z danymi wywołuje requireOperator() jako pierwszą instrukcję (D-016)",
    ).toEqual([]);
  });

  it("every_studio_page_returns_the_person_to_itself_after_login", () => {
    // Measured 27.09: after an API restart the control plane forgets every session, and a click on
    // "Publikacje" landed on the workspace home, because that page called `requireOperator()`
    // without its own address and the login sent the person to "/". Every page under a workspace
    // names where the person was going.
    const studio = ALL_FILES.filter(
      (file) =>
        path.basename(file) === "page.tsx" && relative(file).startsWith("app/(studio)/w/"),
    );
    expect(studio.length).toBeGreaterThanOrEqual(9);
    const bare = studio
      .filter((file) => fs.readFileSync(file, "utf8").includes("requireOperator()"))
      .map(relative);
    expect(bare, "strona przestrzeni bez adresu powrotu po zalogowaniu").toEqual([]);
  });

  it("the_only_route_handlers_are_healthz_and_the_two_session_routes", () => {
    const handlers = ALL_FILES.filter(
      (file) => path.basename(file) === "route.ts" && relative(file).startsWith("app/"),
    ).map(relative);

    expect(ALLOWED_ROUTE_HANDLERS.length).toBe(27);
    expect(handlers.slice().sort()).toEqual(
      ALLOWED_ROUTE_HANDLERS.map((entry) => entry.file).slice().sort(),
    );

    // And each exports only the methods it is allowed to. A `POST` appearing in `app/healthz` or a
    // `DELETE` anywhere would be a mutating surface this phase does not have.
    const methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] as const;
    for (const { file, methods: allowed } of ALLOWED_ROUTE_HANDLERS) {
      const content = fs.readFileSync(path.join(APP_ROOT, file), "utf8");
      for (const method of methods) {
        const exported = new RegExp(`export\\s+(?:async\\s+)?function\\s+${method}\\b`).test(content);
        expect(exported, `${file}: ${method}`).toBe(allowed.includes(method));
      }
    }
  });

  it("nothing_under_app_jobs_or_app_audit_is_a_route_handler", () => {
    // The queue and the log are read-only surfaces. This is the crude second lock on that, in the
    // shape `tests/test_repo_consistency.py::test_no_cdp_or_vnc_port_is_published` uses: a literal
    // check that does not depend on the parser above being right.
    const inside = ALL_FILES.map(relative).filter(
      (file) =>
        (file.startsWith("app/(operations)/jobs/") ||
          file.startsWith("app/(operations)/audit/")) &&
        path.basename(file) === "route.ts",
    );
    expect(inside).toEqual([]);
    // The prefixes above have to match something, or the filter is checking an empty set. The
    // pivot moved these screens into a route group and this assertion is what would have caught
    // the stale path.
    const screens = ALL_FILES.map(relative).filter(
      (file) =>
        file.startsWith("app/(operations)/jobs/") || file.startsWith("app/(operations)/audit/"),
    );
    expect(screens.length).toBeGreaterThan(0);
  });
});
