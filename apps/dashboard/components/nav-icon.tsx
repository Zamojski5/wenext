/**
 * One line icon per navigation route — 18 px, stroke 1.6, drawn inline so the panel loads no
 * icon font and makes no request it did not make before (docs/DASHBOARD-DESIGN.md §3, §4).
 *
 * Keyed by the route id from `lib/routes.ts`; an unknown id gets the generic dot rather than
 * nothing, so a route added later is visible in the sidebar before somebody draws its icon.
 *
 * NO `<title>` inside the SVG, on purpose: the link's text content must stay exactly the route
 * label (`tests/layout_routes.test.tsx` compares it), and the icon is decorative — the label is
 * the name. Nothing here draws a tick or a check (no success glyphs anywhere on this dashboard).
 */
const PATHS: Readonly<Record<string, readonly string[]>> = {
  // four tiles: the overview
  overview: ["M3.5 3.5h5v5h-5z", "M11.5 3.5h5v5h-5z", "M3.5 11.5h5v5h-5z", "M11.5 11.5h5v5h-5z"],
  // a speech bubble with a smaller one: the community
  community: ["M3.5 4.5h9a1.5 1.5 0 0 1 1.5 1.5v5a1.5 1.5 0 0 1-1.5 1.5H8l-3 2.5v-2.5H3.5A1.5 1.5 0 0 1 2 11V6a1.5 1.5 0 0 1 1.5-1.5z", "M15 8h1.5A1.5 1.5 0 0 1 18 9.5V14a1.5 1.5 0 0 1-1.5 1.5H16V17l-2.5-1.5H11"],
  // three stacked rows with a leading dot: the queue
  jobs: ["M7 5.5h9.5", "M7 10h9.5", "M7 14.5h9.5", "M3.5 5.5h.01", "M3.5 10h.01", "M3.5 14.5h.01"],
  // a page with a clock: the audit log
  audit: ["M5 2.5h7l3 3v11.5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-13.5a1 1 0 0 1 1-1z", "M12 2.5v3h3", "M10 9v3l2 1.2"],
  // two arrows crossing: state transitions
  transitions: ["M3 7h11l-3-3", "M17 13H6l3 3"],
  // a document with a signature line: the approval path
  approvals: ["M5 2.5h10a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-13a1 1 0 0 1 1-1z", "M7 7h6", "M7 10h6", "M7 13.5c1-1.2 2-1.2 3 0s2 1.2 3 0"],
};

const FALLBACK: readonly string[] = ["M10 10h.01"];

export function NavIcon({ routeId }: { readonly routeId: string }) {
  const paths = PATHS[routeId] ?? FALLBACK;
  return (
    <svg
      aria-hidden="true"
      className="shell__nav-icon"
      fill="none"
      focusable="false"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.6"
      viewBox="0 0 20 20"
    >
      {paths.map((d) => (
        <path d={d} key={d} />
      ))}
    </svg>
  );
}
