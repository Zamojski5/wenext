import type { ReactNode } from "react";

import "../studio.css";
import "../frame.css";
import "../chat.css";
import "../room.css";

/**
 * The studio's route group. It adds the two stylesheets and NOTHING ELSE.
 *
 * `room.css` is the "Pokój" screen (D-055) and only adds classes (`st-room-*`); it redefines no
 * token, so its place after `chat.css` is not load-bearing.
 *
 * `chat.css` jest osobnym plikiem, bo jest przepisany 1:1 z makiet D-046 i przedefiniowuje
 * tokeny o tych samych nazwach, co `studio.css`, pod korzeniem widoku rozmowy. Kolejność ma
 * znaczenie: chat po studio, żeby to przedefiniowanie wygrywało w swoim zakresie.
 *
 * The visible shell is a component (`components/studio/shell.tsx`) that every screen renders for
 * itself, rather than this layout. The reason is in `lib/studio-frame.ts`: a layout only receives
 * the dynamic segments of its own path, so one here would not know the workspace and one at
 * `w/[workspaceId]` would not know which project is open — it would draw the sidebar and be
 * unable to mark where you are. A sidebar that is sometimes wrong about the current screen is
 * worse than two extra reads on loopback.
 *
 * The parentheses keep this group out of the URL: the page below is `/`, not `/(studio)`.
 */
export default function StudioLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
