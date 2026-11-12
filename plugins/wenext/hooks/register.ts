// The Wenext mod: the Codex card, in Claude Code.
//
// Claude Code is not an MCP Apps host, so the card the agent draws with `room.show` has nowhere to render.
// This mod draws the same room from the same data: when a `room.show` result arrives it opens a pane beside
// the transcript (or, in a terminal too narrow for one, a band above the prompt that opens it), with the
// headline, one person at a time (Prev, Next), the post and the reply as it will appear, and the health of
// the accounts in the footer.
//
// WHAT IT CANNOT DO, AND WHY, MEASURED ON CLAUDE CODE 2.1.296 (12.10.2026). The Codex card sends, edits a
// draft, pauses, signs limits and reconnects an account through five app-only MCP tools behind a one-time
// confirmation (`services/control_plane/control_plane/tools/card_send.py`, `card_actions.py`,
// `card_connect.py`). A mod would have to call them with `$.mcp.call`, and Claude Code does not let it:
//
//   * a tool whose `_meta.ui.visibility` is `["app"]` is not among the connected tools at all:
//     `$.mcp.call` answers `no connected MCP tool "<name>" on a server named "plugin:<p>:<s>"`;
//   * a tool the server does not list answers the same way;
//   * a listed tool is called with the same permission as the model's (`Claude requested permissions to
//     use mcp__... but you haven't granted it yet`).
//
// So the only tools a mod can reach are the ones the MODEL can reach, and exposing the send to the model is
// exactly what the design forbids: publishing must be the person's click, never something the agent does on
// its own. This mod therefore calls NO MCP tool and registers none (`$.mcp` and `$.tool.register` appear
// nowhere in it; a test holds that), and what its buttons do instead is:
//
//   Send       opens the Wenext page at the draft, where the click that sends it is (the existing dashboard
//              approval; Codex has it in the card). Hosted Wenext, which sends nothing, copies the reply
//              and opens the thread, or the X composer with the reply filled in;
//   Edit       a one-line field; Enter asks the AGENT, in the person's own words (`$.prompt.submit` with
//              `asUser`), to save the text as a new draft (`draft.create`: a draft, never a publication);
//   Skip       asks the agent the same way to save the conversation as skipped (`opportunities.mark`);
//   Reconnect  opens the Wenext page where the agent's browser is signed in; the one-click flow is Codex's;
//   Limits     opens the Presence page, where the limits and Pause live.
//
// "SEND ALL" (v11, Codex card: `room.batch_token` and `room.send_batch`, behind one click on a reviewed list)
// IS NOT OFFERED HERE, ON PURPOSE. It is two more app-only tools, so this mod cannot call them (above), and the
// Wenext page has no batch flow to link to: its "Publish" is per draft. A button called "Send all" that opened a
// page where each draft still needs its own click would promise what it does not do. What a Claude Code person
// has instead is "Send in Wenext", per draft, and the Codex card for the batch. When the page gets a batch review
// (the same plan and the same one-time confirmation, `tools/card_batch.py`), the link goes here, without
// `#szkic-`, next to Send.
//
// THE ONE EXCEPTION, AND WHY IT IS NOT ONE: the recording sandbox (`room.show` says `demo: true`, and only
// for the organization of `control_plane/demo.py`, which nothing can act for: it has no dispatcher, so no
// send is ever picked up by a publisher) is REHEARSED in the pane. Send asks "Send from ...?", Yes shows
// "Sent to the publisher (demo)", then "Posted (demo)", each said to be a demo. No tool is called, no
// process runs, no prompt is sent: it is drawing. On any other data the branch is not reachable (a test
// holds it), and a pane never says "Posted" about something it did not see the publisher do.
//
// The first card of a session opens with a short reveal (about two seconds: "Searching threads", "Read N
// threads on r/SaaS, X", then the card), the card's own. `/wenext` opens the pane at any width, or, when
// there is no card yet, asks the agent to show the room (a read), so a demo is one keystroke.
//
// The mod reads the card from the RESULT of `room.show` and keeps it in `$.state`. It opens links with the
// system's own opener (`open`, `xdg-open`), only for an http(s) address, and copies with `$.ui.copy`.

import { atom, read, update } from 'claude-code'
import type { EngineInterface as Api, Register } from 'claude-code'

import type { AccountHealth, Lang, Person, RoomView, UiState } from '../types'
import { drawBand, drawPane, handleOf } from './draw'
import type { Actions } from './draw'
import { FRESH_UI, fromLine, hasLongDash, parseRoom, reconcile, stepped, web } from './room'
import { langOf, say } from './words'

/** The pane's id, and the only tool the mod listens to: the room, from the local Wenext server. */
const PANE = 'wenext'
/** How long a `/wenext` waits for its card: after this the next card is the agent's own and takes no keys. */
const ASKED_MS = 120000
const ROOM_TOOL = /^mcp__(?:plugin_wenext_)?wenext__room_show$/

const room = atom({ plugin: 'wenext', key: 'room' } as const, null)
const ui = atom({ plugin: 'wenext', key: 'ui' } as const, FRESH_UI)

/** The programs that open an address in the person's browser, tried in order. Never a shell. */
const OPENERS: readonly (readonly string[])[] = [['open'], ['xdg-open']]

// -- small helpers that take `$` ----------------------------------------------------------------

async function langFor($: Api, view: RoomView | null): Promise<Lang> {
  const locale = (await $.env.get('LC_ALL')) || (await $.env.get('LANG'))
  return langOf(view?.lang ?? null, locale)
}

async function note($: Api, text: string | null): Promise<void> {
  await update($, ui, (state) => ({ ...state, note: text }))
}

/** Open an address in the browser. `opened`, or `copied` when no opener worked, or `none`. */
async function openUrl($: Api, url: string | null): Promise<'opened' | 'copied' | 'none'> {
  const address = web(url)
  if (address === null) return 'none'
  for (const argv of OPENERS) {
    try {
      const ran = await $.process.run([...argv, address], { timeoutMs: 8000 })
      if (ran.exitCode === 0) return 'opened'
    } catch {
      // this opener is not on this machine: try the next one
    }
  }
  try {
    await $.ui.copy({ text: address })
  } catch {
    // nowhere to copy to either
  }
  return 'copied'
}

async function openAndSay($: Api, url: string | null, done: string): Promise<void> {
  const view = await read($, room)
  const lang = await langFor($, view)
  const how = await openUrl($, url)
  await note($, how === 'none' ? say(lang, 'note.noLink') : how === 'opened' ? done : say(lang, 'note.openFailed'))
}

// -- what a press does ----------------------------------------------------------------------------

async function step($: Api, delta: number): Promise<void> {
  const view = await read($, room)
  if (view === null) return
  await update($, ui, (state) => stepped(state, view, delta))
}

async function more($: Api, person: Person): Promise<void> {
  await update($, ui, (state) => ({ ...state, expanded: state.expanded === person.key ? null : person.key }))
}

async function why($: Api, person: Person): Promise<void> {
  await update($, ui, (state) => ({ ...state, why: state.why === person.key ? null : person.key }))
}

/** SEND. Never a publication from here: the click that sends is on the Wenext page (or, hosted, the
 * person's own post on the platform). */
async function send($: Api, person: Person): Promise<void> {
  const view = await read($, room)
  const reply = person.reply
  if (view === null || reply === null) return
  const lang = await langFor($, view)
  if (view.demo && !view.manual) {
    // The recording sandbox: rehearse the send in the pane (consent first). Nothing is called.
    await update($, ui, (state): UiState => ({ ...state, demo: { key: person.key, phase: 'confirm' }, note: null }))
    return
  }
  if (view.manual) {
    // Hosted: nothing leaves Wenext. X opens its composer with the reply filled in; Reddit gets the
    // reply on the clipboard and the thread in the browser.
    const x = reply.platform === 'X' && reply.intentUrl !== null
    if (!x) {
      try {
        await $.ui.copy({ text: reply.text })
      } catch {
        await note($, say(lang, 'note.copyFailed'))
        return
      }
    }
    await openAndSay($, x ? reply.intentUrl : reply.threadUrl, say(lang, x ? 'note.opened' : 'note.copied'))
    return
  }
  const room$ = view.links.room
  await openAndSay($, room$ === null ? null : `${room$.replace(/#.*$/, '')}#szkic-${reply.rev}`, say(lang, 'note.send'))
}

async function copy($: Api, person: Person): Promise<void> {
  const reply = person.reply
  if (reply === null) return
  const lang = await langFor($, await read($, room))
  try {
    const copied = await $.ui.copy({ text: reply.text })
    await note($, say(lang, copied.isCopied ? 'note.copied' : 'note.copyFailed'))
  } catch {
    await note($, say(lang, 'note.copyFailed'))
  }
}

async function edit($: Api, person: Person): Promise<void> {
  const reply = person.reply
  if (reply === null) return
  await update($, ui, (state): UiState => ({ ...state, editing: reply.rev, draftText: null, note: null }))
}

async function cancelEdit($: Api): Promise<void> {
  await update($, ui, (state): UiState => ({ ...state, editing: null, draftText: null, note: null }))
}

/** EDIT. The words are the person's, the pen is the agent's: ask it to save them as a new draft. A draft is
 * not a publication, and the founder's rule on the long dash is checked here before the agent is asked. */
async function saveEdit($: Api, person: Person, value: string): Promise<void> {
  const view = await read($, room)
  const reply = person.reply
  if (view === null || reply === null) return
  const lang = await langFor($, view)
  const text = fromLine(value)
  const refuse = text === '' ? 'edit.empty' : text === reply.text.trim() ? 'edit.same' : hasLongDash(text) ? 'edit.dash' : null
  if (refuse !== null) {
    // The field stays open with what the person typed, and one line says why.
    await update($, ui, (state): UiState => ({ ...state, draftText: value, note: say(lang, refuse) }))
    return
  }
  const own = reply.kind === 'OWN_POST'
  const ask = own
    ? say(lang, 'ask.editPost', {
        where: reply.community === null ? '' : say(lang, 'ask.inCommunity', { c: reply.community }),
      })
    : say(lang, 'ask.editReply', {
        who: person.author === null ? say(lang, 'ask.who') : handleOf(person.platform, person.author),
        thread: reply.threadUrl === null ? '' : say(lang, 'ask.inThread', { url: reply.threadUrl }),
      })
  await update($, ui, (state): UiState => ({
    ...state,
    editing: null,
    draftText: null,
    pending: { ...state.pending, [person.key]: 'edit' as const },
    note: say(lang, 'note.asked'),
  }))
  // Not awaited: the prompt waits for the agent to be idle, and the pane must not.
  $.prompt.submit({ text: `${ask}\n\n${text}`, asUser: true }).catch(() => undefined)
}

/** SKIP. The agent records it (`opportunities.mark` with `pomin`), the person's decision in the person's
 * words; the conversation leaves the card when the agent shows the room again. */
async function skip($: Api, person: Person): Promise<void> {
  const view = await read($, room)
  const lang = await langFor($, view)
  await update($, ui, (state) => ({
    ...state,
    pending: { ...state.pending, [person.key]: 'skip' as const },
    note: say(lang, 'note.skip'),
  }))
  $.prompt
    .submit({
      text: say(lang, 'ask.skip', { title: person.title.slice(0, 120), url: person.url ?? person.where }),
      asUser: true,
    })
    .catch(() => undefined)
}

async function original($: Api, person: Person): Promise<void> {
  await openAndSay($, person.url, say(await langFor($, await read($, room)), 'note.opened'))
}

async function openPost($: Api, person: Person): Promise<void> {
  await openAndSay($, person.outcome?.url ?? null, say(await langFor($, await read($, room)), 'note.opened'))
}

/** RECONNECT. The one-click flow is Codex's; here it opens the page where the agent's browser is signed in. */
async function reconnect($: Api, account: AccountHealth): Promise<void> {
  const view = await read($, room)
  const lang = await langFor($, view)
  await openAndSay(
    $,
    view?.links.accounts ?? null,
    say(lang, 'note.reconnect', { platform: account.platform === 'X' ? 'X' : 'Reddit' }),
  )
}

async function limits($: Api): Promise<void> {
  const view = await read($, room)
  const lang = await langFor($, view)
  await openAndSay($, view?.links.presence ?? view?.links.room ?? null, say(lang, 'note.limits'))
}

async function bump($: Api): Promise<void> {
  await update($, ui, (state): UiState => ({ ...state, frame: state.frame + 1 }))
}

/** THE REHEARSAL, step two: the person said yes. Only on the sandbox, and only after the sentence was shown. */
async function demoYes($: Api, person: Person): Promise<void> {
  const view = await read($, room)
  const state = await read($, ui)
  if (view === null || !view.demo || view.manual) return
  if (state.demo === null || state.demo.key !== person.key || state.demo.phase !== 'confirm') return
  await update($, ui, (current): UiState => ({ ...current, demo: { key: person.key, phase: 'queued' } }))
  const ticker = $.clock.every(120, () => {
    void bump($)
  })
  $.clock.after(2200, () => {
    ticker.cancel()
    void update($, ui, (current): UiState =>
      current.demo !== null && current.demo.key === person.key ? { ...current, demo: { key: person.key, phase: 'posted' } } : current,
    )
  })
}

async function demoCancel($: Api): Promise<void> {
  await update($, ui, (state): UiState => ({ ...state, demo: null }))
}

/** The first drawing of the session: "Searching threads", "Read N threads on ...", then the card. */
async function startReveal($: Api): Promise<void> {
  const at = await $.clock.now()
  await update($, ui, (state): UiState => ({ ...state, reveal: 1, revealAt: at, frame: 0, revealed: true }))
  const ticker = $.clock.every(100, () => {
    void bump($)
  })
  $.clock.after(800, () => {
    void update($, ui, (state): UiState => ({ ...state, reveal: state.reveal === 0 ? 0 : 2 }))
  })
  $.clock.after(2000, () => {
    ticker.cancel()
    void update($, ui, (state): UiState => ({ ...state, reveal: 0 }))
  })
}

/** The pane's buttons. Each one calls a function above with `$`. */
function actionsOf($: Api): Actions {
  return {
    step: (delta) => step($, delta),
    send: (person) => send($, person),
    copy: (person) => copy($, person),
    edit: (person) => edit($, person),
    cancelEdit: () => cancelEdit($),
    saveEdit: (person, value) => saveEdit($, person, value),
    skip: (person) => skip($, person),
    original: (person) => original($, person),
    more: (person) => more($, person),
    why: (person) => why($, person),
    openPost: (person) => openPost($, person),
    reconnect: (account) => reconnect($, account),
    limits: () => limits($),
    demoYes: (person) => demoYes($, person),
    demoCancel: () => demoCancel($),
  }
}

// -- a room arrived: keep it and seat it --------------------------------------------------------------

/** Did the person's `/wenext` ask for this card a moment ago (and not a minute ago: then it was forgotten). */
async function wasAsked($: Api): Promise<boolean> {
  const state = await read($, ui)
  return state.asked > 0 && (await $.clock.now()) - state.asked < ASKED_MS
}

/** Where the card goes: a pane when one can be seated, else a band that opens it on a press. */
async function seat($: Api, view: RoomView): Promise<boolean> {
  const wanted = view.people.length > 0 || view.accounts.some((account) => account.canConnect)
  if (!wanted) {
    await update($, ui, (state): UiState => ({ ...state, where: 'off' }))
    return false
  }
  // The person typed `/wenext` and is waiting for the card: it takes the keys (they asked for it, and Esc gives
  // them back). A card the agent showed on its own never takes the keyboard from the prompt.
  const asked = await wasAsked($)
  let placed = false
  try {
    placed = (await $.ui.open(asked ? { id: PANE, title: 'Wenext', focus: true, closeOnEscape: true } : { id: PANE, title: 'Wenext' })).isPlaced
  } catch {
    placed = false
  }
  await update($, ui, (state): UiState => ({ ...state, where: placed ? 'pane' : 'band', asked: 0 }))
  if (!placed) $.ui.toast(say(await langFor($, view), 'toast.ready'))
  return placed
}

async function take($: Api, text: string | undefined): Promise<void> {
  const view = parseRoom(text, await $.clock.now())
  if (view === null) return
  await update($, room, () => view)
  await update($, ui, (state) => reconcile(state, view))
  const placed = await seat($, view)
  // The first card of the session, drawn where it can be seen: a short reveal of what was found.
  if (placed && view.people.length > 0 && !(await read($, ui)).revealed) await startReveal($)
}

export const register: Register = (on) => {
  on('session.start', async ($, e, next) => {
    // Registered last in the hook, and wrapped: a name already taken must not stop the rest.
    try {
      await $.command.register({
        name: 'wenext',
        description: say(await langFor($, null), 'cmd.description'),
      })
    } catch {
      // the command is a convenience; the card still appears by itself
    }
    return next(e)
  })

  // THE ONE EVENT THE MOD READS: the result of the room tool, which it hands back untouched.
  on('tool.call', { tool: ROOM_TOOL }, async ($, e, next) => {
    const ran = await next(e)
    if (ran.deny === undefined) {
      const text = typeof ran.text === 'string' ? ran.text : typeof ran.result === 'string' ? ran.result : undefined
      try {
        await take($, text)
      } catch {
        // a card that cannot be drawn is no reason to fail the agent's call
      }
    }
    return ran
  }).catch(($, e, next) => next(e))

  on('command.run', { command: 'wenext' }, async ($) => {
    const view = await read($, room)
    const lang = await langFor($, view)
    if (view === null) {
      // No card yet: ask the agent to show the room. A read, said in the person's own words. Not from
      // here (the engine refuses a submit that would wait on the turn this hook holds, measured): from
      // a timer, which runs outside any event, a moment after the command has answered.
      const ask = say(lang, 'cmd.ask')
      const at = await $.clock.now()
      await update($, ui, (state): UiState => ({ ...state, asked: at }))
      $.clock.after(50, () => {
        $.prompt.submit({ text: ask, asUser: true }).catch(() => undefined)
      })
      return { text: say(lang, 'cmd.asking') }
    }
    await $.ui.open({ id: PANE, title: 'Wenext', focus: true, closeOnEscape: true })
    // Esc closed the pane on a half-written edit: that was a cancel, so it opens on the card, not the field.
    await update($, ui, (state): UiState => ({ ...state, where: 'pane', editing: null, draftText: null }))
    return {}
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e, next) => {
    if (e.surface !== 'terminal' && e.surface !== 'desktop') return next(e)
    const view = await read($, room)
    const state = await read($, ui)
    const { Box, Text, Button, Input } = $.ui.resolve(e)
    if (view === null) {
      return Box({ paddingX: 1, children: [Text({ dimColor: true, children: [say(await langFor($, null), 'pane.none')] })] })
    }
    return drawPane({
      t: { Box, Text, Button, Input },
      lang: await langFor($, view),
      view,
      ui: state,
      now: await $.clock.now(),
      columns: e.props.bodyColumns,
      rows: e.props.scroll.bodyRows,
      actions: actionsOf($),
    })
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.surface !== 'terminal' && e.surface !== 'desktop') return next(e)
    const view = await read($, room)
    const state = await read($, ui)
    if (view === null || state.where !== 'band') return next(e)
    const { Box, Text, Button, Input } = $.ui.resolve(e)
    const band = drawBand(
      {
        t: { Box, Text, Button, Input },
        lang: await langFor($, view),
        view,
        ui: state,
        now: await $.clock.now(),
        columns: e.props.bodyColumns,
        rows: null,
        actions: actionsOf($),
      },
      () => openFromBand($),
      () => dismissBand($),
    )
    if (band === null) return next(e)
    // Keep what the other mods draw in the band, under ours.
    const theirs = await next(e)
    return Box({ flexDirection: 'column', children: [band, theirs] })
  })
}

/** A press is the person's doing, so the pane is seated at any width. */
async function openFromBand($: Api): Promise<void> {
  await $.ui.open({ id: PANE, title: 'Wenext', focus: true, closeOnEscape: true })
  await update($, ui, (state): UiState => ({ ...state, where: 'pane' }))
}

async function dismissBand($: Api): Promise<void> {
  await update($, ui, (state): UiState => ({ ...state, where: 'off' }))
}

