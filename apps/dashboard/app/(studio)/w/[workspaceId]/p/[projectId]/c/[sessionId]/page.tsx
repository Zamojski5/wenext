/**
 * `GET /w/<workspace>/p/<project>/c/<session>`: JEDNA rozmowa projektu (D-045).
 *
 * PO CO OSOBNY ADRES. Do 20.09 strona projektu pokazywała NAJNOWSZĄ rozmowę i tylko ją. Dwie
 * rozmowy jednego projektu były wtedy nie do rozróżnienia linkiem, starsza znikała z ekranu w
 * chwili, gdy ktoś zaczął nową, a „wróć do tamtej rozmowy" nie dało się zrobić inaczej niż
 * pamięcią. D-045 mówi, że projekt jest ZBIOREM rozmów, więc rozmowa dostaje własny adres, a
 * strona projektu przestaje być jedną z nich.
 *
 * TEN EKRAN NIE ZMIENIA WIDOKU ROZMOWY. `components/studio/conversation.tsx` i strumień na żywo
 * są te same, co przedtem; zmienia się tylko to, skąd bierze się sesja: z trasy, a nie z
 * „pierwszej z listy". Widok rozmowy przeszedł 20.09 przeróbkę czytelności po uwadze
 * założyciela i przepisywanie go tutaj oznaczałoby zrobienie tej pracy drugi raz.
 *
 * CZEGO TU NIE MA: kart projektu, dokumentów, wyników i powrotu. One opisują PROJEKT, nie tę
 * rozmowę, i po D-045 mieszkają na stronie projektu oraz w pozycjach „W projekcie" w pasku.
 * Powtórzone tutaj zamieniłyby rozmowę w drugi pulpit.
 */
import { Conversation } from "../../../../../../../../components/studio/conversation";
import { ConversationLive } from "../../../../../../../../components/studio/conversation-live";
import { cardsDigestOf } from "../../../../../../../../components/studio/cards";
import { RefreshOnCards } from "../../../../../../../../components/studio/refresh-on-cards";
import { StudioShell } from "../../../../../../../../components/studio/shell";
import {
  StudioMissing,
  StudioUnreachable,
} from "../../../../../../../../components/studio/states";
import { getProject } from "../../../../../../../../lib/control-plane";
import { conversationsOf, conversationTitle } from "../../../../../../../../lib/conversations";
import { requireOperator } from "../../../../../../../../lib/guard";
import { conversationHref } from "../../../../../../../../lib/routes";
import {
  getRecentConversations,
  getSessionDetail,
  getSessions,
} from "../../../../../../../../lib/sessions-client";
import { loadStudioFrame } from "../../../../../../../../lib/studio-frame";

export const dynamic = "force-dynamic";

export default async function ConversationPage({
  params,
  searchParams,
}: {
  readonly params: Promise<{
    readonly workspaceId: string;
    readonly projectId: string;
    readonly sessionId: string;
  }>;
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { workspaceId, projectId, sessionId } = await params;
  const parameters = await searchParams;
  const rawInfo = parameters["info"];
  const info = typeof rawInfo === "string" ? rawInfo : undefined;

  const gate = await requireOperator(conversationHref(workspaceId, projectId, sessionId));
  if (gate.kind === "unreachable") {
    return <StudioUnreachable reason={gate.reason} />;
  }

  const frame = await loadStudioFrame(gate.token, workspaceId);
  if (!frame.ok) return <StudioUnreachable reason={frame.reason} />;
  if (frame.value === null) return <StudioMissing what="workspace" />;

  const detail = await getProject(gate.token, workspaceId, projectId);
  if (!detail.ok) return <StudioUnreachable reason={detail.reason} />;
  if (detail.value === null) return <StudioMissing what="projekt" />;
  const { project } = detail.value;

  const conversation = await getSessionDetail(gate.token, workspaceId, projectId, sessionId);
  if (!conversation.ok) return <StudioUnreachable reason={conversation.reason} />;
  // Rozmowa z cudzej organizacji jest tu nie do odróżnienia od nieistniejącej, tak jak w API.
  if (conversation.value === null) return <StudioMissing what="rozmowa" />;

  // Pasek: rozmowy tego projektu i ostatnie z całej przestrzeni. Oba odczyty są po to, żeby z
  // otwartej rozmowy dało się przejść do innej bez wracania na stronę projektu.
  const sessions = await getSessions(gate.token, workspaceId, projectId, true);
  const conversations = sessions.ok ? conversationsOf(sessions.value) : undefined;
  const recent = await getRecentConversations(gate.token, workspaceId);

  const here = conversationHref(workspaceId, projectId, sessionId);
  const title = conversationTitle(conversation.value.session);

  return (
    <StudioShell
      active={{ kind: "conversation", projectId, sessionId }}
      conversations={conversations}
      crumb={`${frame.value.workspace.name} / ${project.name} / ${title}`}
      frame={frame.value}
      info={info && ["session_started", "session_message", "session_resumed"].includes(info) ? undefined : info}
      recent={recent.ok ? recent.value : null}
    >
      <RefreshOnCards />
      <Conversation
        detail={conversation.value}
        projectId={projectId}
        returnTo={here}
        workspaceId={workspaceId}
      >
        <ConversationLive
          afterSeq={conversation.value.last_seq}
          cardsDigest={cardsDigestOf(conversation.value.karty ?? [])}
          projectId={projectId}
          sessionId={sessionId}
          status={conversation.value.session.status}
          workspaceId={workspaceId}
        />
      </Conversation>
    </StudioShell>
  );
}
