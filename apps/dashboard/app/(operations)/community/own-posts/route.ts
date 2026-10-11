/**
 * `POST /community/own-posts` — czy w tej społeczności wolno zakładać NASZE własne wątki (0025).
 *
 * TRZY STANY, ZAMKNIĘTY ZBIÓR, ODMAWIAJĄCY DOMYŚLNY. Pole `allowed` przychodzi jako napis z
 * formularza i wolno mu być dokładnie jednym z trzech: „yes", „no", „undecided". Wszystko inne
 * to odmowa, bo pomyłka w tym polu byłaby zgodą na publikowanie w cudzej społeczności wydaną
 * przez literówkę.
 *
 * COFNIĘCIE („undecided") NIE JEST ODMOWĄ, tylko powrotem do pytania, i dlatego jest osobną
 * wartością, a nie brakiem pola: „przemyślmy to jeszcze raz" i „przemyślane, nie wolno" to dwa
 * różne zdania i człowiek ma móc powiedzieć oba.
 */
import { decideOwnPosts } from "../../../../lib/control-plane";
import { backWith, field, formOf, tokenOrLogin, verdictOf } from "../_handler";

export const dynamic = "force-dynamic";

const CHOICES: Record<string, boolean | null> = {
  yes: true,
  no: false,
  undecided: null,
};

export async function POST(request: Request): Promise<Response> {
  const auth = await tokenOrLogin();
  if ("redirect" in auth) return auth.redirect;
  const form = await formOf(request);
  if (form === null) return backWith("own_posts_refused");
  const communityId = field(form, "community_id");
  const choice = field(form, "allowed");
  if (communityId === "" || !Object.prototype.hasOwnProperty.call(CHOICES, choice)) {
    return backWith("own_posts_refused");
  }
  return verdictOf(
    await decideOwnPosts(auth.token, communityId, CHOICES[choice] ?? null),
    "own_posts_saved",
    "own_posts_refused",
  );
}
