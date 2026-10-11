/**
 * Trzy stany decyzji o własnych wątkach na karcie społeczności (0025).
 *
 * CZEGO PILNUJĄ TE TESTY. Ta karta jest jedynym miejscem, w którym człowiek może powiedzieć
 * „w tej społeczności wolno nam pisać od siebie", więc każdy z trzech stanów musi się dać
 * odróżnić NA PIERWSZY RZUT OKA i każdy musi dać się zmienić:
 *
 *   * „nikt nie zdecydował" pokazane jako odmowa nauczyłoby człowieka, że nie ma o co pytać;
 *   * przycisk stanu, w którym już jesteśmy, ma być wyłączony, żeby klikanie go nie wyglądało
 *     jak zmiana;
 *   * cofnięcie decyzji musi być osiągalne, bo „przemyślmy to jeszcze raz" jest czymś innym niż
 *     „nie wolno".
 */
import { describe, expect, it } from "vitest";

import { OwnPostsDecision } from "../components/views/community-view";
import type { CommunitySummary } from "../lib/observe-schema";
import { ONE_MINUTE_AGO } from "./fixtures";
import { renderToDocument } from "./render";

const BASE: CommunitySummary = {
  community_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  platform: "REDDIT",
  community_key: "r/kubernetes",
  ref: "REDDIT:r/kubernetes",
  display_name: "r/kubernetes",
  access_mode: "ACCOUNT_REQUIRED",
  observe_enabled: true,
  poll_interval_seconds: 600,
  last_polled_at: ONE_MINUTE_AGO,
  watermark_created_at: ONE_MINUTE_AGO,
  created_at: ONE_MINUTE_AGO,
  own_posts_allowed: null,
  own_posts_decided_at: null,
  counts: null,
};

function buttons(community: CommunitySummary) {
  const document_ = renderToDocument(<OwnPostsDecision community={community} />);
  return Array.from(document_.querySelectorAll("button")).map((one) => ({
    value: one.getAttribute("value"),
    disabled: one.hasAttribute("disabled"),
  }));
}

describe("decyzja o własnych wątkach", () => {
  it("brak_decyzji_jest_pytaniem_a_nie_odmowa", () => {
    const document_ = renderToDocument(<OwnPostsDecision community={BASE} />);
    const text = document_.body.textContent ?? "";
    expect(text).toContain("nikt jeszcze nie zdecydował");
    // Zdanie mówi, co z tego wynika DZIŚ, żeby brak decyzji nie wyglądał jak awaria.
    expect(text).toContain("agent odpowiada tylko w cudzych wątkach");
    expect(document_.querySelector("[data-ownposts]")?.getAttribute("data-ownposts")).toBe("null");
  });

  it("wolno_i_nie_wolno_to_dwa_rozne_zdania", () => {
    const decided = { ...BASE, own_posts_decided_at: ONE_MINUTE_AGO };
    const yes = renderToDocument(
      <OwnPostsDecision community={{ ...decided, own_posts_allowed: true }} />,
    ).body.textContent;
    const no = renderToDocument(
      <OwnPostsDecision community={{ ...decided, own_posts_allowed: false }} />,
    ).body.textContent;
    expect(yes).toContain("wolno zakładać własne wątki");
    expect(no).toContain("własnych wątków nie zakładamy");
    expect(yes).not.toBe(no);
    // Decyzja ma datę: „kiedy" jest częścią odpowiedzi na pytanie „skąd to się wzięło".
    expect(yes).toContain("zdecydowano");
  });

  it("przycisk_stanu_w_ktorym_jestesmy_jest_wylaczony", () => {
    expect(buttons(BASE)).toEqual([
      { value: "yes", disabled: false },
      { value: "no", disabled: false },
      { value: "undecided", disabled: true },
    ]);
    expect(buttons({ ...BASE, own_posts_allowed: true })).toEqual([
      { value: "yes", disabled: true },
      { value: "no", disabled: false },
      { value: "undecided", disabled: false },
    ]);
    expect(buttons({ ...BASE, own_posts_allowed: false })).toEqual([
      { value: "yes", disabled: false },
      { value: "no", disabled: true },
      { value: "undecided", disabled: false },
    ]);
  });

  it("formularz_wskazuje_swoja_spolecznosc_i_swoja_trase", () => {
    const document_ = renderToDocument(<OwnPostsDecision community={BASE} />);
    const form = document_.querySelector("form");
    expect(form?.getAttribute("action")).toBe("/community/own-posts");
    expect(form?.querySelector("input[name='community_id']")?.getAttribute("value")).toBe(
      BASE.community_id,
    );
  });
});
