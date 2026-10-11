import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

/**
 * The root layout, and DELIBERATELY NOTHING MORE THAN THE DOCUMENT.
 *
 * Before the pivot this file owned the operator panel's sidebar, which meant every screen in the
 * package was inside that chrome. D-038 to D-040 make the studio the product and leave the
 * operator panel as one entry inside it, so the two need different shells — and in the App
 * Router a layout can only be escaped by a route group with its own layout. Hence:
 *
 *   app/layout.tsx            ← this file: <html>, <body>, the stylesheet, nothing else
 *   app/(studio)/layout.tsx   ← the studio shell
 *   app/(operations)/layout.tsx ← the operator panel's shell, unchanged in content
 *
 * The parentheses do not appear in the URL, so `/jobs` is still `/jobs`. What changed is which
 * chrome renders around it, and `lib/routes.ts` therefore carries `href` and `file` as two
 * separate fields — after the pivot one is no longer derivable from the other.
 *
 * `/login` renders directly under this layout, with no shell at all. That replaces the CSS that
 * used to hide the sidebar on that one screen: a rule that hides chrome is a rule that can stop
 * matching, and a login screen with the panel's navigation around it invites a logged-out
 * operator to click into six redirects.
 *
 * WHAT IS STILL NOT HERE, AND MUST NOT BE: no `"use client"` anywhere in this package, and no
 * `NEXT_PUBLIC_` variable. D-016 keeps the operator session and the private API's address out of
 * browser code, and `tests/source_scan.test.ts` enforces the absence by scanning the sources.
 */
export const metadata: Metadata = {
  title: "Studio",
  description:
    "Workspace, projekty, profile osób i konta platform. Praca agenta nad powierzonym celem.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="pl">
      <body>{children}</body>
    </html>
  );
}
