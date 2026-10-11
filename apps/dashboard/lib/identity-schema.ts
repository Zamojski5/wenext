/**
 * The JSON of `api/organizations.py`, checked before it reaches the settings screen (P7).
 *
 * WHAT MUST NEVER DEFAULT HERE IS `active` AND `revoked_at`. The members list shows people who
 * belong here AND people who used to, and a parser that filled either field in would render a
 * removed person as a current one — on the one screen whose job is to answer "who can see our
 * data".
 *
 * There is deliberately no parser for an invitation TOKEN. The token exists in exactly one
 * response, is shown once and is never listed; a type for it here would invite somebody to store
 * it.
 */
import type { Read } from "./control-plane";

function failure(reason: string): Read<never> {
  return { ok: false, unauthorized: false, reason };
}

function report(what: string, missing: readonly string[]): Read<never> {
  return failure(
    `${what}: brakuje pól albo mają zły typ (${missing.join(", ")}). To zepsuty odczyt, ` +
      "nie puste dane",
  );
}

function str(row: Record<string, unknown>, field: string): string | null {
  const value = row[field];
  return typeof value === "string" ? value : null;
}

function flag(row: Record<string, unknown>, field: string): boolean | null {
  const value = row[field];
  return typeof value === "boolean" ? value : null;
}

function nullableString(
  row: Record<string, unknown>,
  field: string,
): { readonly present: true; readonly value: string | null } | { readonly present: false } {
  if (!(field in row)) return { present: false };
  const value = row[field];
  if (value === null) return { present: true, value: null };
  if (typeof value === "string") return { present: true, value };
  return { present: false };
}

export interface OrganizationSummary {
  readonly organization_id: string;
  readonly name: string;
  readonly role: string;
}

export interface OrganizationView {
  readonly current: string;
  /** `true` for a session opened with the founder's operator password, which predates accounts. */
  readonly legacy_operator: boolean;
  readonly organizations: readonly OrganizationSummary[];
}

export interface OrganizationMember {
  readonly user_id: string;
  /** `null` only for the founder's migrated account, until it names itself. */
  readonly email: string | null;
  readonly role: string;
  readonly joined_at: string;
  readonly revoked_at: string | null;
  readonly active: boolean;
}

export interface OrganizationInvitation {
  readonly invitation_id: string;
  readonly email: string;
  readonly expires_at: string;
  readonly accepted_at: string | null;
  /** Still usable: not accepted and not expired. Computed by the backend, printed here. */
  readonly live: boolean;
}

export function parseMember(row: Record<string, unknown>): Read<OrganizationMember> {
  const missing: string[] = [];
  const userId = str(row, "user_id");
  const role = str(row, "role");
  const joined = str(row, "joined_at");
  const email = nullableString(row, "email");
  const revoked = nullableString(row, "revoked_at");
  const active = flag(row, "active");
  if (userId === null) missing.push("user_id");
  if (role === null) missing.push("role");
  if (joined === null) missing.push("joined_at");
  if (!email.present) missing.push("email");
  if (!revoked.present) missing.push("revoked_at");
  if (active === null) missing.push("active");
  if (missing.length > 0) return report("osoba w przestrzeni", missing);
  return {
    ok: true,
    value: {
      user_id: userId as string,
      email: email.present ? email.value : null,
      role: role as string,
      joined_at: joined as string,
      revoked_at: revoked.present ? revoked.value : null,
      active: active as boolean,
    },
  };
}

export function parseInvitation(row: Record<string, unknown>): Read<OrganizationInvitation> {
  const missing: string[] = [];
  const id = str(row, "invitation_id");
  const email = str(row, "email");
  const expires = str(row, "expires_at");
  const accepted = nullableString(row, "accepted_at");
  const live = flag(row, "live");
  if (id === null) missing.push("invitation_id");
  if (email === null) missing.push("email");
  if (expires === null) missing.push("expires_at");
  if (!accepted.present) missing.push("accepted_at");
  if (live === null) missing.push("live");
  if (missing.length > 0) return report("zaproszenie", missing);
  return {
    ok: true,
    value: {
      invitation_id: id as string,
      email: email as string,
      expires_at: expires as string,
      accepted_at: accepted.present ? accepted.value : null,
      live: live as boolean,
    },
  };
}

/** What a role means on the screen. Two roles, and the difference is one sentence long. */
export function roleLabel(role: string): string {
  const names: Record<string, string> = {
    OWNER: "właściciel",
    MEMBER: "członek",
  };
  return names[role] ?? role;
}

export function roleNote(role: string): string {
  return role === "OWNER"
    ? "zaprasza, odbiera dostęp, eksportuje i usuwa przestrzeń"
    : "pracuje w przestrzeni: projekty, materiały, sesje";
}
