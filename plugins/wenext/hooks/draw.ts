// What the Wenext pane and band look like. Pure: the trees are built from the element table the engine hands
// out (`$.ui.resolve(e)`), the card's data and the person's place in it; every press is a callback the caller
// supplies. Nothing here reads the clock, the engine or the network.
//
// THE LOOK IS THE CODEX CARD'S, FOR A TERMINAL: one person at a time on a quiet rounded card, the reply drawn
// as the platform will show it, the headline in the card's own words, the studio palette (a soft green, a
// sand yellow) and nothing else. The brand mark is two triangles of text, the platforms are the glyphs they
// have in Unicode (X is U+1D54F; Reddit has none, so it is its orange dot and the `r/` of every community).
// It is drawn with Box, Text, Button and Input only, the four elements the terminal and the Desktop Code tab
// share, so it looks the same in both.
//
// And one thing is different on purpose, which the pane says in its own words: Claude Code cannot hold the
// send click for the person (see `register.ts`), so "Send" opens the Wenext page where the click is. Only
// the demo sandbox, which nothing can act for, rehearses the whole send in the pane, and says so.

import type { Elements, RenderElement } from 'claude-code'

import type { AccountHealth, Lang, Person, RoomView, UiState } from '../types'
import { ago, say, sayN } from './words'
import { expired, minutesSince, summary, toLine } from './room'

/** The four elements the pane uses; the terminal and the desktop tables both have them. */
export type Table = Pick<Elements['terminal'], 'Box' | 'Text' | 'Button' | 'Input'>

/** What a press does. Each one is a top-level function of `register.ts` that takes `$`. */
export type Actions = {
  step: (delta: number) => void
  send: (person: Person) => void
  copy: (person: Person) => void
  edit: (person: Person) => void
  cancelEdit: () => void
  saveEdit: (person: Person, value: string) => void
  skip: (person: Person) => void
  original: (person: Person) => void
  more: (person: Person) => void
  why: (person: Person) => void
  openPost: (person: Person) => void
  reconnect: (account: AccountHealth) => void
  limits: () => void
  demoYes: (person: Person) => void
  demoCancel: () => void
}

export type Ctx = {
  t: Table
  lang: Lang
  view: RoomView
  ui: UiState
  now: number
  columns: number
  /** Rows the pane has for the tree, when it says (a docked pane does). */
  rows: number | null
  actions: Actions
}

/** The studio palette of the card (`apps_ui.py`), as the terminal can show it. */
export const ACCENT = '#d3e4af'
export const WARN = '#d8c08a'
export const REDDIT = '#ff6a3d'
export const BRAND = '◢◣'
export const SPINNER = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

/** How much of the post's excerpt is drawn, before it is cut to the lines of `budget`. */
export const BODY_CLIP = 280
/** The reveal of the first drawing is over after this long, whatever else happened (a reload loses timers). */
export const REVEAL_MS = 2400

const STATE_WORD: Record<AccountHealth['state'], string> = {
  polaczone: 'acc.ok',
  utracona: 'acc.lost',
  nigdy: 'acc.never',
  czeka_na_ciebie: 'acc.awaiting',
  inne_konto: 'acc.other',
  wstrzymane: 'acc.held',
  nieznana: 'acc.unknown',
}

export function handleOf(platform: 'REDDIT' | 'X', raw: string | null): string {
  const clean = (raw ?? '').trim().replace(platform === 'X' ? /^@/ : /^u\//, '')
  if (clean === '') return platform === 'X' ? '@your_account' : 'u/your_account'
  return (platform === 'X' ? '@' : 'u/') + clean
}

/** A prop only when it applies: an element is never handed a prop set to undefined. */
function when<T extends object>(on: boolean, props: T): T | Record<string, never> {
  return on ? props : {}
}

function clip(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max).trimEnd()}…`
}

/** The lines `text` takes at `width` columns, broken on spaces the way the terminal breaks them. */
export function wrapped(text: string, width: number): string[] {
  const out: string[] = []
  for (const paragraph of text.split('\n')) {
    let line = ''
    for (const word of paragraph.split(' ')) {
      if (line === '') line = word
      else if (line.length + 1 + word.length <= width) line = `${line} ${word}`
      else {
        out.push(line)
        line = word
      }
      while (line.length > width) {
        out.push(line.slice(0, width))
        line = line.slice(width)
      }
    }
    out.push(line)
  }
  return out
}

/** At most `max` lines of `text`; when something is cut, the last line ends in an ellipsis. */
export function clipLines(text: string, width: number, max: number): { text: string; cut: boolean } {
  const lines = wrapped(text, width)
  if (lines.length <= max) return { text, cut: false }
  const kept = lines.slice(0, max)
  while (kept.length > 1 && (kept[kept.length - 1] ?? '').trim() === '') kept.pop()
  const at = kept.length - 1
  kept[at] = `${(kept[at] ?? '').replace(/[\s.,;:!?-]+$/, '')}…`
  return { text: kept.join('\n'), cut: true }
}

/**
 * What fits in the pane: the lines of the post's excerpt and of the reply. The rest of the card (the brand,
 * the headline, the pager, the frame, the buttons, the footer with the accounts and the promise) takes about
 * 34 rows, measured in a real Claude Code, and the Send button has to stay on screen.
 */
export function budget(ctx: Ctx): { width: number; body: number; reply: number } {
  const rows = ctx.rows ?? 44
  const body = rows >= 56 ? 4 : rows >= 40 ? 3 : 2
  return { width: Math.max(24, ctx.columns - 13), body, reply: Math.max(5, rows - 34 - body) }
}

export function clockOf(now: number): string {
  const at = new Date(now)
  return `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`
}

/** The person on screen: the one at `ui.page`, kept in range. */
export function current(view: RoomView, ui: UiState): { person: Person | null; index: number } {
  if (view.people.length === 0) return { person: null, index: 0 }
  const index = Math.min(Math.max(ui.page, 0), view.people.length - 1)
  return { person: view.people[index] ?? null, index }
}

/** Is the first-drawing reveal on screen now? Stage and age both say so: a stale stage is never drawn. */
export function revealing(ui: UiState, now: number): boolean {
  return ui.reveal > 0 && now - ui.revealAt < REVEAL_MS
}

// -- the brand, the platforms ------------------------------------------------------------------------

function platformMark(ctx: Ctx, platform: 'REDDIT' | 'X'): RenderElement {
  const { t } = ctx
  return platform === 'X'
    ? t.Text({ bold: true, children: ['\u{1d54f}'] })
    : t.Text({ bold: true, color: REDDIT, children: ['●'] })
}

function brandRow(ctx: Ctx): RenderElement {
  const { t, lang, view, now } = ctx
  return t.Box({
    key: 'brand',
    flexDirection: 'row',
    justifyContent: 'space-between',
    children: [
      t.Box({
        key: 'mark',
        flexDirection: 'row',
        columnGap: 1,
        children: [
          t.Text({ color: ACCENT, bold: true, children: [BRAND] }),
          t.Text({ bold: true, children: ['Wenext'] }),
          ...(view.demo ? [t.Text({ color: WARN, children: [`· ${say(lang, 'demo.badge')}`] })] : []),
        ],
      }),
      t.Text({ dimColor: true, children: [say(lang, 'as.of', { time: clockOf(view.readAt || now) })] }),
    ],
  })
}

// -- the headline, the summary ------------------------------------------------------------------------

function headline(ctx: Ctx): RenderElement {
  const { t, view, lang, now } = ctx
  const s = summary(view, now)
  if (view.people.length === 0) return t.Text({ bold: true, wrap: 'wrap', children: [say(lang, 'head.none')] })
  if (s.open === 0) return t.Text({ bold: true, wrap: 'wrap', children: [say(lang, 'head.done')] })
  const fresh = s.freshest !== null && s.freshest <= 48 * 60
  return t.Text({
    wrap: 'wrap',
    children: [
      t.Text({ bold: true, children: [sayN(lang, 'head.nowStrong', s.open)] }),
      t.Text({ dimColor: true, children: [sayN(lang, fresh ? 'head.nowRest' : 'head.recentRest', s.open)] }),
    ],
  })
}

function summaryLine(ctx: Ctx): string {
  const { view, lang, now } = ctx
  const s = summary(view, now)
  const bits: string[] = []
  const age = ago(lang, s.freshest)
  if (age !== null) bits.push(say(lang, 'sum.newest', { ago: age }))
  if (s.waiting > 0 && !view.manual) bits.push(sayN(lang, 'sum.waiting', s.waiting))
  if (view.skipped > 0) bits.push(say(lang, 'sum.skipped', { n: view.skipped }))
  return bits.join(' · ')
}

function figure(lang: Lang, value: number | null): string {
  return value === null ? say(lang, 'fig.none') : String(value)
}

// -- the first drawing: what Wenext found, in about two seconds ---------------------------------------------

/** Where the threads were read, as the card says it: three places and a count of the rest. */
export function placesOf(view: RoomView): string {
  const places: string[] = []
  for (const person of view.people) if (!places.includes(person.where)) places.push(person.where)
  const shown = places.slice(0, 3).join(', ')
  return places.length > 3 ? `${shown} +${places.length - 3}` : shown
}

function revealBlock(ctx: Ctx): RenderElement {
  const { t, lang, view, ui } = ctx
  const total = view.people.filter((one) => one.key.startsWith('o')).length + view.skipped
  const read = ui.reveal >= 2
  const frame = SPINNER[ui.frame % SPINNER.length] ?? '*'
  const faces = view.people.slice(0, 4).map((person, i) => t.Box({ key: `face-${i}`, children: [platformMark(ctx, person.platform)] }))
  const tiles = view.people
    .filter((one) => one.key.startsWith('o'))
    .slice(0, 3)
    .map((person, i) =>
      t.Box({
        key: `tile-${i}`,
        flexDirection: 'row',
        columnGap: 1,
        children: [
          platformMark(ctx, person.platform),
          t.Text({ dimColor: true, wrap: 'truncate-end', children: [`${person.platform === 'X' && person.where === 'X' ? '' : `${person.where}  `}${clip(person.title, 64)}`] }),
        ],
      }),
    )
  return t.Box({
    key: 'reveal',
    flexDirection: 'column',
    marginTop: 1,
    children: [
      read
        ? t.Box({
            key: 'read',
            flexDirection: 'row',
            columnGap: 1,
            children: [
              t.Text({ color: ACCENT, children: ['✓'] }),
              t.Text({ children: [sayN(lang, 'rv.read', Math.max(total, 1), { where: placesOf(view) })] }),
              ...faces,
            ],
          })
        : t.Box({
            key: 'searching',
            flexDirection: 'row',
            columnGap: 1,
            children: [t.Text({ color: ACCENT, children: [frame] }), t.Text({ dimColor: true, children: [say(lang, 'rv.searching')] })],
          }),
      t.Box({ key: 'tiles', flexDirection: 'column', marginTop: 1, children: read ? tiles : [] }),
    ],
  })
}

// -- one person -----------------------------------------------------------------------------------------

function metaRow(ctx: Ctx, person: Person): RenderElement {
  const { t, lang, now } = ctx
  const age = ago(lang, minutesSince(person.at, now))
  // The glyph already says X: its "community" is the platform again, so it is not repeated.
  const place = person.platform === 'X' && person.where === 'X' ? null : person.where
  const bits = [place, age, person.author === null ? null : handleOf(person.platform, person.author)]
  const kind = say(lang, `kind.${person.stage}`)
  return t.Box({
    key: 'meta',
    flexDirection: 'row',
    justifyContent: 'space-between',
    columnGap: 2,
    children: [
      t.Box({
        key: 'where',
        flexDirection: 'row',
        columnGap: 1,
        children: [
          platformMark(ctx, person.platform),
          t.Text({ dimColor: true, wrap: 'truncate-end', children: [bits.filter((b) => b !== null).join(' · ')] }),
        ],
      }),
      t.Text({ ...when(person.stage === 'draft', { color: ACCENT }), dimColor: person.stage !== 'draft', children: [kind] }),
    ],
  })
}

function postBlock(ctx: Ctx, person: Person): RenderElement[] {
  const { t, ui, lang } = ctx
  const out: RenderElement[] = []
  if (person.title !== '') out.push(t.Text({ bold: true, wrap: 'wrap', children: [clip(person.title, 200)] }))
  if (person.body !== '') {
    const room = budget(ctx)
    out.push(t.Text({ dimColor: true, wrap: 'wrap', children: [clipLines(clip(person.body, BODY_CLIP), room.width, room.body).text] }))
  }
  if (ui.why === person.key && (person.why !== null || person.angle !== null)) {
    out.push(
      t.Box({
        key: 'why',
        flexDirection: 'column',
        marginTop: 1,
        paddingLeft: 1,
        borderStyle: 'quote',
        borderDimColor: true,
        children: [
          person.why === null ? null : t.Text({ wrap: 'wrap', children: [`${say(lang, 'why.person')}: ${person.why}`] }),
          person.angle === null ? null : t.Text({ wrap: 'wrap', children: [`${say(lang, 'why.angle')}: ${person.angle}`] }),
        ].filter((one): one is RenderElement => one !== null),
      }),
    )
  }
  return out
}

/** The reply as the platform will show it, clipped to a few lines with a way to read the rest. */
function replyBlock(ctx: Ctx, person: Person): RenderElement {
  const { t, lang, ui, now } = ctx
  const reply = person.reply
  if (reply === null) return t.Text({ children: [''] })
  const own = reply.kind === 'OWN_POST'
  const handle = handleOf(reply.platform, reply.account)
  const open = ui.expanded === person.key
  const room = budget(ctx)
  const fit = clipLines(reply.text, room.width, room.reply)
  const long = fit.cut
  const shown = open || !long ? reply.text : fit.text
  const target =
    !own && reply.platform === 'X' && reply.threadAuthor !== null
      ? t.Text({ dimColor: true, children: [`${say(lang, 'reply.to')} ${handleOf('X', reply.threadAuthor)}`] })
      : null
  const place = own && reply.platform === 'REDDIT' && reply.community !== null ? t.Text({ bold: true, children: [`r/${reply.community.replace(/^r\//, '')}`] }) : null
  const head = own && reply.platform === 'REDDIT' && reply.title !== '' ? t.Text({ bold: true, wrap: 'wrap', children: [reply.title] }) : null
  return t.Box({
    key: 'reply',
    flexDirection: 'column',
    children: [
      t.Text({ dimColor: true, children: [say(lang, own ? 'reply.labelPost' : 'reply.labelReply').toUpperCase()] }),
      t.Box({
        key: 'frame',
        flexDirection: 'column',
        paddingLeft: 1,
        borderStyle: 'quote',
        borderColor: reply.platform === 'X' ? 'white' : REDDIT,
        children: [
          t.Text({ children: [t.Text({ bold: true, children: [handle] }), t.Text({ dimColor: true, children: [` · ${say(lang, 'reply.now')}`] })] }),
          target,
          place,
          head,
          t.Text({ wrap: 'wrap', children: [shown] }),
        ].filter((one): one is RenderElement => one !== null),
      }),
      long
        ? t.Box({
            key: 'more-row',
            flexDirection: 'row',
            columnGap: 2,
            children: [
              t.Button({ key: 'more', label: say(lang, open ? 'reply.less' : 'reply.more'), hotkey: 'm', plain: true, dimColor: true, onPress: () => ctx.actions.more(person) }),
              open ? null : t.Text({ dimColor: true, children: [sayN(lang, 'reply.cut', Math.max(reply.text.length - shown.length, 1))] }),
            ].filter((one): one is RenderElement => one !== null),
          })
        : null,
      expired(reply, now) ? t.Text({ color: WARN, children: [say(lang, 'exp.closed')] }) : null,
    ].filter((one): one is RenderElement => one !== null),
  })
}

/** The one-line field that replaces the reply while it is edited. Typing redraws nothing. */
function editor(ctx: Ctx, person: Person): RenderElement {
  const { t, lang } = ctx
  const reply = person.reply
  const value = ctx.ui.draftText ?? (reply === null ? '' : toLine(reply.text))
  return t.Box({
    key: 'editor',
    flexDirection: 'column',
    marginTop: 1,
    children: [
      t.Input({
        key: 'edit',
        label: say(lang, 'edit.label'),
        placeholder: say(lang, 'edit.placeholder'),
        value,
        submitLabel: say(lang, 'edit.submit'),
        autoFocus: true,
        onSubmit: (text: string) => ctx.actions.saveEdit(person, text),
      }),
      t.Text({ dimColor: true, wrap: 'wrap', children: [say(lang, 'edit.hint')] }),
      t.Box({ key: 'edit-buttons', children: [t.Button({ key: 'edit-cancel', label: say(lang, 'b.cancel'), hotkey: 'x', onPress: () => ctx.actions.cancelEdit() })] }),
    ],
  })
}

function outcomeBlock(ctx: Ctx, person: Person): RenderElement | null {
  const { t, lang } = ctx
  const outcome = person.outcome
  if (outcome === null) return null
  return t.Box({
    key: 'outcome',
    flexDirection: 'column',
    marginTop: 1,
    children: [
      t.Text({
        children: [
          t.Text({ dimColor: true, children: [`${say(lang, 'res.votes')} `] }),
          t.Text({ bold: outcome.votes !== null, dimColor: outcome.votes === null, children: [figure(lang, outcome.votes)] }),
          t.Text({ dimColor: true, children: [`   ${say(lang, 'res.replies')} `] }),
          t.Text({ bold: outcome.replies !== null, dimColor: outcome.replies === null, children: [figure(lang, outcome.replies)] }),
        ],
      }),
      outcome.url === null ? null : t.Box({ key: 'open-post-row', children: [t.Button({ key: 'open-post', label: say(lang, 'b.openPost'), hotkey: 'o', plain: true, onPress: () => ctx.actions.openPost(person) })] }),
    ].filter((one): one is RenderElement => one !== null),
  })
}

/** The rehearsal of a send, on the demo sandbox only: the consent, the queue, "Posted", each one said to be a demo. */
function rehearsal(ctx: Ctx, person: Person): RenderElement | null {
  const { t, lang, ui } = ctx
  const state = ui.demo
  const reply = person.reply
  if (state === null || state.key !== person.key || reply === null) return null
  const who = handleOf(reply.platform, reply.account)
  if (state.phase === 'confirm') {
    return t.Box({
      key: 'demo-confirm',
      flexDirection: 'column',
      marginTop: 1,
      children: [
        t.Text({ wrap: 'wrap', children: [say(lang, 'demo.confirm', { who })] }),
        t.Box({
          key: 'demo-buttons',
          flexDirection: 'row',
          columnGap: 2,
          marginTop: 1,
          children: [
            t.Button({ key: 'demo-yes', label: say(lang, 'demo.yes'), hotkey: 'y', variant: 'primary', autoFocus: true, onPress: () => ctx.actions.demoYes(person) }),
            t.Button({ key: 'demo-cancel', label: say(lang, 'demo.cancel'), hotkey: 'x', onPress: () => ctx.actions.demoCancel() }),
          ],
        }),
      ],
    })
  }
  const posted = state.phase === 'posted'
  return t.Box({
    key: 'demo-state',
    flexDirection: 'column',
    marginTop: 1,
    children: [
      posted
        ? t.Text({ children: [t.Text({ color: ACCENT, bold: true, children: ['✓ '] }), t.Text({ bold: true, children: [say(lang, 'demo.posted')] })] })
        : t.Text({ children: [t.Text({ color: ACCENT, children: [`${SPINNER[ui.frame % SPINNER.length] ?? '*'} `] }), t.Text({ bold: true, children: [say(lang, 'demo.queued')] })] }),
      t.Text({ dimColor: true, wrap: 'wrap', children: [say(lang, posted ? 'demo.postedNote' : 'demo.queuedNote')] }),
    ],
  })
}

function buttons(ctx: Ctx, person: Person): RenderElement | null {
  const { t, lang, view, ui } = ctx
  const reply = person.reply
  const pending = ui.pending[person.key] !== undefined
  const rehearsing = ui.demo !== null && ui.demo.key === person.key
  if (rehearsing) return null
  const row: RenderElement[] = []
  if (reply !== null && !pending) {
    if (view.manual) {
      const x = reply.platform === 'X'
      row.push(t.Button({ key: 'send', label: say(lang, x ? 'b.postX' : 'b.copy'), hotkey: 's', variant: 'primary', onPress: () => ctx.actions.send(person) }))
    } else {
      row.push(t.Button({ key: 'send', label: say(lang, view.demo ? 'b.sendDemo' : 'b.send'), hotkey: 's', variant: 'primary', onPress: () => ctx.actions.send(person) }))
      row.push(t.Button({ key: 'edit', label: say(lang, 'b.edit'), hotkey: 'e', plain: true, onPress: () => ctx.actions.edit(person) }))
    }
    row.push(t.Button({ key: 'copy', label: say(lang, 'b.copyReply'), hotkey: 'c', plain: true, dimColor: true, onPress: () => ctx.actions.copy(person) }))
  }
  if ((person.stage === 'new' || person.stage === 'draft') && !pending) {
    row.push(t.Button({ key: 'skip', label: say(lang, 'b.skip'), hotkey: 'k', plain: true, onPress: () => ctx.actions.skip(person) }))
  }
  if (person.url !== null) {
    row.push(t.Button({ key: 'original', label: say(lang, 'b.original'), hotkey: 'o', plain: true, dimColor: true, onPress: () => ctx.actions.original(person) }))
  }
  if (person.why !== null || person.angle !== null) {
    row.push(t.Button({ key: 'why', label: say(lang, 'why.toggle'), hotkey: 'w', plain: true, dimColor: true, onPress: () => ctx.actions.why(person) }))
  }
  if (row.length === 0) return null
  return t.Box({ key: 'buttons', flexDirection: 'row', flexWrap: 'wrap', columnGap: 2, marginTop: 1, children: row })
}

function hintLine(ctx: Ctx, person: Person): RenderElement | null {
  const { t, lang, view, now, ui } = ctx
  const reply = person.reply
  if (reply === null || (ui.demo !== null && ui.demo.key === person.key)) return null
  if (view.manual) return t.Text({ dimColor: true, wrap: 'wrap', children: [say(lang, 'man.hint')] })
  if (view.demo) return t.Text({ dimColor: true, wrap: 'wrap', children: [say(lang, 'demo.banner')] })
  const who = reply.account === null ? null : handleOf(reply.platform, reply.account)
  const bits = [who === null ? say(lang, 'hint.goesProject') : say(lang, 'hint.goes', { who })]
  if (reply.expires !== null && !expired(reply, now)) {
    const minutes = Math.ceil((Date.parse(reply.expires) - now) / 60000)
    if (minutes < 60) bits.push(say(lang, 'exp.min', { m: Math.max(1, minutes) }))
    else if (minutes < 36 * 60) bits.push(say(lang, 'exp.hour', { h: Math.floor(minutes / 60) }))
  }
  return t.Text({ dimColor: true, wrap: 'wrap', children: [bits.join(' ')] })
}

function pager(ctx: Ctx, index: number): RenderElement | null {
  const { t, lang, view } = ctx
  if (view.people.length < 2) return null
  return t.Box({
    key: 'pager',
    flexDirection: 'row',
    columnGap: 2,
    children: [
      t.Button({ key: 'prev', label: `‹ ${say(lang, 'nav.prev')}`, hotkey: 'p', plain: true, onPress: () => ctx.actions.step(-1) }),
      t.Text({ dimColor: true, children: [say(lang, 'nav.of', { i: index + 1, n: view.people.length })] }),
      t.Button({ key: 'next', label: `${say(lang, 'nav.next')} ›`, hotkey: 'n', plain: true, onPress: () => ctx.actions.step(1) }),
    ],
  })
}

// -- the footer: the health of the accounts, and the scope -----------------------------------------------

function dotColor(state: AccountHealth['state']): string | null {
  if (state === 'polaczone') return ACCENT
  if (state === 'utracona' || state === 'inne_konto' || state === 'czeka_na_ciebie') return WARN
  return null
}

function accountRow(ctx: Ctx): RenderElement | null {
  const { t, lang, view } = ctx
  if (view.manual || view.accounts.length === 0) return null
  const items = view.accounts.map((account, i) => {
    const who = handleOf(account.platform, account.handle)
    const word = say(lang, STATE_WORD[account.state])
    const color = dotColor(account.state)
    const dot = t.Text({ ...when(color !== null, { color: color ?? ACCENT }), dimColor: color === null, children: ['●'] })
    if (account.canConnect) {
      return t.Box({
        key: `acc-${account.id}`,
        flexDirection: 'row',
        columnGap: 1,
        children: [
          dot,
          t.Button({
            key: `reconnect-${account.id}`,
            label: say(lang, account.state === 'nigdy' ? 'b.connect' : 'b.reconnect', { who }),
            ...when(i === 0, { hotkey: 'r' }),
            plain: true,
            onPress: () => ctx.actions.reconnect(account),
          }),
        ],
      })
    }
    return t.Box({
      key: `acc-${account.id}`,
      flexDirection: 'row',
      columnGap: 1,
      children: [dot, t.Text({ dimColor: account.state !== 'polaczone', children: [account.state === 'polaczone' ? who : `${who} · ${word}`] })],
    })
  })
  return t.Box({ key: 'accounts', flexDirection: 'row', flexWrap: 'wrap', columnGap: 3, children: items })
}

function dayOf(iso: string, lang: Lang): string | null {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return null
  try {
    return at.toLocaleDateString(lang === 'pl' ? 'pl-PL' : 'en-US', { day: 'numeric', month: 'short' })
  } catch {
    return iso.slice(0, 10)
  }
}

function scopeLine(ctx: Ctx): string | null {
  const { view, lang } = ctx
  const scope = view.scope
  if (view.manual || scope === null) return null
  if (scope.state === 'WSTRZYMANY') return say(lang, 'sc.paused')
  if (scope.state === 'WYGASL') return say(lang, 'sc.expired')
  if (scope.state !== 'AKTYWNY') return say(lang, 'sc.none')
  const bits = [sayN(lang, 'sc.accounts', scope.accounts)]
  if (scope.perHour !== null) bits.push(say(lang, 'sc.hour', { h: scope.perHour }))
  if (scope.perDay !== null) bits.push(say(lang, 'sc.day', { d: scope.perDay }))
  if (scope.until !== null) {
    const day = dayOf(scope.until, lang)
    if (day !== null) bits.push(say(lang, 'sc.until', { d: day }))
  }
  return bits.join(' · ')
}

function limitsRow(ctx: Ctx): RenderElement | null {
  const { t, lang, view } = ctx
  const line = scopeLine(ctx)
  if (view.manual || line === null) return null
  return t.Box({
    key: 'scope',
    flexDirection: 'row',
    columnGap: 2,
    children: [
      t.Text({ dimColor: true, children: [line] }),
      t.Button({ key: 'limits', label: say(lang, 'b.limits'), hotkey: 'l', plain: true, dimColor: true, onPress: () => ctx.actions.limits() }),
    ],
  })
}

/** The share line, only when it does not get in the way: room for it, nothing being edited, no message. */
function shareLine(ctx: Ctx): RenderElement | null {
  const { t, lang, ui, columns, rows } = ctx
  if (columns < 64 || ui.editing !== null || ui.note !== null || ui.demo !== null) return null
  if (rows !== null && rows < 26) return null
  return t.Text({ dimColor: true, children: [say(lang, 'foot.share')] })
}

// -- the pane ---------------------------------------------------------------------------------------------

export function drawPane(ctx: Ctx): RenderElement {
  const { t, view, ui, lang, now } = ctx
  const { person, index } = current(view, ui)
  const editing = person !== null && person.reply !== null && ui.editing === person.reply.rev
  const body: RenderElement[] = [brandRow(ctx)]

  if (revealing(ui, now) && view.people.length > 0) {
    body.push(revealBlock(ctx))
    return t.Box({ key: 'wenext', flexDirection: 'column', paddingX: 1, children: body })
  }

  body.push(t.Box({ key: 'head-row', marginTop: 1, children: [headline(ctx)] }))
  const line = summaryLine(ctx)
  if (line !== '') body.push(t.Text({ dimColor: true, wrap: 'wrap', children: [line] }))
  const pageRow = pager(ctx, index)
  if (pageRow !== null) body.push(t.Box({ key: 'pager-gap', marginTop: 1, children: [pageRow] }))

  if (person === null) {
    body.push(t.Box({ key: 'empty', marginTop: 1, children: [t.Text({ wrap: 'wrap', dimColor: true, children: [say(lang, 'empty.body')] })] }))
  } else {
    body.push(
      t.Box({
        key: `person-${person.key}`,
        flexDirection: 'column',
        marginTop: 1,
        borderStyle: 'round',
        borderDimColor: true,
        paddingX: 1,
        children: [
          metaRow(ctx, person),
          t.Box({ key: 'post', flexDirection: 'column', marginTop: 1, children: postBlock(ctx, person) }),
          person.reply === null ? null : t.Box({ key: 'reply-gap', marginTop: 1, children: [editing ? editor(ctx, person) : replyBlock(ctx, person)] }),
          outcomeBlock(ctx, person),
          editing ? null : rehearsal(ctx, person),
          editing ? null : buttons(ctx, person),
          editing ? null : hintLine(ctx, person),
        ].filter((one): one is RenderElement => one !== null),
      }),
    )
  }
  if (ui.note !== null) body.push(t.Box({ key: 'note-gap', marginTop: 1, children: [t.Text({ italic: true, wrap: 'wrap', children: [ui.note] })] }))

  const footer = [accountRow(ctx), limitsRow(ctx), shareLine(ctx)].filter((one): one is RenderElement => one !== null)
  if (footer.length > 0) body.push(t.Box({ key: 'footer', flexDirection: 'column', marginTop: 1, children: footer }))
  return t.Box({ key: 'wenext', flexDirection: 'column', paddingX: 1, children: body })
}

// -- the band: only a nudge, when the pane cannot be seated -----------------------------------------------

export function drawBand(ctx: Ctx, open: () => void, dismiss: () => void): RenderElement | null {
  const { t, lang, view, now } = ctx
  const lost = view.accounts.find((a) => a.canConnect)
  const s = summary(view, now)
  if (s.open === 0 && lost === undefined) return null
  const text =
    s.open > 0
      ? `${sayN(lang, 'head.nowStrong', s.open)}${sayN(lang, 'head.nowRest', s.open)}`
      : say(lang, 'band.attention', { who: handleOf(lost?.platform ?? 'X', lost?.handle ?? null) })
  return t.Box({
    key: 'wenext-band',
    flexDirection: 'row',
    columnGap: 2,
    paddingX: 1,
    children: [
      t.Text({ color: ACCENT, bold: true, children: [BRAND] }),
      t.Text({ bold: true, wrap: 'truncate-end', children: [text] }),
      t.Button({ key: 'band-open', label: say(lang, 'band.open'), onPress: open }),
      t.Button({ key: 'band-dismiss', label: say(lang, 'band.dismiss'), dimColor: true, onPress: dismiss }),
    ],
  })
}
