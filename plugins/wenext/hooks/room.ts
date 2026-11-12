// Reading the card the agent drew (the result of the `room.show` tool) into what the pane draws.
//
// PURE: no engine, no clock of its own (the caller says what time it is), nothing from the network. The
// shape in is the one `GET .../room` and the Codex card draw from (`opportunities/room.py` and
// `tools/opportunity_tools.py::_room_show` in the Wenext repository); the shape out is `RoomView` of
// `types/index.d.ts`. A field that is missing or of the wrong kind is dropped, never invented: the pane
// says less, it does not say something untrue. A number nobody measured stays null and is drawn as "not
// measured", never as 0.

import type {
  AccountHealth,
  Lang,
  Outcome,
  Person,
  Platform,
  Reply,
  RoomView,
  ScopeLine,
  UiState,
} from '../types'

type Json = Record<string, unknown>

const HEALTH: ReadonlySet<string> = new Set([
  'polaczone',
  'utracona',
  'nigdy',
  'czeka_na_ciebie',
  'inne_konto',
  'wstrzymane',
  'nieznana',
])

function obj(value: unknown): Json | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? (value as Json) : null
}

function list(value: unknown): Json[] {
  return Array.isArray(value) ? value.map(obj).filter((one): one is Json => one !== null) : []
}

function str(value: unknown, max = 4000): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value.slice(0, max) : null
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** Only an http(s) address is ever opened: they come from strangers' posts and from the platforms. */
export function web(value: unknown): string | null {
  const text = typeof value === 'string' ? value.trim() : ''
  return /^https?:\/\/[^\s]+$/i.test(text) && text.length <= 2000 ? text : null
}

function platformOf(value: unknown): Platform {
  return value === 'X' ? 'X' : 'REDDIT'
}

function replyOf(row: Json): Reply | null {
  const rev = str(row.revision_id, 80)
  const text = str(row.tekst)
  if (rev === null || text === null) return null
  const target = obj(row.w_odpowiedzi_na)
  return {
    rev,
    kind: row.rodzaj === 'OWN_POST' ? 'OWN_POST' : 'REPLY',
    platform: platformOf(row.platforma),
    account: str(row.konto, 120),
    community: str(row.spolecznosc, 120),
    title: str(row.tytul, 300) ?? '',
    text,
    threadUrl: web(target?.url) ?? web(row.watek_url),
    threadAuthor: str(target?.autor, 120),
    expires: str(row.wygasa, 40),
    intentUrl: web(row.intent_url),
  }
}

function outcomeOf(row: Json | undefined): Outcome | null {
  if (row === undefined) return null
  return {
    url: web(row.url),
    votes: num(row.glosy),
    replies: num(row.odpowiedzi),
    at: str(row.opublikowano, 40),
    text: str(row.tekst, 600) ?? '',
  }
}

function whereOf(platform: Platform, community: string | null): string {
  if (platform === 'X') return 'X'
  return community === null ? 'Reddit' : `r/${community.replace(/^r\//, '')}`
}

/** The people on the card, in the order the Codex card pages them: a draft waiting first, then a draft with
 * no conversation of its own, then a new person, then a reply that went out. */
function peopleOf(data: Json): Person[] {
  const opportunities = list(data.okazje)
  const drafts = list(data.szkice)
  const results = list(obj(data.wyniki)?.pozycje)
  const byRevision = new Map<string, Json>()
  for (const draft of drafts) {
    const rev = str(draft.revision_id, 80)
    if (rev !== null) byRevision.set(rev, draft)
  }
  const resultOf = new Map<string, Json>()
  for (const result of results) {
    const id = str(result.okazja_id, 80)
    if (id !== null && !resultOf.has(id)) resultOf.set(id, result)
  }
  const seen = new Set<string>()
  const entries: { rank: number; person: Person }[] = []
  for (const row of opportunities) {
    const id = str(row.id, 80)
    if (id === null) continue
    seen.add(id)
    const platform = platformOf(row.platforma)
    const draftRow =
      row.stan === 'SZKIC' && str(row.szkic, 80) !== null
        ? (byRevision.get(str(row.szkic, 80) as string) ?? null)
        : null
    const reply = draftRow === null ? null : replyOf(draftRow)
    const stage: Person['stage'] =
      row.stan === 'ODPOWIEDZIANO' ? 'posted' : row.stan === 'SZKIC' ? (reply ? 'draft' : 'gone') : 'new'
    const title = str(row.tytul, 300) ?? ''
    const body = str(row.fragment, 600) ?? ''
    entries.push({
      rank: stage === 'posted' ? 3 : reply ? 0 : 2,
      person: {
        key: `o${id}`,
        stage,
        platform,
        where: whereOf(platform, str(row.spolecznosc, 120)),
        author: str(row.autor, 120),
        at: str(row.utworzono, 40),
        title: title !== '' ? title : body.slice(0, 120),
        body: body === title ? '' : body,
        url: web(row.url),
        why: str(row.dlaczego, 600),
        angle: str(row.kat, 600),
        reply,
        outcome: stage === 'posted' ? outcomeOf(resultOf.get(id)) : null,
      },
    })
  }
  for (const draft of drafts) {
    const opportunity = str(draft.okazja_id, 80)
    if (opportunity !== null && seen.has(opportunity)) continue
    const reply = replyOf(draft)
    if (reply === null) continue
    entries.push({
      rank: 1,
      person: {
        key: `d${reply.rev}`,
        stage: 'draft',
        platform: reply.platform,
        where: whereOf(reply.platform, reply.community),
        author: reply.threadAuthor,
        at: null,
        title: reply.title,
        body: '',
        url: reply.threadUrl,
        why: null,
        angle: null,
        reply,
        outcome: null,
      },
    })
  }
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => a.entry.rank - b.entry.rank || a.index - b.index)
    .map((one) => one.entry.person)
}

function accountsOf(value: unknown): AccountHealth[] {
  return list(value).flatMap((row) => {
    const handle = str(row.konto, 120)
    const state = typeof row.stan === 'string' && HEALTH.has(row.stan) ? row.stan : 'nieznana'
    if (handle === null) return []
    return [
      {
        id: str(row.id, 80) ?? handle,
        platform: platformOf(row.platforma),
        handle,
        state: state as AccountHealth['state'],
        canConnect: row.mozna_polaczyc === true,
      },
    ]
  })
}

function scopeOf(value: unknown): ScopeLine | null {
  const row = obj(value)
  if (row === null) return null
  const limits = obj(row.limity)
  return {
    state: str(row.stan, 24) ?? 'BRAK',
    accounts: list(row.konta).length,
    perHour: num(limits?.godzina),
    perDay: num(limits?.doba),
    until: str(row.wygasa, 40),
  }
}

/** The card behind the text of a `room.show` result, or null when the text is not one. */
export function parseRoom(text: string | undefined, now: number): RoomView | null {
  if (text === undefined || text.trim() === '') return null
  let data: Json | null
  try {
    data = obj(JSON.parse(text))
  } catch {
    return null
  }
  if (data === null || data.stan !== 'WYKONANE') return null
  if (!Array.isArray(data.okazje) && !Array.isArray(data.szkice)) return null
  const manual = data.wysylka === 'reczna'
  const base = web(data.pokoj_adres)
  const dashboard = base === null ? null : (/^(https?:\/\/[^/]+)/i.exec(base)?.[1] ?? null)
  return {
    lang: data.jezyk === 'pl' || data.jezyk === 'en' ? (data.jezyk as Lang) : null,
    manual,
    // ONLY the server's word, and never on hosted data: the rehearsal is for the recording sandbox.
    demo: data.demo === true && !manual,
    people: peopleOf(data),
    skipped: Math.max(list(data.pominiete).length, num(data.pominiete_lacznie) ?? 0),
    // HOSTED: no accounts of the person's, no scope. Whatever the server sends, the pane shows none.
    accounts: manual ? [] : accountsOf(data.konta_stan),
    scope: manual ? null : scopeOf(data.zakres),
    links: {
      room: base,
      presence: manual ? null : web(data.obecnosc_adres),
      accounts: manual ? null : (web(data.konta_adres) ?? (dashboard === null ? null : `${dashboard}/community`)),
    },
    readAt: now,
  }
}

/** How many people are waiting for the person, and how old the newest one is, in minutes. */
export function summary(view: RoomView, now: number): { open: number; waiting: number; freshest: number | null } {
  // The conversations that wait for an answer: as on the Codex card, a draft with no conversation of its
  // own (an own post) is a page, not a person to answer.
  const open = view.people.filter(
    (one) => one.key.startsWith('o') && (one.stage === 'new' || one.stage === 'draft'),
  )
  const ages = open
    .map((one) => minutesSince(one.at, now))
    .filter((minutes): minutes is number => minutes !== null)
  return {
    open: open.length,
    waiting: view.people.filter((one) => one.reply !== null && !expired(one.reply, now)).length,
    freshest: ages.length === 0 ? null : Math.min(...ages),
  }
}

export function minutesSince(iso: string | null, now: number): number | null {
  if (iso === null) return null
  const at = Date.parse(iso)
  return Number.isNaN(at) ? null : Math.max(0, Math.floor((now - at) / 60000))
}

export function expired(reply: Reply, now: number): boolean {
  if (reply.expires === null) return false
  const at = Date.parse(reply.expires)
  return !Number.isNaN(at) && at <= now
}

/** A reply on one line for the field: a line break is the arrow, a paragraph break two of them, and back
 * again on save. Typing a single arrow, two, or two with a space between all mean what they look like. */
export function toLine(text: string): string {
  return text
    .replace(/\r\n?/g, '\n')
    .replace(/\n{2,}/g, ' \u21b5\u21b5 ')
    .replace(/\n/g, ' \u21b5 ')
}

export function fromLine(line: string): string {
  return line
    .replace(/\s*\u21b5\u21b5\s*/g, '\n\n')
    .replace(/\s*\u21b5\s*/g, '\n')
    .trim()
}

/** The founder's rule: no long dash (U+2014, U+2013) in anything that goes out. Built from the two
 * code points, so that no file of this repository ever holds the character itself. */
const LONG_DASH = new RegExp(`[${String.fromCharCode(0x2013)}${String.fromCharCode(0x2014)}]`)

export function hasLongDash(text: string): boolean {
  return LONG_DASH.test(text)
}

/** What the person sees before the agent has drawn anything. */
export const FRESH_UI: UiState = {
  page: 0,
  key: null,
  where: 'off',
  editing: null,
  expanded: null,
  why: null,
  note: null,
  draftText: null,
  pending: {},
  asked: 0,
  reveal: 0,
  revealAt: 0,
  frame: 0,
  revealed: false,
  demo: null,
}

/** A new reading arrived: keep the same person on screen when they are still there, and let go of
 * everything that was about the reading before (a field that was open, a request that was waiting). */
export function reconcile(ui: UiState, view: RoomView): UiState {
  const found = ui.key === null ? -1 : view.people.findIndex((one) => one.key === ui.key)
  const page = found >= 0 ? found : Math.min(Math.max(ui.page, 0), Math.max(view.people.length - 1, 0))
  const key = view.people[page]?.key ?? null
  return {
    ...ui,
    page,
    key,
    editing: null,
    draftText: null,
    expanded: key !== null && ui.expanded === key ? key : null,
    why: key !== null && ui.why === key ? key : null,
    note: null,
    pending: {},
    // A rehearsal belongs to the reading it began on: a new reading puts the person back at the card.
    demo: null,
  }
}

/** The person `delta` places away, kept in range. */
export function stepped(ui: UiState, view: RoomView, delta: number): UiState {
  if (view.people.length === 0) return ui
  const page = Math.min(Math.max(ui.page + delta, 0), view.people.length - 1)
  return { ...ui, page, key: view.people[page]?.key ?? null, editing: null, draftText: null, note: null }
}
