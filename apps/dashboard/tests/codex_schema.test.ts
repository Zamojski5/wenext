/**
 * The parsers of "Połączenia Codex", and the one substitution they must never make.
 *
 * ZAKRES-FUNKCJONALNY §3E is unusually specific about this screen: "Jeżeli limitów nie da się
 * odczytać, interfejs mówi wprost, że nie wie. Nie pokazuje zera, nie pokazuje szacunku, nie
 * ukrywa pola." A parser is where that promise is kept or quietly broken, because a defaulted
 * field is invisible afterwards — the screen renders a confident number and nothing anywhere
 * says it was invented.
 *
 * So: `read`, `complete`, `used_percent`, `resets_at` and `window_minutes` are each parsed
 * strictly and independently, and a missing one fails the whole read.
 */
import { describe, expect, it } from "vitest";

import { parseCodexRoot, parseConnection } from "../lib/codex-schema";

const WINDOW = {
  name: "primary",
  present: true,
  used_percent: 12.5,
  // Optional in the pinned app-server contract, so `null` here is a legal answer, not a gap.
  resets_at: null,
  window_minutes: null,
};

const CONNECTION: Record<string, unknown> = {
  connection_id: "aaaa",
  label: "Konto założyciela",
  scope: "organization",
  workspace_id: null,
  home_ref: ".",
  status: "CONNECTED",
  account_email: "kto@example.com",
  plan_type: "pro",
  installation_id: "inst-1",
  models: {
    read: true,
    complete: true,
    count: 2,
    models: [{ id: "gpt-5.6-terra" }, { id: "gpt-5.6-luna" }],
    fetched_at: "2026-09-19T12:00:00+00:00",
    reason: null,
  },
  limits: {
    read: true,
    reason: null,
    at: "2026-09-19T12:00:00+00:00",
    windows: [WINDOW, { ...WINDOW, name: "secondary", present: false, used_percent: null }],
    plan_type: "pro",
  },
  login: null,
  last_read_at: "2026-09-19T12:00:00+00:00",
  last_error: null,
  created_at: "2026-09-19T11:00:00+00:00",
  disconnected_at: null,
  active: true,
  home: { configured: true, exists: true, has_auth: true },
  engine_read: { enabled: true, reason: null },
};

function ok(row: Record<string, unknown>) {
  const result = parseConnection(row) as { ok: boolean; reason?: string; value?: unknown };
  expect(result.ok, result.reason ?? "").toBe(true);
  return result.value as Record<string, unknown>;
}

describe("the Codex connection parser", () => {
  it("accepts_a_complete_row", () => {
    const value = ok(CONNECTION);
    expect(value["status"]).toBe("CONNECTED");
    expect(value["home_ref"]).toBe(".");
  });

  it("fails_the_whole_read_when_any_top_level_field_is_missing", () => {
    for (const key of Object.keys(CONNECTION)) {
      const damaged = { ...CONNECTION };
      delete damaged[key];
      const result = parseConnection(damaged) as { ok: boolean };
      expect(result.ok, `połączenie bez pola ${key} przeszło parsowanie`).toBe(false);
    }
  });

  it("keeps_the_three_window_fields_apart", () => {
    // A window with a percentage and no reset time is a legal answer in the pinned contract.
    // Collapsing the three fields into one "we have limits" boolean would lose exactly that.
    const value = ok(CONNECTION);
    const limits = value["limits"] as { windows: readonly Record<string, unknown>[] };
    expect(limits.windows.length).toBe(2);
    expect(limits.windows[0]?.["used_percent"]).toBe(12.5);
    expect(limits.windows[0]?.["resets_at"]).toBe(null);
    expect(limits.windows[0]?.["window_minutes"]).toBe(null);
    // The second window was not reported at all, which is NOT the same as zero percent used.
    expect(limits.windows[1]?.["present"]).toBe(false);
    expect(limits.windows[1]?.["used_percent"]).toBe(null);
  });

  it("a_window_missing_a_field_entirely_is_a_broken_read", () => {
    for (const key of ["used_percent", "resets_at", "window_minutes", "present", "name"]) {
      const window = { ...WINDOW };
      delete (window as Record<string, unknown>)[key];
      const result = parseConnection({
        ...CONNECTION,
        limits: { ...(CONNECTION["limits"] as object), windows: [window] },
      }) as { ok: boolean };
      expect(result.ok, key).toBe(false);
    }
  });

  it("limits_that_were_not_read_carry_the_reason_and_no_windows", () => {
    const value = ok({
      ...CONNECTION,
      limits: {
        read: false,
        reason: "codex account authentication required to read rate limits",
        at: null,
        windows: [],
        plan_type: null,
      },
    });
    const limits = value["limits"] as Record<string, unknown>;
    expect(limits["read"]).toBe(false);
    expect(limits["reason"]).toContain("authentication required");
    expect(limits["windows"]).toEqual([]);
  });

  it("an_incomplete_model_catalogue_stays_marked_incomplete", () => {
    // Routing on a catalogue that stopped before `nextCursor = null` is forbidden (L2189), so
    // the flag has to survive parsing rather than being inferred from a non-empty list.
    const value = ok({
      ...CONNECTION,
      models: { read: true, complete: false, count: 1, models: [{ id: "x" }], fetched_at: null, reason: null },
    });
    const models = value["models"] as Record<string, unknown>;
    expect(models["complete"]).toBe(false);
    expect(models["count"]).toBe(1);
  });

  it("an_open_login_needs_both_the_address_and_the_code", () => {
    const value = ok({
      ...CONNECTION,
      login: {
        verification_url: "https://auth.example/device",
        user_code: "ABCD-1234",
        deadline_at: "2026-09-19T12:15:00+00:00",
      },
    });
    expect((value["login"] as Record<string, unknown>)["user_code"]).toBe("ABCD-1234");

    // Half a ticket is useless to the human, so it is a failed read rather than a half-drawn box.
    for (const half of [
      { verification_url: "https://auth.example/device" },
      { user_code: "ABCD-1234" },
    ]) {
      expect((parseConnection({ ...CONNECTION, login: half }) as { ok: boolean }).ok).toBe(false);
    }
  });

  it("a_connection_we_never_open_carries_the_reason_as_data", () => {
    // A BOOLEAN from the backend, not a sentence the screen matches on: the reason is prose and
    // prose gets edited, while a screen keying off it would silently stop marking the state.
    const value = ok({
      ...CONNECTION,
      engine_read: {
        enabled: false,
        reason: "na tym katalogu pracuje pilot (L2159)",
      },
    });
    const read = value["engine_read"] as Record<string, unknown>;
    expect(read["enabled"]).toBe(false);
    expect(read["reason"]).toContain("L2159");
  });

  it("an_account_with_no_email_parses_and_stays_null", () => {
    // The pinned schema allows `email: null` on a logged-in ChatGPT account, so this is not a
    // missing login and the parser must not treat it as one.
    const value = ok({ ...CONNECTION, account_email: null });
    expect(value["account_email"]).toBe(null);
    expect(value["status"]).toBe("CONNECTED");
  });
});

describe("the volume root state", () => {
  it("an_unconfigured_deployment_is_a_fact_not_an_empty_list", () => {
    const result = parseCodexRoot({ configured: false, adopted: false }) as {
      ok: boolean;
      value?: Record<string, unknown>;
    };
    expect(result.ok).toBe(true);
    expect(result.value?.["configured"]).toBe(false);
    expect(result.value?.["path"]).toBe(null);
  });

  it("a_configured_root_carries_what_is_really_on_disk", () => {
    const result = parseCodexRoot({
      configured: true,
      adopted: false,
      exists: true,
      has_auth: true,
      path: "/var/wenext/codex-home",
    }) as { ok: boolean; value?: Record<string, unknown> };
    expect(result.ok).toBe(true);
    expect(result.value?.["has_auth"]).toBe(true);
    expect(result.value?.["adopted"]).toBe(false);
  });

  it("a_root_without_the_two_flags_is_a_broken_read", () => {
    expect((parseCodexRoot({ configured: true }) as { ok: boolean }).ok).toBe(false);
    expect((parseCodexRoot({}) as { ok: boolean }).ok).toBe(false);
  });
});
