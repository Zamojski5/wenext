/**
 * What the delegation panel says one account can do (P6 etap A).
 *
 * The rule lives in the backend (`presence/scope.py::action_supported`, over
 * `workspace/platforms.py`). This file guards the READING of it: the panel must not invent a
 * capability, must not hide one, and must never present "nie wolno" as if it were "nie ma jak".
 * The second is the failure that matters here, because a person who sees REPLY offered for an X
 * account signs a scope that no adapter can carry out and then waits for work that never comes.
 */
import { describe, expect, it } from "vitest";

import {
  accountCapabilityNote,
  actionsPossibleFor,
} from "../lib/presence-schema";

const REDDIT = {
  platform: "REDDIT",
  label: "Reddit",
  comment: true,
  own_post: false,
};
const X = { platform: "X", label: "X", comment: false, own_post: false };

describe("what an account may be delegated for", () => {
  it("an_account_that_can_comment_offers_reply_first", () => {
    // REPLY first because it is the thing the person is most likely to want, and because
    // RESEARCH is always available and would otherwise bury it.
    expect(actionsPossibleFor(REDDIT)).toEqual(["REPLY", "RESEARCH"]);
  });

  it("an_account_on_a_platform_without_an_adapter_offers_only_research", () => {
    expect(actionsPossibleFor(X)).toEqual(["RESEARCH"]);
  });

  it("an_unknown_platform_is_treated_as_having_no_adapter", () => {
    // Not as "everything is possible". The dangerous default is the permissive one.
    expect(actionsPossibleFor(null)).toEqual(["RESEARCH"]);
  });

  it("own_post_appears_only_when_the_backend_says_it_exists", () => {
    // Since 0023 the backend says it exists for Reddit; the panel still reads the flag rather
    // than the platform name, so a platform that cannot found a thread is not offered one.
    expect(actionsPossibleFor({ ...REDDIT, own_post: true })).toContain("OWN_POST");
    expect(actionsPossibleFor(REDDIT)).not.toContain("OWN_POST");
  });

  it("the_sentence_names_the_platform_and_does_not_pretend", () => {
    expect(accountCapabilityNote(REDDIT)).toContain("Reddit");
    expect(accountCapabilityNote(REDDIT)).toContain("REPLY");

    const refusal = accountCapabilityNote(X);
    expect(refusal).toContain("X");
    // "nie umie", not "nie wolno": the product cannot, rather than the person may not.
    expect(refusal).toContain("nie umie tu dziś publikować");
  });

  it("an_unknown_platform_says_so_rather_than_naming_nothing", () => {
    expect(accountCapabilityNote(null)).toContain("platforma nieznana");
  });
});
