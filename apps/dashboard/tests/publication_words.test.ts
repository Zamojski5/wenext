/**
 * A publication row in words a person uses (27.09).
 *
 * The first X reply showed as "SKIPPED, bez potwierdzenia wysłania", with no platform and no
 * reason: nobody could tell it was X, nor that the actuator refused and nothing went out.
 */
import { describe, expect, it } from "vitest";

import { parseResultPublication, publicationReason, publicationStatusLabel } from "../lib/workspace-schema";

describe("publication words", () => {
  it("names_every_state_and_keeps_an_unknown_code_as_it_came", () => {
    expect(publicationStatusLabel("SKIPPED")).toBe("pominięta, nic nie wysłano");
    expect(publicationStatusLabel("UNKNOWN")).toBe("wynik niepewny, nie ponawiamy");
    expect(publicationStatusLabel("QUEUED")).toBe("czeka na wysłanie");
    expect(publicationStatusLabel("NEW_CODE")).toBe("NEW_CODE");
  });

  it("shortens_the_queues_sentence_without_inventing_one", () => {
    const detail =
      "[submit_started=false] aktuator odmówił, nic nie wysłano: aktuator odmówił (409): nie widać posta 2102827031166189870 na stronie; nic nie wysłano";
    // Measured on the page 27.09: "Powód: aktuator odmówił, nic nie wysłano: aktuator odmówił
    // (409): nie widać posta ...; nic nie wysłano" under a state that already said it.
    expect(publicationReason(detail)).toBe("nie widać posta 2102827031166189870 na stronie");
    const blocked =
      '[submit_started=false] platforma zablokowała: platforma zablokowała publikację: host odmówił całemu klientowi (status 403)';
    expect(publicationReason(blocked)).toBe(
      "platforma zablokowała publikację: host odmówił całemu klientowi (status 403)",
    );
    const long = `[submit_started=false] aktuator odmówił, nic nie wysłano: ${"x".repeat(400)}`;
    expect((publicationReason(long) ?? "").length).toBeLessThanOrEqual(160);
    expect(publicationReason(null)).toBeNull();
    expect(publicationReason("   ")).toBeNull();
  });

  it("reads_platform_and_reason_from_the_wire_and_tolerates_an_older_api", () => {
    const base = {
      request_id: "r",
      status: "SKIPPED",
      kind: "REPLY",
      target_url: "https://x.com/demo_author/status/2102827031166189870",
      permalink: null,
      published_at: null,
      queued_at: "2026-09-27T00:47:21+00:00",
      still_present: null,
      observed_at: null,
    };
    const read = parseResultPublication({ ...base, platform: "X", detail: "aktuator odmówił" });
    expect(read.ok && read.value.platform).toBe("X");
    expect(read.ok && read.value.detail).toBe("aktuator odmówił");
    const older = parseResultPublication(base);
    expect(older.ok && older.value.platform).toBeNull();
  });
});
