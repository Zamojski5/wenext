import type { ReactNode } from "react";

import {
  DraftsBlock,
  EmptyRoom,
  OpportunitiesBlock,
  ResultsBlock,
  RoomHero,
  ScopeNote,
} from "../../../../../../../components/studio/room";
import { StudioShell } from "../../../../../../../components/studio/shell";
import { StudioMissing, StudioUnreachable } from "../../../../../../../components/studio/states";
import { getProject } from "../../../../../../../lib/control-plane";
import { projectConversations } from "../../../../../../../lib/conversations";
import { requireOperator } from "../../../../../../../lib/guard";
import { getPresence } from "../../../../../../../lib/presence-client";
import { getRoom } from "../../../../../../../lib/room-client";
import type { Room } from "../../../../../../../lib/room-schema";
import { draftStateOf } from "../../../../../../../lib/room-words";
import { presenceHref, roomHref } from "../../../../../../../lib/routes";
import { loadStudioFrame } from "../../../../../../../lib/studio-frame";

/**
 * `GET /w/<workspace>/p/<projekt>/room`: "Pokój", the screen the founder opens from Codex (D-055).
 *
 * THE ORDER IS THE DEMO. A sentence that makes the promise with the live count in it; then the
 * replies that are ready, drawn as the posts they will become, each under the thread it answers;
 * then the people those replies are for; then what already went out, in numbers. Ready replies
 * come before the list of people because a founder who has just said "znajdź moich pierwszych
 * użytkowników" wants to see the answer to that sentence first, and the list of people is its
 * evidence.
 *
 * A FAILED READ IS NOT AN EMPTY ROOM. The empty state makes a promise ("tu pojawią się ludzie"),
 * which is true of a project nobody searched for, and a lie about one whose read just failed or
 * whose control plane has no such route yet. The two are different branches below and say
 * different things.
 *
 * WHAT THE SCREEN DOES NOT KNOW AND ASKS ABOUT: whether the project may publish at all. A second
 * read (`getPresence`) tells it whether a signed scope is live, so the person is told to sign one
 * BEFORE the click that would be refused; if that read fails the note is simply not drawn, and the
 * refusal after the click carries the same explanation (`lib/room-actions.ts`).
 *
 * `force-dynamic`: a cached room would be a second opinion about who is writing right now.
 */
export const dynamic = "force-dynamic";

function isEmpty(room: Room): boolean {
  return (
    room.opportunities.length === 0 &&
    room.drafts.length === 0 &&
    room.results.items.length === 0 &&
    (room.results.published ?? 0) === 0
  );
}

export default async function RoomPage({
  params,
  searchParams,
}: {
  readonly params: Promise<{ readonly workspaceId: string; readonly projectId: string }>;
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { workspaceId, projectId } = await params;
  const parameters = await searchParams;
  const rawInfo = parameters["info"];
  const info = typeof rawInfo === "string" ? rawInfo : undefined;

  const gate = await requireOperator(roomHref(workspaceId, projectId));
  if (gate.kind === "unreachable") return <StudioUnreachable reason={gate.reason} />;

  const frame = await loadStudioFrame(gate.token, workspaceId);
  if (!frame.ok) return <StudioUnreachable reason={frame.reason} />;
  if (frame.value === null) return <StudioMissing what="workspace" />;
  const studio = frame.value;

  const detail = await getProject(gate.token, workspaceId, projectId);
  if (!detail.ok) return <StudioUnreachable reason={detail.reason} />;
  if (detail.value === null) return <StudioMissing what="projekt" />;
  const project = detail.value.project;
  const assigned = detail.value.assignments.filter((one) => one.active);
  // Rozmowy TYLKO dla paska, jak na sąsiednich ekranach projektu.
  const conversations = await projectConversations(gate.token, workspaceId, projectId);

  const room = await getRoom(gate.token, workspaceId, projectId);
  const presence = await getPresence(gate.token, workspaceId, projectId, 1);
  const here = roomHref(workspaceId, projectId);
  const now = new Date();

  const delegationLive = presence.ok ? presence.value.delegation?.live === true : null;

  const shell = (children: ReactNode) => (
    <StudioShell
      active={{ kind: "room", projectId }}
      conversations={conversations}
      crumb={`${studio.workspace.name} / ${project.name} / Pokój`}
      frame={studio}
      info={info}
      mode={project.mode}
    >
      <div className="st-room" data-room-state={room.ok ? (isEmpty(room.value) ? "empty" : "full") : "failed"}>
        {children}
      </div>
    </StudioShell>
  );

  if (!room.ok) {
    return shell(
      <>
        <h1 className="st__title">Pokój</h1>
        <p className="st__lede">{project.name}</p>
        {/* Nieudany odczyt to NIE pusty pokój i zdanie mówi to wprost. */}
        <p className="st-tag" data-tone="absent">
          Nie udało się odczytać Pokoju: {room.reason}
        </p>
        <p className="st-note">
          To nie znaczy, że nikt nie pisze o Twoim problemie: ten ekran po prostu nic o tym nie
          wie. <a href={here}>Spróbuj jeszcze raz</a>.
        </p>
      </>,
    );
  }

  const value = room.value;
  if (isEmpty(value)) {
    return shell(<EmptyRoom projectName={project.name} />);
  }

  const waiting = value.drafts.some((draft) => draftStateOf(draft).actionable);
  const refused = info === "room_scope_unsigned";
  const scope =
    refused || (waiting && delegationLive === false) ? (
      <ScopeNote presenceHref={presenceHref(workspaceId, projectId)} refused={refused} />
    ) : null;

  return shell(
    <>
      <RoomHero now={now} refreshHref={here} room={value} />
      <DraftsBlock
        assigned={assigned}
        now={now}
        projectId={projectId}
        returnTo={here}
        room={value}
        scope={scope}
        workspaceId={workspaceId}
      />
      <OpportunitiesBlock
        now={now}
        projectId={projectId}
        returnTo={here}
        room={value}
        workspaceId={workspaceId}
      />
      <ResultsBlock now={now} results={value.results} />
    </>,
  );
}
