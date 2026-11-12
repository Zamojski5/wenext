// The values the Wenext mod keeps in `$.state`: the last card the agent drew (`room.show`) and what the
// person is doing with it. Plain data only (the host holds it for the session), and every value is read from
// the result of `room.show`; nothing here is ever sent anywhere.

export type Lang = 'pl' | 'en'
export type Platform = 'REDDIT' | 'X'

/** The reply (or own post) waiting for the person's click, exactly as the platform will show it. */
export type Reply = {
  rev: string
  kind: 'REPLY' | 'OWN_POST'
  platform: Platform
  /** The handle it goes out from, as the server named it. */
  account: string | null
  community: string | null
  title: string
  text: string
  threadUrl: string | null
  threadAuthor: string | null
  expires: string | null
  /** Hosted (manual) sending only: where the person posts it by hand. */
  intentUrl: string | null
}

/** What came of a reply that went out: the numbers, each one measured or null (never 0). */
export type Outcome = {
  url: string | null
  votes: number | null
  replies: number | null
  at: string | null
  text: string
}

/** One person on the card: a conversation, with the reply waiting for the person's OK when there is one. */
export type Person = {
  key: string
  stage: 'new' | 'draft' | 'posted' | 'gone'
  platform: Platform
  where: string
  author: string | null
  at: string | null
  title: string
  body: string
  url: string | null
  why: string | null
  angle: string | null
  reply: Reply | null
  outcome: Outcome | null
}

/** One assigned account and whether the agent's browser is signed in as it. */
export type AccountHealth = {
  id: string
  platform: Platform
  handle: string
  state:
    | 'polaczone'
    | 'utracona'
    | 'nigdy'
    | 'czeka_na_ciebie'
    | 'inne_konto'
    | 'wstrzymane'
    | 'nieznana'
  canConnect: boolean
}

/** The signed scope in one line: how many accounts, at what pace, until when. */
export type ScopeLine = {
  state: string
  accounts: number
  perHour: number | null
  perDay: number | null
  until: string | null
}

export type RoomView = {
  /** The language the person pinned (WENEXT_LANG), else null: the mod then decides by the environment. */
  lang: Lang | null
  /** Hosted Wenext: nothing is sent by Wenext, the person posts by hand. */
  manual: boolean
  /** The recording sandbox (the server says so): nothing can act for it, so the pane may rehearse a send. */
  demo: boolean
  people: Person[]
  skipped: number
  accounts: AccountHealth[]
  scope: ScopeLine | null
  links: { room: string | null; presence: string | null; accounts: string | null }
  /** When the card was read, in epoch milliseconds. */
  readAt: number
}

export type UiState = {
  /** Which person is on screen, and the key of that person, so a new reading keeps the same one. */
  page: number
  key: string | null
  /** Where the card is drawn: the pane beside the transcript, or the band above the prompt. */
  where: 'pane' | 'band' | 'off'
  /** The revision being edited (the field is open), else null. */
  editing: string | null
  expanded: string | null
  why: string | null
  /** What the person typed in the field when it was refused, so a refusal does not cost them the text. */
  draftText: string | null
  /** One calm line under the buttons. */
  note: string | null
  /** People the agent was asked about (skip, edit) and is working on: their buttons rest. */
  pending: Record<string, 'skip' | 'edit'>
  /** The first drawing of the session: 0 not showing, 1 "Searching", 2 "Read N threads"; when it began and
   * which frame of the spinner is on. It is over after `REVEAL_MS` whatever these say. */
  /** When the person typed `/wenext` with no card yet (ms), 0 when they did not: the card then takes the keys. */
  asked: number
  reveal: 0 | 1 | 2
  revealAt: number
  frame: number
  /** Has this session drawn its first card already: the reveal is once per session. */
  revealed: boolean
  /** A rehearsal of a send in the demo sandbox: whose, and how far it is. Never set outside the sandbox. */
  demo: { key: string; phase: 'confirm' | 'queued' | 'posted' } | null
}

declare module 'claude-code' {
  interface PluginState {
    wenext: {
      room: RoomView | null
      ui: UiState
    }
  }
}
