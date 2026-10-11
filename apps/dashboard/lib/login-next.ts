/** Preserve the requested conversation through login, without allowing external redirects. */
export function loginNext(raw: unknown): string {
  if (typeof raw !== "string" || !raw.startsWith("/") || raw.startsWith("//") || /[\\\x00-\x20]/.test(raw)) return "/";
  return raw;
}
