// The parts of the mod that are shown to people: the first drawing (a short reveal), the brand and the
// platform glyphs, the share line that stays out of the way, and the demo sandbox where the whole send is
// rehearsed in the pane, said to be a demo, calling nothing.

import { expect, test } from 'claude-code/testing'

import { clipLines, budget, wrapped } from '../hooks/draw'
import { PANE, answerRoom, arm, room, shown } from './fixtures'

const SURFACES = ['terminal', 'desktop'] as const

test('the first card of a session opens with a short reveal of what was found, then the card', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec, false)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    // 1. Searching: a spinner and the words of the card. The card is not there yet.
    expect(await ui.find({ type: 'Text', text: 'Searching threads' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /are writing right now/ })).toBeUndefined()
    await ui.unmount()
  }
  await rec.clock.advance(900)
  const read = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // 2. Read N threads on the places, and the first few conversations as tiles.
  expect(await read.find({ type: 'Text', text: 'Read 9 threads on r/SaaS, X, r/SideProject +1' })).toBeDefined()
  expect(await read.find({ type: 'Text', text: /What is a boring marketing channel/ })).toBeDefined()
  await read.unmount()
  await rec.clock.advance(1600)
  const card = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // 3. The card, with the headline in the card's own words.
  expect(await card.find({ type: 'Text', text: /3 people are writing right now about the problem your code solves\./ })).toBeDefined()
  expect(await card.find({ type: 'Text', text: 'Searching threads' })).toBeUndefined()
})

test('the spinner turns while it searches', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec, false)
  const first = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const before = JSON.stringify(await first.findAll({ type: 'Text' }))
  await first.unmount()
  await rec.clock.advance(300)
  const second = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(JSON.stringify(await second.findAll({ type: 'Text' }))).not.toBe(before)
})

test('the reveal is once per session: the room shown again goes straight to the card', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec)
  await shown($, rec, false)
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await again.find({ type: 'Text', text: 'Searching threads' })).toBeUndefined()
  expect(await again.find({ type: 'Text', text: /are writing right now/ })).toBeDefined()
})

test('a pane that cannot be seated does not spend the reveal', async ($, on) => {
  const rec = arm(on, { placed: false })
  answerRoom(on, room())
  await shown($, rec, false)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: 'Searching threads' })).toBeUndefined()
})

test('the card carries the brand mark and the platforms\' own glyphs', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect(await ui.find({ type: 'Text', text: '◢◣' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'Wenext' })).toBeDefined()
    // The first person is on Reddit: its orange dot. The second is on X: the real X glyph.
    expect(await ui.find({ type: 'Text', text: '●' })).toBeDefined()
    await ui.press({ key: 'next' })
    expect(await ui.find({ type: 'Text', text: '\u{1d54f}' })).toBeDefined()
    await ui.press({ key: 'prev' })
    await ui.unmount()
  }
})

test('the share line is there when there is room for it, and out of the way when there is not', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec)
  const share = { type: 'Text', text: 'Nothing posts without your click · getwenext.com' } as const
  const wide = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await wide.find(share)).toBeDefined()
  // While a message is on screen, or the field is open, it steps aside.
  await wide.press({ key: 'copy' })
  expect(await wide.find(share)).toBeUndefined()
  await wide.press({ key: 'edit' })
  expect(await wide.find(share)).toBeUndefined()
  await wide.unmount()
  // Narrow, or short: not drawn.
  const narrow = await $.ui.mount({ ...PANE, props: { ...PANE.props, bodyColumns: 50 }, surface: 'terminal' })
  expect(await narrow.find(share)).toBeUndefined()
  await narrow.unmount()
  const short = await $.ui.mount({ ...PANE, props: { ...PANE.props, scroll: { offset: 0, bodyRows: 12 } }, surface: 'terminal' })
  expect(await short.find(share)).toBeUndefined()
})

test('the share line is in Polish for a Polish card', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room({ jezyk: 'pl' }))
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: 'Nic nie wyjdzie bez Twojego kliknięcia · getwenext.com' })).toBeDefined()
})

// -- the demo sandbox: the whole send, rehearsed ----------------------------------------------------------

test('on the demo sandbox the whole send is rehearsed in the pane, said to be a demo, and calls nothing', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room({ demo: true }))
  await shown($, rec)
  for (const surface of SURFACES) {
    await shown($, rec) // the room again: the rehearsal starts from the card
    const ui = await $.ui.mount({ ...PANE, surface })
    expect(await ui.find({ type: 'Text', text: '· demo sandbox' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Demo data: nothing here can reach a platform/ })).toBeDefined()
    expect(JSON.stringify(await ui.find({ key: 'send' }))).toContain('"Send"')
    // 1. Send: the consent, in a sentence, with Yes and Cancel.
    await ui.press({ key: 'send' })
    expect(await ui.find({ type: 'Text', text: 'Send from u/demo_founder? It goes out exactly as you see it.' })).toBeDefined()
    expect(await ui.find({ key: 'send' })).toBeUndefined()
    // Cancel puts the card back.
    await ui.press({ key: 'demo-cancel' })
    expect(await ui.find({ key: 'send' })).toBeDefined()
    // 2. Yes: queued with the publisher (demo).
    await ui.press({ key: 'send' })
    await ui.press({ key: 'demo-yes' })
    expect(await ui.find({ type: 'Text', text: 'Sent to the publisher (demo).' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /nothing leaves this machine/ })).toBeDefined()
    // 3. Posted (demo), a moment later.
    await rec.clock.advance(2300)
    expect(await ui.find({ type: 'Text', text: 'Posted (demo)' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /A rehearsal/ })).toBeDefined()
    await ui.unmount()
  }
  // IT DID NOTHING REAL: no program ran, nothing was copied, no message went to the agent.
  expect(rec.processes).toEqual([])
  expect(rec.copied).toEqual([])
  expect(rec.prompts).toEqual([])
})

test('Yes does nothing unless the sentence was shown first, and a new room puts the card back', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room({ demo: true }))
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // No consent yet: there is no Yes to press.
  expect(await ui.find({ key: 'demo-yes' })).toBeUndefined()
  await ui.press({ key: 'send' })
  await ui.press({ key: 'demo-yes' })
  await ui.unmount()
  await shown($, rec) // the agent shows the room again mid-rehearsal
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await again.find({ type: 'Text', text: /Posted \(demo\)|Sent to the publisher/ })).toBeUndefined()
  expect(await again.find({ key: 'send' })).toBeDefined()
})

test('on any other data Send opens the Wenext page and there is no rehearsal', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: '· demo sandbox' })).toBeUndefined()
  await ui.press({ key: 'send' })
  expect(await ui.find({ key: 'demo-yes' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /Send from u\/demo_founder/ })).toBeUndefined()
  expect(rec.processes.length).toBe(1)
})

test('the demo flag is the server\'s word alone: not a string, not on hosted data', async ($, on) => {
  const rec = arm(on)
  const answer = answerRoom(on, room({ demo: 'true' }))
  await shown($, rec)
  let ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: '· demo sandbox' })).toBeUndefined()
  await ui.unmount()
  answer.set(room({ demo: true, wysylka: 'reczna' }))
  await shown($, rec)
  ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: '· demo sandbox' })).toBeUndefined()
  expect(await ui.find({ key: 'send' })).toBeDefined()
  await ui.press({ key: 'send' })
  expect(await ui.find({ key: 'demo-yes' })).toBeUndefined()
})

test('the demo badge and the rehearsal speak Polish for a Polish card', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room({ demo: true, jezyk: 'pl' }))
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: '· piaskownica demo' })).toBeDefined()
  await ui.press({ key: 'send' })
  expect(await ui.find({ type: 'Text', text: 'Wysłać z u/demo_founder? Wyjdzie dokładnie tak, jak je widzisz.' })).toBeDefined()
  await ui.press({ key: 'demo-yes' })
  await rec.clock.advance(2300)
  expect(await ui.find({ type: 'Text', text: 'Opublikowano (demo)' })).toBeDefined()
})

// -- /wenext: the card the person asked for takes the keys, the one the agent showed on its own does not ------

test('a card asked for with /wenext takes the keyboard, so one keystroke is the whole demo', async ($, on) => {
  const rec = arm(on)
  const answer = answerRoom(on, 'no card yet')
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await $.command.run({ command: 'wenext', args: '' } as never)
  await rec.clock.advance(100)
  answer.set(room())
  rec.opened.length = 0
  await shown($, rec, false)
  expect(rec.opened[0]).toMatchObject({ id: 'wenext', focus: true, closeOnEscape: true })
  // That was once: the agent showing the room again does not take the keys.
  rec.opened.length = 0
  await shown($, rec, false)
  expect(rec.opened[0]).toEqual({ id: 'wenext', title: 'Wenext' })
})

test('a card the agent showed on its own never takes the keyboard from the prompt', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await shown($, rec, false)
  expect(rec.opened[0]).toEqual({ id: 'wenext', title: 'Wenext' })
})

test('a /wenext that was forgotten (two minutes on) does not make the next card take the keys', async ($, on) => {
  const rec = arm(on)
  const answer = answerRoom(on, 'no card yet')
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await $.command.run({ command: 'wenext', args: '' } as never)
  await rec.clock.advance(125000)
  answer.set(room())
  rec.opened.length = 0
  await shown($, rec, false)
  expect(rec.opened[0]).toEqual({ id: 'wenext', title: 'Wenext' })
})

// -- the card fits the pane: Send stays on screen ----------------------------------------------------------------

test('words are broken the way the terminal breaks them, and a cut ends in an ellipsis', () => {
  expect(wrapped('one two three', 7)).toEqual(['one two', 'three'])
  expect(wrapped('a\n\nb', 10)).toEqual(['a', '', 'b'])
  expect(wrapped('abcdefghij', 4)).toEqual(['abcd', 'efgh', 'ij'])
  expect(clipLines('one two three', 20, 3)).toEqual({ text: 'one two three', cut: false })
  expect(clipLines('one two three four five', 9, 2)).toEqual({ text: 'one two\nthree…', cut: true })
  // Never a dangling comma before the ellipsis, never a blank line before it.
  expect(clipLines('first line.\n\nsecond', 20, 2).text).toBe('first line…')
})

test('a short pane clips the reply to what leaves room for Send, a tall one shows all of it', async ($, on) => {
  const long = Array.from({ length: 24 }, (_, i) => `Line number ${i + 1} of a rather long reply.`).join('\n')
  const rec = arm(on)
  const view = room()
  ;(view.szkice as { tekst: string }[])[0]!.tekst = long
  answerRoom(on, view)
  await shown($, rec)
  const short = await $.ui.mount({ ...PANE, props: { ...PANE.props, scroll: { offset: 0, bodyRows: 40 } }, surface: 'terminal' })
  expect(await short.find({ type: 'Text', text: /Line number 1 of/ })).toBeDefined()
  expect(await short.find({ type: 'Text', text: /Line number 24 of/ })).toBeUndefined()
  expect(await short.find({ type: 'Text', text: /Show all/ })).toBeUndefined()
  expect(JSON.stringify(await short.find({ key: 'more' }))).toContain('Show all')
  expect(await short.find({ key: 'send' })).toBeDefined()
  await short.unmount()
  const tall = await $.ui.mount({ ...PANE, props: { ...PANE.props, scroll: { offset: 0, bodyRows: 90 } }, surface: 'terminal' })
  expect(await tall.find({ type: 'Text', text: /Line number 24 of/ })).toBeDefined()
  expect(await tall.find({ key: 'more' })).toBeUndefined()
})

test('the room for the reply grows with the pane, and the Send button is never pushed out of a pane of 40 rows', () => {
  const at = (rows: number) => budget({ columns: 72, rows } as never)
  expect(at(30).reply).toBe(5)
  expect(at(40).reply).toBeLessThan(at(60).reply)
  // About 34 rows of chrome, measured in a real Claude Code, plus the excerpt and the reply.
  for (const rows of [40, 46, 60, 80]) {
    const room = at(rows)
    expect(34 + room.body + room.reply).toBeLessThanOrEqual(Math.max(rows, 34 + room.body + 5))
  }
})

test('Esc on a half written edit is a cancel: /wenext opens the card again, not the field', async ($, on) => {
  const rec = arm(on)
  answerRoom(on, room())
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await shown($, rec)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'edit' })
  expect(await ui.find({ key: 'edit-cancel' })).toBeDefined()
  await ui.unmount()
  // The pane was closed with Esc; the person types the command again.
  await $.command.run({ command: 'wenext', args: '' } as never)
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await again.find({ key: 'edit-cancel' })).toBeUndefined()
  expect(await again.find({ key: 'send' })).toBeDefined()
})
