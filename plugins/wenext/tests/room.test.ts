// The pure parts of the mod: reading the card, the one-line field, the words. No engine involved (the kit is
// imported only because a test file needs one test()).

import { expect, test } from 'claude-code/testing'

import { fromLine, hasLongDash, minutesSince, parseRoom, reconcile, stepped, summary, toLine, web, FRESH_UI } from '../hooks/room'
import { WORDS, ago, formIndex, langOf, say, sayN } from '../hooks/words'
import { NOW, REV_A, room } from './fixtures'

const parsed = (over: Record<string, unknown> = {}) => parseRoom(JSON.stringify(room(over)), NOW)

test('the people come in the order of the Codex card: a draft, an own post, a new person, a reply that went out', () => {
  const view = parsed()
  expect(view).not.toBeNull()
  expect(view?.people.map((person) => [person.key, person.stage])).toEqual([
    ['oo1', 'draft'],
    ['oo2', 'draft'],
    [`d${'33333333-3333-4333-8333-333333333333'}`, 'draft'],
    ['oo3', 'new'],
    ['oo4', 'posted'],
  ])
  expect(view?.people[0]?.reply?.rev).toBe(REV_A)
  expect(view?.people[0]?.where).toBe('r/SaaS')
  expect(view?.people[1]?.where).toBe('X')
  // The own post has no conversation of its own, and is not one of the people to answer.
  expect(summary(view!, NOW).open).toBe(3)
  expect(summary(view!, NOW).waiting).toBe(3)
  expect(summary(view!, NOW).freshest).toBe(180)
})

test('a number nobody measured stays null, never 0', () => {
  const posted = parsed()?.people.find((person) => person.stage === 'posted')
  expect(posted?.outcome?.votes).toBe(41)
  expect(posted?.outcome?.replies).toBeNull()
})

test('hosted data shows no accounts and no scope whatever the server sent', () => {
  const view = parsed({ wysylka: 'reczna' })
  expect(view?.manual).toBe(true)
  expect(view?.accounts).toEqual([])
  expect(view?.scope).toBeNull()
  expect(view?.links.presence).toBeNull()
  expect(view?.links.accounts).toBeNull()
})

test('junk is not a card: not JSON, a refusal, another shape', () => {
  for (const text of [undefined, '', 'not json', '[]', '{"stan":"NIEDOSTEPNE"}', '{"stan":"WYKONANE"}', '{"stan":"WYKONANE","okazje":"x"}']) {
    expect(parseRoom(text, NOW)).toBeNull()
  }
  // A card with nothing in it is a card: the footer may still have something to say.
  expect(parseRoom('{"stan":"WYKONANE","okazje":[],"szkice":[]}', NOW)?.people).toEqual([])
})

test('fields of the wrong kind are dropped, not invented', () => {
  const view = parsed({
    okazje: [{ id: 'x1', platforma: 'REDDIT', tytul: 42, url: 'javascript:alert(1)', stan: 'NOWA' }, { nope: true }, 'text', null],
    szkice: [{ revision_id: 7, tekst: 'x' }],
    konta_stan: [{ konto: 'a', stan: 'weird' }, { konto: '' }, null],
    zakres: 'x',
  })
  expect(view?.people.length).toBe(1)
  expect(view?.people[0]?.title).toBe('')
  expect(view?.people[0]?.url).toBeNull()
  expect(view?.accounts).toEqual([{ id: 'a', platform: 'REDDIT', handle: 'a', state: 'nieznana', canConnect: false }])
  expect(view?.scope).toBeNull()
})

test('only an http(s) address is ever opened', () => {
  expect(web('https://x.com/a/status/1')).toBe('https://x.com/a/status/1')
  expect(web('http://127.0.0.1:3000/w/1')).toBe('http://127.0.0.1:3000/w/1')
  for (const bad of ['javascript:alert(1)', 'file:///etc/passwd', '-n https://x.com', 'https://x.com/a b', '', null, undefined, 42, `https://x.com/${'a'.repeat(2100)}`]) {
    expect(web(bad)).toBeNull()
  }
})

test('the dashboard links come from the address the launcher gave, and the accounts page has a default', () => {
  const view = parsed({ konta_adres: undefined })
  expect(view?.links.accounts).toBe('http://127.0.0.1:3000/community')
  expect(parsed({ pokoj_adres: undefined, konta_adres: undefined })?.links.accounts).toBeNull()
})

test('a reply goes to the one line field and back, a break shown as the arrow', () => {
  const text = 'One.\n\nTwo.\nThree.'
  expect(toLine(text)).toBe('One. ↵↵ Two. ↵ Three.')
  expect(fromLine('One. ↵ ↵ Two.')).toBe('One.\n\nTwo.')
  expect(fromLine(toLine(text))).toBe(text)
  expect(fromLine('  spaced out  ')).toBe('spaced out')
})

test('the founder\'s rule on the long dash is checked by code point', () => {
  expect(hasLongDash(`a ${String.fromCharCode(0x2014)} b`)).toBe(true)
  expect(hasLongDash(`a ${String.fromCharCode(0x2013)} b`)).toBe(true)
  expect(hasLongDash('a - b, c: d.')).toBe(false)
})

test('a new reading keeps the same person and lets go of what was about the old one', () => {
  const view = parsed()!
  const moved = stepped({ ...FRESH_UI, where: 'pane' }, view, 2)
  expect(moved.page).toBe(2)
  const open = { ...moved, editing: REV_A, draftText: 'x', note: 'n', pending: { a: 'skip' as const }, why: moved.key }
  const next = reconcile(open, view)
  expect(next.key).toBe(moved.key)
  expect(next.page).toBe(2)
  expect(next.editing).toBeNull()
  expect(next.draftText).toBeNull()
  expect(next.note).toBeNull()
  expect(next.pending).toEqual({})
  expect(next.why).toBe(moved.key)
  // The person is gone (skipped): the page is kept in range.
  const fewer = parseRoom(JSON.stringify(room({ okazje: [], szkice: [] })), NOW)!
  expect(reconcile(open, fewer).page).toBe(0)
  // Stepping stops at the ends.
  expect(stepped(FRESH_UI, view, -5).page).toBe(0)
  expect(stepped(FRESH_UI, view, 99).page).toBe(view.people.length - 1)
})

test('times are told the way the card tells them', () => {
  expect(minutesSince(new Date(NOW - 90 * 60000).toISOString(), NOW)).toBe(90)
  expect(minutesSince('not a time', NOW)).toBeNull()
  expect(ago('en', 1)).toBe('just now')
  expect(ago('en', 59)).toBe('59 min ago')
  expect(ago('en', 180)).toBe('3 h ago')
  expect(ago('pl', 3000)).toBe('2 dni temu')
  expect(ago('en', 1500)).toBe('yesterday')
})

test('the two dictionaries have the same keys and the same placeholders, and no long dash', () => {
  const en = Object.keys(WORDS.en).sort()
  expect(Object.keys(WORDS.pl).sort()).toEqual(en)
  const holes = (value: string | string[]) => [...new Set((Array.isArray(value) ? value.join(' ') : value).match(/\{\w+\}/g) ?? [])].sort()
  for (const key of en) {
    const a = WORDS.en[key]!
    const b = WORDS.pl[key]!
    expect(holes(a)).toEqual(holes(b))
    // English has one form and the rest, Polish one, two to four and the rest.
    if (Array.isArray(a)) expect(a.length).toBe(2)
    if (Array.isArray(b)) expect(b.length).toBe(3)
    expect(hasLongDash(JSON.stringify([a, b]))).toBe(false)
  }
})

test('plural forms: Polish has three, English two, and 12 to 14 are the third form', () => {
  expect([1, 2, 4, 5, 12, 14, 22, 25].map((n) => formIndex('pl', n))).toEqual([0, 1, 1, 2, 2, 2, 1, 2])
  expect([1, 2].map((n) => formIndex('en', n))).toEqual([0, 1])
  expect(sayN('pl', 'head.nowStrong', 3)).toBe('3 osoby')
  expect(sayN('pl', 'head.nowStrong', 5)).toBe('5 osób')
  expect(sayN('en', 'head.nowStrong', 1)).toBe('1 person')
  expect(sayN('en', 'head.nowRest', 1)).toBe(' is writing right now about the problem your code solves.')
  expect(say('en', 'no.such.key')).toBe('no.such.key')
})

test('the language: the pinned one first, then the locale, Polish only for Polish', () => {
  expect(langOf('en', 'pl_PL.UTF-8')).toBe('en')
  expect(langOf('pl', 'en_US.UTF-8')).toBe('pl')
  expect(langOf(null, 'pl_PL.UTF-8')).toBe('pl')
  expect(langOf(null, 'pl')).toBe('pl')
  expect(langOf(null, 'en_US.UTF-8')).toBe('en')
  expect(langOf(null, undefined)).toBe('en')
  expect(langOf(null, 'polish')).toBe('en')
})
