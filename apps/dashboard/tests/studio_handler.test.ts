/**
 * `safeReturn` — the open-redirect guard of the studio's form handlers.
 *
 * WHY THIS IS A TEST AND NOT A COMMENT. `return_to` arrives in a POST body, so anybody can put
 * anything in it, and a 303 whose `Location` came from that body is an open redirect: a link that
 * looks like this dashboard, is followed by a logged-in operator, and lands somewhere else.
 *
 * The obvious check — `value.startsWith("/")` — is NOT enough, and that is the whole reason this
 * file exists. `//evil.example/x` starts with a slash and is a PROTOCOL-RELATIVE URL: the browser
 * reads it as `https://evil.example/x`. `/\\evil.example` is the same trick with the separator
 * some clients normalise. Both are refused here, and both are in the table below so that a
 * "simplification" of the guard fails instead of passing.
 *
 * The parser is also checked for the thing it must NOT do: refuse a legitimate deep path with a
 * query string. A guard that sends every submit back to `/` looks safe and quietly breaks the
 * product, which is how guards get deleted.
 */
import { describe, expect, it } from "vitest";

import { backTo, checkbox, field, safeReturn } from "../app/studio/_handler";

function formWith(entries: Record<string, string>): FormData {
  const form = new FormData();
  for (const [key, value] of Object.entries(entries)) {
    form.set(key, value);
  }
  return form;
}

const REFUSED: readonly string[] = [
  "https://evil.example/x",
  "http://evil.example",
  "//evil.example/x",
  "/\\evil.example",
  "/w/abc\\..\\..",
  "javascript:alert(1)",
  "evil.example",
  "",
];

const ACCEPTED: readonly string[] = [
  "/",
  "/w/aaaa-bbbb",
  "/w/aaaa-bbbb/p/cccc-dddd",
  "/w/aaaa-bbbb/people",
  "/operations",
];

describe("safeReturn", () => {
  it("refuses_every_value_that_could_leave_this_origin", () => {
    // Count first: an empty table would make the loop below prove nothing.
    expect(REFUSED.length).toBe(8);
    for (const value of REFUSED) {
      expect(safeReturn(formWith({ return_to: value })), value).toBe("/");
    }
  });

  it("accepts_a_path_on_this_origin_unchanged", () => {
    expect(ACCEPTED.length).toBe(5);
    for (const value of ACCEPTED) {
      expect(safeReturn(formWith({ return_to: value })), value).toBe(value);
    }
  });

  it("a_missing_field_falls_back_to_the_studio_root", () => {
    expect(safeReturn(new FormData())).toBe("/");
  });
});

describe("backTo", () => {
  it("carries_the_info_KEY_and_never_a_sentence", () => {
    // The screen looks the key up in `STUDIO_INFO`. A message passed as text in the URL would be
    // a message anybody can put on this dashboard.
    const response = backTo("/w/abc", "project_created");
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/w/abc?info=project_created");
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("appends_to_a_path_that_already_has_a_query", () => {
    expect(backTo("/w/abc?x=1", "refused").headers.get("location")).toBe(
      "/w/abc?x=1&info=refused",
    );
  });
});

describe("field and checkbox", () => {
  it("field_trims_and_treats_a_missing_value_as_empty", () => {
    const form = formWith({ name: "  Anna  " });
    expect(field(form, "name")).toBe("Anna");
    expect(field(form, "nothing")).toBe("");
  });

  it("an_unchecked_checkbox_is_false_because_it_is_simply_absent", () => {
    // This is the whole mechanism behind un-pausing a project: a browser sends nothing for an
    // unchecked box, so a handler that only ever read "true" would give the user a pause with no
    // way out.
    expect(checkbox(formWith({ paused: "on" }), "paused")).toBe(true);
    expect(checkbox(new FormData(), "paused")).toBe(false);
  });
});
