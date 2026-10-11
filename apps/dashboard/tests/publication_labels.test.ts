/**
 * Own post or reply, and the words on the link to a published item, now that X publishes too
 * (26.09).
 *
 * A prepared draft has no `kind` yet, so the publications screen reads it from the target's
 * identifier. Until 26.09 that was `startsWith("r/")`, and every X draft of our own read as "odpowiedź".
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { permalinkLabel, targetIsOwnPost } from "../lib/workspace-schema";

describe("own post or reply", () => {
  it("a_reddit_community_and_an_x_account_are_own_posts", () => {
    expect(targetIsOwnPost("r/SaaS")).toBe(true);
    expect(targetIsOwnPost("x:@anna")).toBe(true);
  });

  it("what_names_the_thing_being_answered_is_a_reply", () => {
    // `t3_…` is the Reddit thread a reply goes under; `x:<digits>` is the X post it answers.
    for (const target of ["t3_abc123", "x:1839201928374", "x:", "", "@anna", "reddit.com/r/SaaS"]) {
      expect(targetIsOwnPost(target), target).toBe(false);
    }
  });
});

describe("the link to a published item", () => {
  it("on_x_it_is_a_post", () => {
    expect(permalinkLabel("https://x.com/anna/status/1839201928374")).toBe("Otwórz post na X");
    expect(permalinkLabel("https://www.x.com/anna/status/1")).toBe("Otwórz post na X");
    expect(permalinkLabel("https://X.COM/anna/status/1")).toBe("Otwórz post na X");
  });

  it("elsewhere_it_stays_the_published_reply", () => {
    for (const permalink of [
      "https://www.reddit.com/r/SaaS/comments/abc/x/def/",
      // A host that merely ENDS in "x.com" is somebody else's.
      "https://notx.com/anna/status/1",
      "https://x.com.evil.example/status/1",
      "nie adres",
    ]) {
      expect(permalinkLabel(permalink), permalink).toBe("Otwórz opublikowaną odpowiedź");
    }
  });

  it("the_publications_screen_asks_these_functions_and_not_its_own_copy", () => {
    const page = fs.readFileSync(
      path.join(
        path.resolve(import.meta.dirname, ".."),
        "app/(studio)/w/[workspaceId]/p/[projectId]/publications/page.tsx",
      ),
      "utf8",
    );
    expect(page).toContain("targetIsOwnPost(draft.target_external_id)");
    expect(page).toContain("permalinkLabel(publication.permalink)");
    expect(page).not.toContain('startsWith("r/")');
  });
});
