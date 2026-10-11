/**
 * The live script waits for hydration before it binds to the page (27.09).
 *
 * MEASURED IN THE FOUNDER'S CHROME: a background session streamed 56 frames in 6 s and the page
 * showed no step at all. The script in the HTML ran before React took the page over, opened the
 * stream, changed the status label, and React, finding text other than the server's (error #418),
 * threw the DOM away and drew it again. The script kept writing into the detached nodes.
 *
 * So: no stream before the client component says the page is hydrated, the nodes are looked up
 * when the script begins (after React replaced them), and a page that never hydrates still gets
 * its stream after the wait.
 */
import { createContext, runInNewContext } from "node:vm";

import { describe, expect, it } from "vitest";

import { ConversationLive } from "../components/studio/conversation-live";
import { renderToDocument } from "./render";

type Handler = (message: { data: string }) => void;

function harness() {
  const rendered = renderToDocument(
    <ConversationLive
      afterSeq={36}
      projectId="bbbbbbbb-0000-4000-8000-000000000001"
      sessionId="cccccccc-0000-4000-8000-000000000001"
      status="RUNNING"
      workspaceId="aaaaaaaa-0000-4000-8000-000000000001"
    />,
  );
  const script = rendered.querySelector("script")?.textContent ?? "";
  const streams: { handlers: Record<string, Handler> }[] = [];
  const timers: (() => void)[] = [];
  const window_: Record<string, unknown> = {
    setTimeout: (fn: () => void) => {
      timers.push(fn);
      return 0;
    },
    location: { reload() {} },
  };
  class FakeEventSource {
    handlers: Record<string, Handler> = {};
    constructor(public url: string) {
      streams.push(this);
    }
    addEventListener(kind: string, fn: Handler) {
      this.handlers[kind] = fn;
    }
    close() {}
  }
  runInNewContext(script, createContext({ document: rendered, EventSource: FakeEventSource, window: window_ }));
  return { rendered, streams, timers, window: window_ };
}

describe("the live script and hydration", () => {
  it("binds_to_the_nodes_react_drew_not_to_the_ones_it_threw_away", () => {
    const test = harness();
    expect(test.streams).toHaveLength(0);

    // Hydration fails on the text, React draws the section again: new nodes in the same place.
    const oldRoot = test.rendered.querySelector("[data-live-root]") as Element;
    const drawnAgain = oldRoot.cloneNode(true) as Element;
    oldRoot.replaceWith(drawnAgain);
    test.window.wenextSoftRefresh = true;
    (test.timers.shift() as () => void)();

    expect(test.streams).toHaveLength(1);
    const stream = test.streams[0] as { handlers: Record<string, Handler> };
    stream.handlers.event?.({
      data: JSON.stringify({ seq: 37, kind: "ASSISTANT_MESSAGE", payload: { text: "Czytam wątek." } }),
    });
    expect(drawnAgain.querySelector("[data-live-answer]")?.textContent).toBe("Czytam wątek.");
    expect(oldRoot.querySelector("[data-live-answer]")).toBeNull();
  });

  it("a_page_that_never_hydrates_still_gets_its_stream_after_the_wait", () => {
    const test = harness();
    for (let tick = 0; tick < 59; tick += 1) (test.timers.shift() as () => void)();
    expect(test.streams).toHaveLength(0);
    (test.timers.shift() as () => void)();
    expect(test.streams).toHaveLength(1);
  });
});
