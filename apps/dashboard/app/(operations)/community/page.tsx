import { CommunityView, type PictureFor } from "../../../components/views/community-view";
import { CONTROL_PLANE_CONCURRENCY, mapWithConcurrency } from "../../../lib/concurrency";
import {
  getCapsule,
  getCommunities,
  getJudgments,
  getLatestPicture,
  getUserProfile,
  type Read,
} from "../../../lib/control-plane";
import { requireOperator } from "../../../lib/guard";

/**
 * `GET /community` — Outcome A on one screen. Five reads, each carried to the view with its own
 * `Read<T>` so a failure in one section is reported in that section and nowhere else.
 *
 * `force-dynamic` because a prerendered picture of a community is a picture of the build.
 *
 * `?info=` carries one sentence from the route handlers under `app/community/` after a form POST
 * (303 back here). It is text the SERVER wrote, never something the browser typed: the handlers
 * pick a sentence from their own closed set, so an operator cannot be shown a message somebody
 * else put in a link.
 */
export const dynamic = "force-dynamic";

const FLASHES: Record<string, string> = {
  enabled: "Obserwacja włączona; pierwszy odczyt jest w kolejce jobów.",
  enable_refused: "Control plane odrzucił community. Sprawdź platformę i klucz (np. `ask` albo `r/devops`).",
  profile_saved: "Profil zapisany jako nowa wersja; osądy nieocenionych wątków zostały zlecone.",
  profile_refused: "Control plane odrzucił profil. Każde z trzech pierwszych pól musi mieć treść.",
  feedback_saved: "Odpowiedź zapisana obok osądu.",
  feedback_refused: "Control plane odrzucił odpowiedź. Osąd nie istnieje albo werdykt jest nieznany.",
  resumed: "Joby czekające na człowieka wróciły do kolejki.",
  own_posts_saved:
    "Decyzja o własnych wątkach zapisana. Wpis w dzienniku mówi, kto i kiedy ją podjął.",
  own_posts_refused:
    "Control plane odrzucił decyzję o własnych wątkach. Odśwież ekran i spróbuj jeszcze raz.",
  capsule_signed_in: "Sprawdzone: jesteś zalogowany. Zatrzymane zadania wróciły do kolejki.",
  capsule_signed_out:
    "Sprawdzone: sesja nie jest zalogowana. Otwórz przeglądarkę agenta i zaloguj się w niej jeszcze raz.",
  capsule_open_refused: "Nie udało się otworzyć przeglądarki agenta. Szczegóły na karcie poniżej.",
  capsule_confirm_failed: "Sprawdzenie logowania nie doszło do skutku. Nic nie zostało zmienione.",
  capsule_confirm_pending:
    "Potwierdzenie trwa dłużej niż zwykle. Odśwież za chwilę. Stan karty pokaże wynik.",
  capsule_open_blocked:
    "Adres przeglądarki agenta nie pasuje do skonfigurowanego. Panel nie otworzył go celowo.",
  unreachable: "Nie udało się porozmawiać z control plane. Zapis NIE nastąpił.",
};

export default async function CommunityPage({
  searchParams,
}: {
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const gate = await requireOperator();
  const now = new Date();
  const parameters = await searchParams;
  const info = parameters["info"];
  const flash = typeof info === "string" && info in FLASHES ? (FLASHES[info] ?? null) : null;

  if (gate.kind === "unreachable") {
    const failed: Read<never> = { ok: false, unauthorized: false, reason: gate.reason };
    return (
      <CommunityView
        capsule={failed}
        communities={failed}
        engage={failed}
        flash={flash}
        now={now}
        pictures={[]}
        profile={failed}
        recentIgnored={failed}
      />
    );
  }

  const [communities, profile, engage, recentIgnored, capsule] = await Promise.all([
    getCommunities(gate.token),
    getUserProfile(gate.token),
    getJudgments(gate.token, "ENGAGE"),
    getJudgments(gate.token, "IGNORE"),
    // Its own read, like every other section: a capsule the control plane cannot describe must not
    // make the judgments look empty, and vice versa.
    getCapsule(gate.token),
  ]);

  const pictures: readonly PictureFor[] = communities.ok
    ? await mapWithConcurrency(communities.value, CONTROL_PLANE_CONCURRENCY, async (community) => ({
        community,
        picture: await getLatestPicture(gate.token, community.community_id),
      }))
    : [];

  return (
    <CommunityView
      capsule={capsule}
      communities={communities}
      engage={engage}
      flash={flash}
      now={now}
      pictures={pictures}
      profile={profile}
      recentIgnored={recentIgnored}
    />
  );
}
