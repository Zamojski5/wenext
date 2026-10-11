/**
 * Umówione powroty agenta na ekranie obecności (0023).
 *
 * CZEGO PILNUJĄ TE TESTY. Każdy przypadek to jedno zdanie, które ten panel mógłby powiedzieć
 * nieprawdziwie:
 *
 *   * „agent wróci o 18" pokazane jako coś, co się już wydarzyło;
 *   * przycisk odwołania, który odwołuje inny wiersz niż ten, pod którym stoi;
 *   * brak umówionych powrotów pokazany jako awaria, choć to normalny stan;
 *   * termin przyjęty z control plane bez terminu, czyli wiersz udający, że coś wie.
 */
import { describe, expect, it } from "vitest";

import { PlannedReturns } from "../components/studio/planned-returns";
import { parsePresence } from "../lib/presence-schema";
import type { Wakeup } from "../lib/presence-schema";
import { renderToDocument } from "./render";

const WORKSPACE = "aaaaaaaa-0000-4000-8000-000000000001";
const PROJECT = "bbbbbbbb-0000-4000-8000-000000000001";
const HERE = `/w/${WORKSPACE}/p/${PROJECT}/presence`;

const PLANNED: Wakeup = {
  wakeup_id: "cccccccc-0000-4000-8000-000000000001",
  reason: "wrócę sprawdzić, czy ktoś odpowiedział na wątek o operatorach",
  session_id: null,
  skipped_reason: null,
  skipped_detail: null,
  cost: null,
  created_at: "2026-09-19T18:00:00+00:00",
  due_at: "2026-09-19T21:30:00+00:00",
  cancelled_at: null,
  note: "wróć do wątku o operatorach i sprawdź, czy autor odpowiedział",
};

describe("panel umówionych powrotów", () => {
  it("pusta_lista_mowi_ze_to_normalny_stan_a_nie_awaria", () => {
    const document_ = renderToDocument(
      <PlannedReturns planned={[]} workspaceId={WORKSPACE} projectId={PROJECT} here={HERE} />,
    );
    const note = document_.querySelector(".st-note")?.textContent ?? "";
    expect(note).toContain("nie umówił się");
    expect(document_.querySelectorAll("form").length).toBe(0);
  });

  it("wiersz_pokazuje_termin_a_nie_chwile_zaplanowania", () => {
    const document_ = renderToDocument(
      <PlannedReturns
        planned={[PLANNED]}
        workspaceId={WORKSPACE}
        projectId={PROJECT}
        here={HERE}
      />,
    );
    const row = document_.querySelector("[data-kind='planned']")?.textContent ?? "";
    // Godzina powrotu jest tym, po co człowiek tu patrzy; chwila zaplanowania stoi obok, opisana.
    expect(row).toContain("2026-09-19 21:30");
    expect(row).toContain("zaplanowany 2026-09-19 18:00");
    // Zdanie agenta, nie kod „DUE".
    expect(row).toContain("sprawdź, czy autor odpowiedział");
    expect(row).not.toContain("DUE");
  });

  it("odwolanie_niesie_identyfikator_swojego_wiersza", () => {
    const second: Wakeup = {
      ...PLANNED,
      wakeup_id: "cccccccc-0000-4000-8000-000000000002",
      due_at: "2026-09-20T09:00:00+00:00",
    };
    const document_ = renderToDocument(
      <PlannedReturns
        planned={[PLANNED, second]}
        workspaceId={WORKSPACE}
        projectId={PROJECT}
        here={HERE}
      />,
    );
    const forms = Array.from(document_.querySelectorAll("form"));
    expect(forms.length).toBe(2);
    const ids = forms.map(
      (form) => form.querySelector("input[name='wakeup_id']")?.getAttribute("value") ?? "",
    );
    expect(ids).toEqual([PLANNED.wakeup_id, second.wakeup_id]);
    for (const form of forms) {
      expect(form.getAttribute("action")).toBe("/studio/presence");
      expect(form.querySelector("input[name='action']")?.getAttribute("value")).toBe(
        "cancel_return",
      );
      expect(form.querySelector("input[name='return_to']")?.getAttribute("value")).toBe(HERE);
    }
  });
});

describe("odczyt obecności rozdziela historię od obietnicy", () => {
  const payload = {
    state: "czuwa",
    delegation: null,
    budget: null,
    wakeups: [
      {
        wakeup_id: "dddddddd-0000-4000-8000-000000000001",
        reason: "nowy wątek",
        session_id: "eeeeeeee-0000-4000-8000-000000000001",
        skipped_reason: null,
        skipped_detail: null,
        cost: { MODEL_TURN: 1 },
        created_at: "2026-09-19T17:00:00+00:00",
        due_at: null,
        cancelled_at: null,
      },
    ],
    planned: [
      {
        wakeup_id: PLANNED.wakeup_id,
        reason: PLANNED.reason,
        session_id: null,
        skipped_reason: null,
        skipped_detail: null,
        cost: null,
        created_at: PLANNED.created_at,
        due_at: PLANNED.due_at,
        cancelled_at: null,
        notatka: PLANNED.note,
      },
    ],
  };

  it("kazda_lista_trafia_tam_gdzie_jej_miejsce", () => {
    const read = parsePresence(payload);
    expect(read.ok).toBe(true);
    if (!read.ok) return;
    expect(read.value.wakeups.map((one) => one.wakeup_id)).toEqual([
      "dddddddd-0000-4000-8000-000000000001",
    ]);
    expect(read.value.planned.map((one) => one.wakeup_id)).toEqual([PLANNED.wakeup_id]);
    expect(read.value.wakeups[0]?.due_at).toBe(null);
    expect(read.value.planned[0]?.due_at).toBe("2026-09-19T21:30:00+00:00");
    expect(read.value.planned[0]?.note).toBe(PLANNED.note);
    // Obudzenie ze zdarzenia nie ma notatki i nie udaje, że ma.
    expect(read.value.wakeups[0]?.note).toBe(null);
  });

  it("control_plane_bez_klucza_planned_daje_puste_a_nie_wyjatek", () => {
    // Obraz api bywa starszy od ekranu. Brak klucza ma znaczyć „nic nie wiem o powrotach",
    // a nie zepsuty ekran obecności.
    const older: Record<string, unknown> = { ...payload };
    delete older["planned"];
    const read = parsePresence(older);
    expect(read.ok).toBe(true);
    if (!read.ok) return;
    expect(read.value.planned).toEqual([]);
    expect(read.value.wakeups.length).toBe(1);
  });
});
