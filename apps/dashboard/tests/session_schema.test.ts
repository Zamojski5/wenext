/**
 * `lib/session-schema.ts`: a broken read must not become a calm screen.
 *
 * The two failures worth naming, both of which this file measures directly:
 *
 *   * a `status` that is absent, or a value nobody defined, must FAIL the read. Rendered as
 *     anything at all it tells the person the agent is working when nobody knows what it is
 *     doing, and ZAKRES-FUNKCJONALNY §5 forbids exactly that;
 *   * one malformed event must fail the WHOLE trace rather than being skipped. A skipped event
 *     leaves a conversation with a hole in it that looks identical to a conversation where
 *     nothing happened.
 *
 * And one property that is easy to lose in a refactor: only `RUNNING` means a turn is in flight.
 * `QUEUED` is a job nobody has claimed, and a mark that waves for it is a screen claiming work
 * that is not happening.
 */
import { describe, expect, it } from "vitest";

import {
  SESSION_STATUSES,
  isWaitingOnTheModel,
  parseEvent,
  parseSession,
  parseSessionDetail,
  parseSessionList,
} from "../lib/session-schema";

const SESSION = {
  session_id: "11111111-1111-1111-1111-111111111111",
  project_id: "22222222-2222-2222-2222-222222222222",
  goal: "sprawdź, dlaczego odzew jest mały",
  status: "WAITING_USER",
  engine_thread_id: "th_1",
  result_summary: "odpowiedź agenta",
  result_refs: [{ tool: "catalog.list" }],
  created_at: "2026-09-19T12:00:00+00:00",
  updated_at: "2026-09-19T12:05:00+00:00",
  finished_at: null,
};

const EVENT = {
  seq: 1,
  kind: "USER_MESSAGE",
  payload: { text: "cel" },
  created_at: "2026-09-19T12:00:00+00:00",
};

describe("parseSession", () => {
  it("accepts a complete row and keeps every field", () => {
    const parsed = parseSession(SESSION);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.session_id).toBe(SESSION.session_id);
    expect(parsed.value.status).toBe("WAITING_USER");
    expect(parsed.value.engine_thread_id).toBe("th_1");
    expect(parsed.value.finished_at).toBeNull();
  });

  it("accepts null for the fields that are legitimately null", () => {
    const parsed = parseSession({
      ...SESSION,
      engine_thread_id: null,
      result_summary: null,
      status: "QUEUED",
    });
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.engine_thread_id).toBeNull();
  });

  it("fails on an unknown status instead of rendering it", () => {
    const parsed = parseSession({ ...SESSION, status: "THINKING" });
    expect(parsed.ok).toBe(false);
    if (parsed.ok) return;
    expect(parsed.reason).toContain("status");
  });

  it.each(["session_id", "goal", "status", "created_at", "result_refs"])(
    "fails when %s is missing, rather than defaulting it",
    (field) => {
      const row: Record<string, unknown> = { ...SESSION };
      delete row[field];
      expect(parseSession(row).ok).toBe(false);
    },
  );

  it("treats an ABSENT nullable field as broken, not as null", () => {
    const row: Record<string, unknown> = { ...SESSION };
    delete row["engine_thread_id"];
    const parsed = parseSession(row);
    expect(parsed.ok).toBe(false);
    if (parsed.ok) return;
    expect(parsed.reason).toContain("engine_thread_id");
  });
});

describe("parseEvent", () => {
  it("accepts a complete event", () => {
    const parsed = parseEvent(EVENT);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.seq).toBe(1);
    expect(parsed.value.kind).toBe("USER_MESSAGE");
  });

  it("refuses seq 0, a negative seq and a fractional one", () => {
    for (const seq of [0, -1, 1.5]) {
      expect(parseEvent({ ...EVENT, seq }).ok).toBe(false);
    }
  });

  it("refuses an unknown kind", () => {
    expect(parseEvent({ ...EVENT, kind: "THINKING" }).ok).toBe(false);
  });
});

describe("parseSessionDetail", () => {
  it("accepts a session with its trace and its last number", () => {
    const parsed = parseSessionDetail({
      session: SESSION,
      events: [EVENT, { ...EVENT, seq: 2, kind: "ASSISTANT_MESSAGE" }],
      last_seq: 2,
    });
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.events.map((event) => event.seq)).toEqual([1, 2]);
    expect(parsed.value.last_seq).toBe(2);
  });

  it("fails the whole read when ONE event is malformed", () => {
    const parsed = parseSessionDetail({
      session: SESSION,
      events: [EVENT, { ...EVENT, seq: 2, kind: "NONSENSE" }],
      last_seq: 2,
    });
    expect(parsed.ok).toBe(false);
  });

  it("fails when last_seq is missing, because the screen resumes from it", () => {
    expect(parseSessionDetail({ session: SESSION, events: [] }).ok).toBe(false);
  });

  it("accepts an empty trace with last_seq 0", () => {
    const parsed = parseSessionDetail({ session: SESSION, events: [], last_seq: 0 });
    expect(parsed.ok).toBe(true);
  });
});

describe("parseSessionList", () => {
  it("accepts a list and fails on one bad row", () => {
    expect(parseSessionList({ sessions: [SESSION] }).ok).toBe(true);
    expect(parseSessionList({ sessions: [SESSION, { goal: "x" }] }).ok).toBe(false);
    expect(parseSessionList({}).ok).toBe(false);
  });
});

describe("isWaitingOnTheModel", () => {
  it("is true for RUNNING and false for every other status", () => {
    expect(isWaitingOnTheModel("RUNNING")).toBe(true);
    const others = SESSION_STATUSES.filter((status) => status !== "RUNNING");
    expect(others.length).toBe(7);
    for (const status of others) {
      expect(isWaitingOnTheModel(status)).toBe(false);
    }
  });

  it("does not wave for QUEUED, which is a job nobody has claimed yet", () => {
    expect(isWaitingOnTheModel("QUEUED")).toBe(false);
  });
});

describe("trzy pola D-045 i ich trzeci stan", () => {
  // `tytul`, `wynik_zdanie` i `rodzaj` wchodzą do API osobno od ekranu. BRAK pola znaczy „ten
  // obraz api ich jeszcze nie liczy" i NIE jest zepsutym odczytem, w odróżnieniu od wszystkich
  // pozostałych pól tej odpowiedzi. Zły TYP nadal jest: znaczy, że rozmawiamy z czymś innym.
  const row = {
    session_id: "11111111-1111-1111-1111-111111111111",
    project_id: "22222222-2222-2222-2222-222222222222",
    goal: "cel",
    status: "RUNNING",
    engine_thread_id: null,
    result_summary: null,
    result_refs: [],
    created_at: "2026-09-20T08:00:00+00:00",
    updated_at: "2026-09-20T08:00:00+00:00",
    finished_at: null,
  };

  it("an_answer_without_them_is_a_good_answer", () => {
    const parsed = parseSession(row);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.tytul).toBeUndefined();
      expect(parsed.value.wynik_zdanie).toBeUndefined();
      expect(parsed.value.rodzaj).toBeUndefined();
    }
  });

  it("null_is_carried_through_and_is_not_the_same_as_absent", () => {
    const parsed = parseSession({ ...row, tytul: null, wynik_zdanie: null, rodzaj: null });
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.tytul).toBeNull();
      expect("tytul" in parsed.value).toBe(true);
    }
  });

  it("a_value_of_the_wrong_type_breaks_the_read", () => {
    for (const broken of [{ tytul: 7 }, { wynik_zdanie: [] }, { rodzaj: "TIK" }]) {
      const parsed = parseSession({ ...row, ...broken });
      expect(parsed.ok, JSON.stringify(broken)).toBe(false);
    }
  });

  it("the_two_known_kinds_are_accepted_and_nothing_else_is", () => {
    for (const rodzaj of ["CONVERSATION", "PRESENCE"]) {
      expect(parseSession({ ...row, rodzaj }).ok, rodzaj).toBe(true);
    }
  });
});
