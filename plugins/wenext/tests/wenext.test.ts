// The Wenext mod, through the engine's own kit: the card arrives in the result of `room.show`, is drawn as
// a pane (or a band), and every press does what the header of `hooks/register.ts` says and nothing more.
//
// THE INVARIANT HELD HERE: nothing the mod does can publish. It calls no MCP tool (a stub for `mcp.call`
// is never registered, so a call would fail the test), registers none, and its buttons only open pages,
// copy text and ask the agent, in the person's own words, to save a DRAFT or a skip.

import { expect, test } from 'claude-code/testing'

import { BAND, DASHBOARD, PANE, REV_A, ROOM_URL, TOOL, answerRoom, arm, room, shown } from './fixtures'

const SURFACES = ['terminal', 'desktop'] as const

test('a room.show result opens the pane and reaches the model untouched', async ($, on) => {
  const rec = arm(on)
  const payload = room()
  answerRoom(on, payload)
  const ran = await shown($, rec)
  expect(rec.opened.length).toBe(1)
  expect(rec.opened[0]).toMatchObject({ id: 'wenext', title: 'Wenext' })
  // The mod only watches: what the model reads is exactly what the server said.
  expect((ran as { text?: string }).text).toBe(JSON.stringify(payload))
})

test('the pane draws the headline, one person, the post and the reply as it will appear', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect(await ui.find({ type: 'Text', text: /3 people are writing right now about the problem your code solves/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Newest 3 h ago/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /3 replies waiting for your OK/ })).toBeDefined()
    // Draft A is first: a Reddit reply, drawn with the handle it goes out from.
    expect(await ui.find({ type: 'Text', text: /r\/SaaS/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /What is a boring marketing channel/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'YOUR REPLY, AS IT WILL APPEAR' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'u/demo_founder' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /First users rarely come from one place/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Goes out from u\/demo_founder after your click on the Wenext page/ })).toBeDefined()
    await ui.unmount()
  }
})

test('prev and next page through the people and stop at the ends', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect(await ui.find({ type: 'Text', text: '1 of 5' })).toBeDefined()
    await ui.press({ key: 'next' })
    expect(await ui.find({ type: 'Text', text: '2 of 5' })).toBeDefined()
    // The second person is on X: its reply is drawn as an X reply.
    expect(await ui.find({ type: 'Text', text: 'Replying to @builder_x' })).toBeDefined()
    await ui.press({ key: 'next' })
    await ui.press({ key: 'next' })
    await ui.press({ key: 'next' })
    await ui.press({ key: 'next' })
    expect(await ui.find({ type: 'Text', text: '5 of 5' })).toBeDefined()
    await ui.press({ key: 'prev' })
    expect(await ui.find({ type: 'Text', text: '4 of 5' })).toBeDefined()
    await ui.press({ key: 'prev' })
    await ui.press({ key: 'prev' })
    await ui.press({ key: 'prev' })
    await ui.press({ key: 'prev' })
    expect(await ui.find({ type: 'Text', text: '1 of 5' })).toBeDefined()
    await ui.unmount()
  }
})

test('Send opens the Wenext page at the draft: the click that sends is there, not here', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    rec.processes.length = 0
    await ui.press({ key: 'send' })
    expect(rec.processes).toEqual([['open', `${ROOM_URL}#szkic-${REV_A}`]])
    expect(await ui.find({ type: 'Text', text: /approving there is the click that sends it/ })).toBeDefined()
    await ui.unmount()
  }
  // And nothing in the mod asked the agent to send: no prompt was submitted by a press on Send.
  expect(rec.prompts).toEqual([])
})

test('the opener falls back to xdg-open, and to the clipboard when neither exists', async ($, on) => {
  const rec = arm(on, { exit: 1 })
  answerRoom(on, room())
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'send' })
  expect(rec.processes.map((argv) => argv[0])).toEqual(['open', 'xdg-open'])
  expect(rec.copied).toEqual([`${ROOM_URL}#szkic-${REV_A}`])
  expect(await ui.find({ type: 'Text', text: /The link is copied/ })).toBeDefined()
})

test('Copy puts the reply on the clipboard, word for word', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'copy' })
  expect(rec.copied.length).toBe(1)
  expect(rec.copied[0]).toMatch(/^First users rarely come from one place\./)
  expect(rec.copied[0]).toContain('\n\nI only mention the tool when nobody asks.')
})

test('Edit opens a one line field; Enter asks the AGENT to save a draft, in the person\'s own words', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'edit' })
  const field = await ui.find({ key: 'edit' })
  expect(field).toBeDefined()
  expect(JSON.stringify(field)).toContain('↵')
  await ui.input({ key: 'edit', text: 'Answering one live thread a day worked for me. ↵ ↵ I mention the tool only when asked.' })
  expect(rec.prompts.length).toBe(1)
  const asked = rec.prompts[0] ?? ''
  expect(asked).toContain('draft.create')
  expect(asked).toContain('https://www.reddit.com/r/SaaS/comments/zz3/boring/')
  expect(asked).toContain('Answering one live thread a day worked for me.\n\nI mention the tool only when asked.')
  // A DRAFT, never a publication: the sentence forbids it and names no tool that sends.
  expect(asked).toContain('publish nothing')
  expect(asked).not.toMatch(/send_draft|send_token|drafts\.publish|approve/i)
  expect(await ui.find({ type: 'Text', text: /Asked the agent to save your text as a new draft/ })).toBeDefined()
  // The buttons of that person rest until the agent shows the room again.
  expect(await ui.find({ key: 'send' })).toBeUndefined()
})

test('Edit refuses an empty text, no change, and the long dash, before the agent is asked', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'edit' })
  await ui.input({ key: 'edit', text: '   ' })
  expect(await ui.find({ type: 'Text', text: 'The text is empty.' })).toBeDefined()
  const same = String.raw`First users rarely come from one place. What worked for me was answering threads where someone already had the problem. ↵ ↵ I only mention the tool when nobody asks.`
  await ui.input({ key: 'edit', text: same })
  expect(await ui.find({ type: 'Text', text: 'Nothing changed in the text.' })).toBeDefined()
  const dash = String.fromCharCode(0x2014)
  await ui.input({ key: 'edit', text: `A better answer ${dash} really` })
  expect(await ui.find({ type: 'Text', text: /instead of the long dash/ })).toBeDefined()
  // What was typed is kept in the field, so a refusal costs the person nothing.
  expect(JSON.stringify(await ui.find({ key: 'edit' }))).toContain('A better answer')
  expect(rec.prompts).toEqual([])
})

test('Skip asks the agent to record the conversation as skipped, and says so', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'skip' })
  expect(rec.prompts.length).toBe(1)
  expect(rec.prompts[0]).toContain('opportunities.mark')
  expect(rec.prompts[0]).toContain('pomin=true')
  expect(rec.prompts[0]).toContain('https://www.reddit.com/r/SaaS/comments/zz3/boring/')
  expect(await ui.find({ type: 'Text', text: /Asked the agent to skip this conversation/ })).toBeDefined()
})

test('the footer shows each account\'s health and Reconnect only where it helps', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect(await ui.find({ type: 'Text', text: 'u/demo_founder' })).toBeDefined()
    expect(await ui.find({ key: 'reconnect-a1' })).toBeUndefined()
    expect(await ui.find({ key: 'reconnect-a2' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /2 accounts · 4\/h · 20\/day/ })).toBeDefined()
    rec.processes.length = 0
    await ui.press({ key: 'reconnect-a2' })
    // Not the one-click flow (that is Codex's): the page where the agent's browser is signed in.
    expect(rec.processes).toEqual([['open', `${DASHBOARD}/community`]])
    expect(await ui.find({ type: 'Text', text: /Sign in to X in the agent's browser there/ })).toBeDefined()
    await ui.unmount()
  }
})

test('Limits opens the Presence page', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'limits' })
  expect(rec.processes).toEqual([['open', `${DASHBOARD}/w/ws-1/p/pr-1/presence`]])
})

test('an account that was never connected says Connect, and a paused one has no button', async ($, on) => {
  const rec = arm(on)
  answerRoom(
    on,
    room({
      konta_stan: [
        { id: 'n1', platforma: 'X', konto: 'demo_founder', stan: 'nigdy', ekran: 'zamkniety', powod: null, mozna_polaczyc: true },
        { id: 'h1', platforma: 'REDDIT', konto: 'held_r', stan: 'wstrzymane', ekran: 'zamkniety', powod: null, mozna_polaczyc: false },
      ],
    }),
  )
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(JSON.stringify(await ui.find({ key: 'reconnect-n1' }))).toContain('Connect @demo_founder')
  expect(await ui.find({ key: 'reconnect-h1' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /u\/held_r · paused/ })).toBeDefined()
})

test('a language pinned by the server wins; Polish is drawn with its own plural forms', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room({ jezyk: 'pl' }))
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /3 osoby właśnie piszą o problemie, który rozwiązuje Twój kod/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'TWOJA ODPOWIEDŹ, TAK JAK WYJDZIE' })).toBeDefined()
  expect(JSON.stringify(await ui.find({ key: 'send' }))).toContain('Wyślij w Wenext')
  expect(JSON.stringify(await ui.find({ key: 'reconnect-a2' }))).toContain('Połącz ponownie @demo_founder')
})

test('the environment decides when the server pins nothing', async ($, on) => {
  const rec = arm(on, { env: { LANG: 'pl_PL.UTF-8' } })
  answerRoom(on, room())
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /3 osoby właśnie piszą o problemie, który rozwiązuje Twój kod/ })).toBeDefined()
})

test('a narrow terminal gets a band that opens the pane on a press, at any width', async ($, on) => {
  const rec = arm(on, { placed: false })
  answerRoom(on, room())
  await shown($, rec)
  expect(rec.toasts.some((text) => /Run \/wenext/.test(text))).toBe(true)
  for (const surface of SURFACES) {
    await shown($, rec) // the room again: the card goes back to the band
    const ui = await $.ui.mount({ ...BAND, surface })
    expect(await ui.find({ type: 'Text', text: /3 people are writing right now about the problem your code solves/ })).toBeDefined()
    rec.opened.length = 0
    await ui.press({ key: 'band-open' })
    expect(rec.opened[0]).toMatchObject({ id: 'wenext', focus: true, closeOnEscape: true })
    await ui.unmount()
  }
})

test('the band can be dismissed and then draws nothing of its own', async ($, on) => {
  const rec = arm(on, { placed: false })
  answerRoom(on, room())
  await shown($, rec)
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  await ui.press({ key: 'band-dismiss' })
  await ui.unmount()
  const after = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await after.find({ type: 'Text', text: /Wenext:/ })).toBeUndefined()
  expect(await after.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
})

test('an empty room with a lost session still shows the way out', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room({ okazje: [], szkice: [], wyniki: { okno_dni: 7, pozycje: [] } }))
  await shown($, rec)
  expect(rec.opened.length).toBe(1)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: 'Nobody yet.' })).toBeDefined()
  expect(await ui.find({ key: 'reconnect-a2' })).toBeDefined()
})

test('an empty room with nothing to fix opens nothing', async ($, on) => {
  const rec = arm(on)
  answerRoom(
    on,
    room({
      okazje: [],
      szkice: [],
      konta_stan: [{ id: 'a1', platforma: 'X', konto: 'demo_founder', stan: 'polaczone', ekran: 'zamkniety', powod: null, mozna_polaczyc: false }],
    }),
  )
  await shown($, rec)
  expect(rec.opened).toEqual([])
})

test('hosted Wenext: no accounts, no scope, no Edit; the post is made by hand', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room({ wysylka: 'reczna' }))
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ key: 'reconnect-a2' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /accounts ·/ })).toBeUndefined()
  expect(await ui.find({ key: 'limits' })).toBeUndefined()
  expect(await ui.find({ key: 'edit' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /Wenext sends nothing in this mode/ })).toBeDefined()
  rec.processes.length = 0
  await ui.press({ key: 'send' })
  expect(rec.copied[0]).toMatch(/^First users rarely come from one place/)
  expect(rec.processes).toEqual([['open', 'https://www.reddit.com/r/SaaS/comments/zz3/boring/']])
})

test('only the room tool of the local server is read; a failed or foreign result is not', async ($, on) => {
  const rec = arm(on)
  on('tool.call', () => ({ result: 'not json', text: 'not json' }))
  await $.tool.call({ tool: TOOL })
  await $.tool.call({ tool: 'mcp__plugin_wenext_wenext__room_other' })
  await $.tool.call({ tool: 'mcp__plugin_wenext-cloud_wenext__room_show' })
  await $.tool.call({ tool: 'mcp__github__room_show' })
  expect(rec.opened).toEqual([])
})

test('a refusal from the server draws no card', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, { stan: 'NIEDOSTEPNE', powod: 'x' })
  await shown($, rec)
  expect(rec.opened).toEqual([])
})

test('the same person stays on screen when the agent shows the room again', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'next' })
  expect(await ui.find({ type: 'Text', text: '2 of 5' })).toBeDefined()
  await ui.unmount()
  await shown($, rec)
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await again.find({ type: 'Text', text: '2 of 5' })).toBeDefined()
})

test('the command opens the pane with the keyboard, or says there is no card yet', async ($, on) => {
  const rec = arm(on)
  const answer = answerRoom(on, 'no card yet')
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const none = (await $.command.run({ command: 'wenext', args: '' } as never)) as { text?: string }
  // No card yet: the command asks the agent to show the room (a read), in the person's own words.
  expect(none.text).toMatch(/Asking the agent to show your room/)
  await rec.clock.advance(100)
  expect(rec.prompts).toEqual(['Show me my Wenext room: call room.show.'])
  answer.set(room())
  await shown($, rec)
  rec.opened.length = 0
  await $.command.run({ command: 'wenext', args: '' } as never)
  expect(rec.opened[0]).toMatchObject({ id: 'wenext', focus: true, closeOnEscape: true })
})
