import type { ConsentDescription } from '@cloudflare/workers-oauth-provider';

/** Everything that came from a client (name, scopes, hosts) is attacker-chosen: always escaped. */
export const escapeHtml = (value: string): string =>
  value.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);

const STYLE = `
  body{font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:12vh auto;padding:0 1.25rem;color:#1b1b1f}
  h1{font-size:1.35rem;margin:0 0 .75rem}
  button{font:inherit;padding:.55rem 1.1rem;border-radius:.5rem;border:1px solid #1b1b1f;cursor:pointer}
  button[value=approve]{background:#1b1b1f;color:#fff}
  .note{color:#555;font-size:.92rem}
  @media (prefers-color-scheme:dark){body{background:#131316;color:#ececf1}button{color:#ececf1;border-color:#ececf1;background:none}button[value=approve]{background:#ececf1;color:#131316}.note{color:#aaa}}
`;

function shell(title: string, body: string): string {
  return `<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeHtml(title)}</title>
<style>${STYLE}</style>
${body}`;
}

export function consentPage(details: ConsentDescription, handle: string): string {
  const name = escapeHtml(details.clientName);
  const origin = details.clientDomain
    ? `Published by <strong>${escapeHtml(details.clientDomain)}</strong>.`
    : 'This app registered itself, so its name is not verified.';
  const local = details.redirectIsLoopback
    ? '<p><strong>This sends access to an app on your computer.</strong> Continue only if you just started signing in from your own coding agent.</p>'
    : '';
  return shell(
    'Connect to Wenext',
    `<h1>Connect ${name} to Wenext?</h1>
<p>${name} wants to use Wenext on your behalf: find people who have your product's problem and draft replies for you to post. Wenext never posts for you.</p>
<p class="note">${origin} After you allow it, you sign in with GitHub so Wenext knows which project is yours. Access will be sent to <strong>${escapeHtml(details.redirectHost)}</strong>.</p>
${local}
<form method="post">
  <input type="hidden" name="handle" value="${escapeHtml(handle)}">
  <p><button name="decision" value="approve">Allow and sign in with GitHub</button> <button name="decision" value="deny">Deny</button></p>
</form>`,
  );
}

export function messagePage(title: string, message: string): string {
  return shell(title, `<h1>${escapeHtml(title)}</h1><p>${escapeHtml(message)}</p>`);
}

export function landingPage(mcpUrl: string): string {
  return shell(
    'Wenext MCP',
    `<h1>Wenext</h1>
<p>This is the sign-in and gateway for Wenext in Codex and Claude Code.</p>
<p>Codex: <code>codex mcp add wenext --url ${escapeHtml(mcpUrl)}</code><br>
Claude Code: <code>claude mcp add --transport http wenext ${escapeHtml(mcpUrl)}</code></p>`,
  );
}
