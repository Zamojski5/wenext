// Fictional data and the stubs every test of the mod needs. Not a test file (it holds no test()).

import { mock } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

/** The kit types a stub by the engine event it answers; the stubs here answer with `{ value }`, as its docs show. */
type Loose = (name: string, hook: (_$: unknown, e: any) => unknown) => void

export const TOOL = 'mcp__plugin_wenext_wenext__room_show'
export const NOW = Date.parse('2026-10-12T09:00:00.000Z')
export const DASHBOARD = 'http://127.0.0.1:3000'
export const ROOM_URL = `${DASHBOARD}/w/ws-1/p/pr-1/room`
export const REV_A = '11111111-1111-4111-8111-111111111111'
export const REV_B = '22222222-2222-4222-8222-222222222222'
export const REV_C = '33333333-3333-4333-8333-333333333333'

export const PANE = {
  plugin: 'wenext',
  component: 'Pane',
  requestId: 'wenext',
  viewport: { columns: 120, rows: 40 },
  props: {
    title: 'Wenext',
    isFocused: true,
    bodyColumns: 100,
    placement: 'inline',
    scroll: { offset: 0, bodyRows: 30 },
    view: {},
  },
} as const

export const BAND = {
  plugin: 'wenext',
  component: 'AbovePrompt',
  requestId: 'band',
  viewport: { columns: 100, rows: 40 },
  props: {
    hasSurvey: false,
    isWorking: false,
    maxRows: 6,
    bodyColumns: 98,
    scroll: { offset: 0, bodyRows: 4 },
    view: {},
  },
} as const

const REPLY_TEXT =
  'First users rarely come from one place. What worked for me was answering threads where someone already had the problem.\n\nI only mention the tool when nobody asks.'

export function minutesAgo(minutes: number): string {
  return new Date(NOW - minutes * 60000).toISOString()
}

/** The result of `room.show` of a local install: two drafts, a new person, a reply that went out. */
export function room(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    stan: 'WYKONANE',
    okazje: [
      {
        id: 'o1',
        platforma: 'REDDIT',
        spolecznosc: 'r/SaaS',
        tytul: 'What is a boring marketing channel that works for dev tools?',
        fragment: 'We built a CLI and nobody finds it. Where do people actually go?',
        autor: 'quiet_maker',
        url: 'https://www.reddit.com/r/SaaS/comments/zz3/boring/',
        utworzono: minutesAgo(180),
        dlaczego: 'Asks exactly what the project does.',
        kat: 'Tell how you found your own first users.',
        stan: 'SZKIC',
        szkic: REV_A,
      },
      {
        id: 'o2',
        platforma: 'X',
        spolecznosc: 'X',
        tytul: 'Anyone using agents for distribution? Tried three tools, none stuck.',
        fragment: 'Anyone using agents for distribution? Tried three tools, none stuck.',
        autor: 'builder_x',
        url: 'https://x.com/builder_x/status/1000000000000000001',
        utworzono: minutesAgo(240),
        stan: 'SZKIC',
        szkic: REV_B,
      },
      {
        id: 'o3',
        platforma: 'REDDIT',
        spolecznosc: 'r/SideProject',
        tytul: 'How do you find your first ten users?',
        fragment: 'I built a small tool for invoices and nobody knows it exists.',
        autor: 'late_dev',
        url: 'https://www.reddit.com/r/SideProject/comments/zz9/first/',
        utworzono: minutesAgo(300),
        stan: 'NOWA',
        szkic: null,
      },
      {
        id: 'o4',
        platforma: 'REDDIT',
        spolecznosc: 'r/indiehackers',
        tytul: 'Launch flopped, what now?',
        fragment: '',
        autor: 'maker_pl',
        url: 'https://www.reddit.com/r/indiehackers/comments/zz5/flopped/',
        utworzono: minutesAgo(4000),
        stan: 'ODPOWIEDZIANO',
        szkic: null,
      },
    ],
    szkice: [
      {
        revision_id: REV_A,
        okazja_id: 'o1',
        platforma: 'REDDIT',
        konto: 'demo_founder',
        spolecznosc: 'r/SaaS',
        rodzaj: 'REPLY',
        tytul: '',
        tekst: REPLY_TEXT,
        w_odpowiedzi_na: { url: 'https://www.reddit.com/r/SaaS/comments/zz3/boring/', tytul: 'x', autor: 'quiet_maker' },
        stan: 'czeka_na_zgode',
        wygasa: new Date(NOW + 130 * 60000).toISOString(),
        odcisk: 'a'.repeat(64),
      },
      {
        revision_id: REV_B,
        okazja_id: 'o2',
        platforma: 'X',
        konto: 'demo_founder',
        spolecznosc: 'X',
        rodzaj: 'REPLY',
        tytul: '',
        tekst: 'Tools alone did not stick for me either.',
        w_odpowiedzi_na: { url: 'https://x.com/builder_x/status/1000000000000000001', tytul: 'x', autor: 'builder_x' },
        stan: 'czeka_na_zgode',
        wygasa: new Date(NOW + 200 * 60000).toISOString(),
        odcisk: 'b'.repeat(64),
      },
      {
        revision_id: REV_C,
        okazja_id: null,
        platforma: 'REDDIT',
        konto: 'demo_founder',
        spolecznosc: 'r/SideProject',
        rodzaj: 'OWN_POST',
        tytul: 'I shipped a tiny tool',
        tekst: 'The first ten users came from replies.',
        w_odpowiedzi_na: null,
        stan: 'czeka_na_zgode',
        wygasa: new Date(NOW + 300 * 60000).toISOString(),
        odcisk: 'c'.repeat(64),
      },
    ],
    wyniki: {
      okno_dni: 7,
      pozycje: [
        {
          url: 'https://www.reddit.com/r/indiehackers/comments/zz5/flopped/yy1/',
          okazja_id: 'o4',
          platforma: 'REDDIT',
          tekst: 'Start with one thread a day.',
          widocznosc: 'CONFIRMED',
          glosy: 41,
          odpowiedzi: null,
          opublikowano: minutesAgo(2800),
        },
      ],
    },
    pominiete: [],
    pominiete_lacznie: 5,
    pokoj: '/w/ws-1/p/pr-1/room',
    pokoj_adres: ROOM_URL,
    obecnosc: '/w/ws-1/p/pr-1/presence',
    obecnosc_adres: `${DASHBOARD}/w/ws-1/p/pr-1/presence`,
    wysylka: 'karta',
    zakres: {
      stan: 'AKTYWNY',
      tryb: 'REVIEW',
      wygasa: new Date(NOW + 14 * 86400000).toISOString(),
      konta: [{ platforma: 'REDDIT', konto: 'demo_founder' }, { platforma: 'X', konto: 'demo_founder' }],
      limity: { godzina: 4, doba: 20, wlasne_doba: 1 },
    },
    konta_stan: [
      { id: 'a1', platforma: 'REDDIT', konto: 'demo_founder', stan: 'polaczone', ekran: 'zamkniety', powod: null, mozna_polaczyc: false },
      { id: 'a2', platforma: 'X', konto: 'demo_founder', stan: 'utracona', ekran: 'zamkniety', powod: null, mozna_polaczyc: true },
    ],
    konta_adres: `${DASHBOARD}/community`,
    ...over,
  }
}

export type Rec = {
  /** The mock clock: time moves only when a test moves it. */
  clock: ReturnType<typeof mock.clock>
  processes: string[][]
  copied: string[]
  prompts: string[]
  toasts: string[]
  opened: Record<string, unknown>[]
}

type Answers = { placed?: boolean; exit?: number; env?: Record<string, string>; copy?: boolean }

/** The stubs: what Claude Code would answer, and a record of what the mod asked of it. */
export type KitOn = Parameters<typeof mock.clock>[0]

export function arm(kit: KitOn, answers: Answers = {}): Rec {
  const on = kit as unknown as Loose
  const clock = mock.clock(kit, { now: NOW })
  const rec: Rec = { clock, processes: [], copied: [], prompts: [], toasts: [], opened: [] }
  mock.env(kit, answers.env ?? { LANG: 'en_US.UTF-8' })
  on('command.register', () => ({ value: undefined }))
  on('session.start', () => ({ cwd: '/work' }))
  on('ui.open', (_$, e) => {
    rec.opened.push(e as unknown as Record<string, unknown>)
    return { value: { isPlaced: answers.placed ?? true } }
  })
  on('ui.toast', (_$, e) => {
    rec.toasts.push(e.text)
    return { value: undefined }
  })
  on('process.run', (_$, e) => {
    rec.processes.push([...e.argv])
    return { value: { exitCode: answers.exit ?? 0, stdout: '', stderr: '' } }
  })
  on('ui.copy', (_$, e) => {
    rec.copied.push(e.text)
    return { value: { isCopied: answers.copy ?? true } }
  })
  on('prompt.submit', (_$, e) => {
    rec.prompts.push(e.text)
    return { text: e.text }
  })
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
  return rec
}

/** The model calls `room.show`; Claude Code answers with the payload set last. */
export function answerRoom(kit: KitOn, payload: unknown): { set: (next: unknown) => void } {
  let current = payload
  const on = kit as unknown as Loose
  on('tool.call', () => {
    const text = typeof current === 'string' ? current : JSON.stringify(current)
    return { result: text, text }
  })
  return {
    set: (next) => {
      current = next
    },
  }
}

/** The model calls the room tool of the local server, and the first drawing (the reveal) runs its course. */
export async function shown($: Engine, rec: Rec, settle = true) {
  const ran = await $.tool.call({ tool: TOOL })
  if (settle) await rec.clock.advance(2500)
  return ran
}
