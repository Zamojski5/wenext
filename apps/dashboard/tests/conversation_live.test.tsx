/**
 * The live half of the conversation (P2 etap B).
 *
 * WHAT IS BEING PROTECTED, and each one is a way a live screen can lie or leak:
 *
 *   * the mark waves while nothing runs, so "pracuje" stops meaning anything
 *     (ZAKRES-FUNKCJONALNY §5 forbids activity with no model running);
 *   * the stream is opened against the control plane instead of this origin, which would put
 *     the operator session in browser code (D-016);
 *   * event text reaches the DOM as HTML, so a page the agent read can execute in the operator's
 *     browser;
 *   * the page needs JavaScript to show the conversation at all, rather than needing it only to
 *     show the conversation SOONER.
 */
import { describe, expect, it } from "vitest";

import { ConversationLive } from "../components/studio/conversation-live";
import { renderToDocument } from "./render";

const WORKSPACE = "aaaaaaaa-0000-4000-8000-000000000001";
const PROJECT = "bbbbbbbb-0000-4000-8000-000000000001";
const SESSION = "cccccccc-0000-4000-8000-000000000001";

function render(status: Parameters<typeof ConversationLive>[0]["status"], afterSeq = 7) {
  return renderToDocument(
    <ConversationLive
      afterSeq={afterSeq}
      projectId={PROJECT}
      sessionId={SESSION}
      status={status}
      workspaceId={WORKSPACE}
    />,
  );
}

describe("ConversationLive", () => {
  it("starts from the sequence the server already rendered", () => {
    const root = render("RUNNING").querySelector("[data-live-root]");
    expect(root?.getAttribute("data-live-seq")).toBe("7");
    // Otherwise the stream would repeat the conversation the page is already showing.
    expect(root?.getAttribute("data-live-count")).toBe("0");
  });

  it("opens the stream against THIS origin and never against the control plane", () => {
    const root = render("RUNNING").querySelector("[data-live-root]");
    const url = root?.getAttribute("data-live-url") ?? "";
    expect(url.startsWith("/studio/stream?")).toBe(true);
    expect(url).toContain(`session_id=${SESSION}`);
    // D-016: no absolute address, nothing for a browser to talk to directly. (A port number is
    // not a useful thing to assert on: the fixture ids happen to contain digits, which is how
    // this assertion first failed on "8000" inside a UUID.)
    expect(url).not.toContain("http");
    expect(url).not.toContain("//");
  });

  it.each(["RUNNING", "QUEUED"] as const)("waves while the work is real: %s", (status) => {
    const mark = render(status).querySelector("[data-live-state]");
    expect(mark?.getAttribute("data-live-state")).toBe("running");
    expect(mark?.textContent).toContain("tura trwa");
  });

  it.each(["WAITING_USER", "PAUSED", "DONE", "FAILED", "BUDGET_EXHAUSTED"] as const)(
    "stays still when nothing runs: %s",
    (status) => {
      const mark = render(status).querySelector("[data-live-state]");
      expect(mark?.getAttribute("data-live-state")).toBe("idle");
      expect(mark?.textContent).toContain("cicho");
    },
  );

  it("puts event text in the DOM as TEXT and never as markup", () => {
    const script = render("RUNNING").querySelector("script")?.textContent ?? "";
    expect(script).toContain("textContent");
    // The trace carries other people's words: a fetched page, a model's answer. A screen that
    // pastes them as HTML is a screen that executes them.
    expect(script).not.toContain("innerHTML");
    expect(script).not.toContain("document.write");
  });

  it("falls back to reloading when the stream cannot be opened", () => {
    const script = render("RUNNING").querySelector("script")?.textContent ?? "";
    expect(script).toContain("location.reload");
    expect(script).toContain("polling");
  });

  it("says what happens without JavaScript instead of leaving a blank", () => {
    const rendered = render("RUNNING");
    const noscript = rendered.querySelector("noscript");
    expect(noscript?.textContent).toContain("Rozmowa powyżej jest kompletna");
  });

  it("adds nothing to the history: both places are empty on the server", () => {
    // Dwa miejsca od 20.09: nurt rozmowy i zwinięty blok kroków. Oba mają być puste w renderze
    // serwera, bo strumień niczego nie dodaje do historii, tylko pokazuje ją wcześniej.
    const rendered = render("RUNNING");
    expect(rendered.querySelector("[data-live-flow]")?.children.length).toBe(0);
    expect(rendered.querySelector("[data-live-step-list]")?.children.length).toBe(0);
    // Blok kroków jest schowany, dopóki nie ma ani jednego kroku.
    expect(rendered.querySelector("[data-live-steps]")?.hasAttribute("hidden")).toBe(true);
  });
});
