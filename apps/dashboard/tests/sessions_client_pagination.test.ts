import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CONTROL_PLANE_URL_VARIABLE } from "../lib/control-plane";
import { getSessionDetail } from "../lib/sessions-client";

const session = {
  session_id: "conversation", project_id: "project", goal: "Research",
  status: "RUNNING", engine_thread_id: null, result_summary: null,
  result_refs: [], created_at: "2026-09-22T16:00:00Z", updated_at: "2026-09-22T16:00:00Z",
  finished_at: null,
};
const events = (start: number, end: number) => Array.from({ length: end - start + 1 }, (_, i) => ({
  seq: start + i, kind: "ASSISTANT_MESSAGE", payload: { text: `Message ${start + i}` },
  created_at: session.created_at,
}));
const page = (start: number, end: number, latest: number, legacy = false) => new Response(JSON.stringify({
  session, events: events(start, end), last_seq: legacy ? latest : end,
  ...(legacy ? {} : { latest_seq: latest }),
}), { status: 200 });
const read = (after = 0) => getSessionDetail("token", "workspace", "project", "conversation", after);

beforeEach(() => { vi.stubEnv(CONTROL_PLANE_URL_VARIABLE, "http://control-plane.test"); });
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("conversation history pagination", () => {
  it.each([false, true])("renders all 283 events before starting the stream (legacy=%s)", async (legacy) => {
    const fetcher = vi.fn().mockResolvedValueOnce(page(1, 200, 283, legacy))
      .mockResolvedValueOnce(page(201, 283, 283, legacy));
    vi.stubGlobal("fetch", fetcher);
    const result = await read();
    expect(result.ok).toBe(true);
    if (!result.ok || !result.value) throw new Error("Missing conversation");
    expect(result.value.events).toHaveLength(283);
    expect(result.value.events.at(-1)?.payload.text).toBe("Message 283");
    expect(result.value.last_seq).toBe(283);
    expect(fetcher.mock.calls[1]?.[0]).toContain("after_seq=200");
  });

  it("stops at the initial snapshot while the agent keeps appending", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(page(1, 200, 250))
      .mockResolvedValueOnce(page(201, 300, 350));
    vi.stubGlobal("fetch", fetcher);
    const result = await read();
    expect(result.ok && result.value?.last_seq).toBe(300);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it.each(["empty", "duplicate", "unauthorized", "wrong session"])("does not hide a %s second page", async (failure) => {
    let second: Response;
    if (failure === "unauthorized") second = new Response("", { status: 401 });
    else if (failure === "duplicate") second = page(200, 250, 283);
    else if (failure === "wrong session") second = new Response(JSON.stringify({
      session: { ...session, session_id: "someone-else" }, events: events(201, 283), last_seq: 283,
    }));
    else second = page(201, 200, 283);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(page(1, 200, 283)).mockResolvedValueOnce(second));
    const result = await read();
    expect(result.ok).toBe(false);
    if (failure === "unauthorized" && !result.ok) expect(result.unauthorized).toBe(true);
  });

  it("accepts an empty incremental read without repeated polling", async () => {
    const fetcher = vi.fn().mockResolvedValue(page(284, 283, 283));
    vi.stubGlobal("fetch", fetcher);
    const result = await read(283);
    expect(result.ok && result.value?.events).toEqual([]);
    expect(result.ok && result.value?.last_seq).toBe(283);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
