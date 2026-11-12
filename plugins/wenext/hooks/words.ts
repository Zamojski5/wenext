// Every word the Wenext pane says, in the two languages of the Codex card, and the two messages a press on
// "Edit" and "Skip" asks the agent in the person's own name.
//
// A value is a string or, for a count, a list of forms: three for Polish (one, two to four, the rest), two
// for English (one, the rest). `{name}` is filled by `say`. No long dash anywhere (the founder's rule; a test
// holds this file to it), and the two dictionaries have exactly the same keys and the same placeholders.

import type { Lang } from '../types'

export type Words = Record<string, string | string[]>

export const WORDS: Record<Lang, Words> = {
  en: {
    'head.nowStrong': ['{n} person', '{n} people'],
    'head.nowRest': [' is writing right now about the problem your code solves.', ' are writing right now about the problem your code solves.'],
    'head.recentRest': [' wrote recently about the problem your code solves.', ' wrote recently about the problem your code solves.'],
    'head.done': 'Every conversation is handled.',
    'head.none': 'Nobody yet.',
    'rv.searching': 'Searching threads',
    'rv.read': ['Read {n} thread on {where}', 'Read {n} threads on {where}'],
    'rv.more': '+{n}',
    'rv.found': 'Found',
    'foot.share': 'Nothing posts without your click \u00b7 getwenext.com',
    'demo.badge': 'demo sandbox',
    'demo.banner': 'Demo data: nothing here can reach a platform.',
    'b.sendDemo': 'Send',
    'demo.confirm': 'Send from {who}? It goes out exactly as you see it.',
    'demo.yes': 'Yes, send',
    'demo.cancel': 'Cancel',
    'demo.queued': 'Sent to the publisher (demo).',
    'demo.queuedNote': 'A rehearsal: the sandbox has no publisher, nothing leaves this machine.',
    'demo.posted': 'Posted (demo)',
    'demo.postedNote': 'A rehearsal: nothing was posted. In your own project, Send opens Wenext and the post goes out after your click there.',
    'cmd.ask': 'Show me my Wenext room: call room.show.',
    'cmd.asking': 'Asking the agent to show your room.',
    'sum.newest': 'Newest {ago}',
    'sum.waiting': ['{n} reply waiting for your OK', '{n} replies waiting for your OK'],
    'sum.skipped': '{n} skipped',
    'as.of': 'As of {time}',
    'ago.now': 'just now',
    'ago.min': '{m} min ago',
    'ago.hour': '{h} h ago',
    'ago.yesterday': 'yesterday',
    'ago.days': '{d} days ago',
    'nav.of': '{i} of {n}',
    'nav.prev': 'Prev',
    'nav.next': 'Next',
    'kind.new': 'New',
    'kind.draft': 'Reply ready',
    'kind.posted': 'Posted',
    'kind.gone': 'Draft gone',
    'why.toggle': 'Why?',
    'why.person': 'Why this person',
    'why.angle': 'Suggested angle',
    'reply.labelReply': 'Your reply, as it will appear',
    'reply.labelPost': 'Your post, as it will appear',
    'reply.now': 'now',
    'reply.to': 'Replying to',
    'reply.more': 'Show all',
    'reply.less': 'Show less',
    'reply.cut': '(+{n} characters)',
    'b.send': 'Send in Wenext ↗',
    'b.edit': 'Edit',
    'b.skip': 'Skip',
    'b.copy': 'Copy and open thread ↗',
    'b.postX': 'Post on X ↗',
    'b.original': 'Original ↗',
    'b.openPost': 'Open the post ↗',
    'b.copyReply': 'Copy',
    'b.save': 'Save',
    'b.cancel': 'Cancel',
    'b.reconnect': 'Reconnect {who}',
    'b.connect': 'Connect {who}',
    'b.limits': 'Limits',
    'hint.goes': 'Goes out from {who} after your click on the Wenext page.',
    'hint.goesProject': 'Goes out from the project account after your click on the Wenext page.',
    'exp.min': 'Expires in {m} min.',
    'exp.hour': 'Expires in {h} h.',
    'exp.closed': 'The approval window has closed.',
    'edit.label': 'Edit',
    'edit.placeholder': 'Type the new text and press Enter',
    'edit.submit': 'ask the agent to save',
    'edit.hint': 'One line: ↵ marks a new line. Nothing is published; the agent saves a new draft.',
    'edit.same': 'Nothing changed in the text.',
    'edit.empty': 'The text is empty.',
    'edit.dash': 'Use a period, comma or colon instead of the long dash.',
    'note.asked': 'Asked the agent to save your text as a new draft. The card will refresh.',
    'note.skip': 'Asked the agent to skip this conversation. The card will refresh.',
    'note.copied': 'Copied. Paste it in the thread.',
    'note.copyFailed': 'Could not reach the clipboard.',
    'note.opened': 'Opened in your browser.',
    'note.noLink': 'There is no link for this. Open Wenext in your browser.',
    'note.openFailed': 'Could not open the browser. The link is copied.',
    'note.send': 'Opened the Wenext page: approving there is the click that sends it.',
    'note.reconnect':
      "Opened the Wenext page. Sign in to {platform} in the agent's browser there. In Codex this is one click on the card.",
    'note.limits': 'Opened the Presence page, where the limits and Pause live.',
    'note.working': 'Waiting for the agent. The card refreshes when it shows the room again.',
    'acc.ok': 'connected',
    'acc.lost': 'session lost',
    'acc.never': 'not connected yet',
    'acc.other': 'signed in as someone else',
    'acc.held': 'paused',
    'acc.unknown': "can't check right now",
    'acc.awaiting': 'waiting for your sign-in',
    'sc.accounts': ['{n} account', '{n} accounts'],
    'sc.hour': '{h}/h',
    'sc.day': '{d}/day',
    'sc.until': 'until {d}',
    'sc.paused': 'Paused',
    'sc.expired': 'Scope expired',
    'sc.none': 'No scope yet',
    'res.votes': 'Votes',
    'res.replies': 'Replies',
    'fig.none': 'not measured',
    'man.hint': 'Wenext sends nothing in this mode: you post it yourself.',
    'empty.body': 'Ask the agent to find conversations. They will show up here.',
    'pane.none': 'No Wenext card yet. Ask the agent to show the room.',
    'band.text': ['Wenext: {n} person to answer', 'Wenext: {n} people to answer'],
    'band.attention': 'Wenext: {who} needs your sign-in',
    'band.open': 'Open',
    'band.dismiss': 'Dismiss',
    'keys.line': 'Keys: s send · e edit · k skip · c copy · p/n people',
    'cmd.description': 'Show who is writing about your problem, with a reply ready',
    'cmd.none': 'No Wenext card yet. Ask the agent to find conversations and show the room.',
    'cmd.opened': 'The Wenext card is open.',
    'toast.ready': 'Wenext: the card is ready. Run /wenext to open it.',
    'ask.editReply':
      'I edited the reply waiting for {who}{thread}. Save my text word for word as the new draft for the same thread with draft.create, change nothing else and publish nothing, then call room.show again.',
    'ask.editPost':
      'I edited my post draft{where}. Save my text word for word as the new draft with draft.create, same account and place, change nothing else and publish nothing, then call room.show again.',
    'ask.inThread': ' in {url}',
    'ask.inCommunity': ' for {c}',
    'ask.skip':
      'Skip this conversation: {title} ({url}). Save it with opportunities.mark, pomin=true and powod "Skipped by you from the Wenext pane", then call room.show again.',
    'ask.who': 'this person',
  },
  pl: {
    'head.nowStrong': ['{n} osoba', '{n} osoby', '{n} osób'],
    'head.nowRest': [
      ' właśnie pisze o problemie, który rozwiązuje Twój kod.',
      ' właśnie piszą o problemie, który rozwiązuje Twój kod.',
      ' właśnie pisze o problemie, który rozwiązuje Twój kod.',
    ],
    'head.recentRest': [
      ' ostatnio pisze o problemie, który rozwiązuje Twój kod.',
      ' ostatnio piszą o problemie, który rozwiązuje Twój kod.',
      ' ostatnio pisze o problemie, który rozwiązuje Twój kod.',
    ],
    'head.done': 'Wszystkie rozmowy obsłużone.',
    'head.none': 'Na razie nikogo.',
    'rv.searching': 'Szukam wątków',
    'rv.read': ['Przeczytano {n} wątek: {where}', 'Przeczytano {n} wątki: {where}', 'Przeczytano {n} wątków: {where}'],
    'rv.more': '+{n}',
    'rv.found': 'Znaleziono',
    'foot.share': 'Nic nie wyjdzie bez Twojego kliknięcia \u00b7 getwenext.com',
    'demo.badge': 'piaskownica demo',
    'demo.banner': 'Dane demo: nic tu nie dotrze do żadnej platformy.',
    'b.sendDemo': 'Wyślij',
    'demo.confirm': 'Wysłać z {who}? Wyjdzie dokładnie tak, jak je widzisz.',
    'demo.yes': 'Tak, wyślij',
    'demo.cancel': 'Anuluj',
    'demo.queued': 'Wysłane do publikatora (demo).',
    'demo.queuedNote': 'Próba: piaskownica nie ma publikatora, nic nie opuszcza tego komputera.',
    'demo.posted': 'Opublikowano (demo)',
    'demo.postedNote': 'Próba: nic nie zostało opublikowane. W Twoim projekcie Wyślij otwiera Wenext, a post wychodzi po Twoim kliknięciu tam.',
    'cmd.ask': 'Pokaż mi mój Pokój Wenext: zawołaj room.show.',
    'cmd.asking': 'Proszę agenta o pokazanie Twojego Pokoju.',
    'sum.newest': 'Najnowsza {ago}',
    'sum.waiting': [
      '{n} odpowiedź czeka na Twoją zgodę',
      '{n} odpowiedzi czekają na Twoją zgodę',
      '{n} odpowiedzi czeka na Twoją zgodę',
    ],
    'sum.skipped': '{n} pominiętych',
    'as.of': 'Odczyt o {time}',
    'ago.now': 'przed chwilą',
    'ago.min': '{m} min temu',
    'ago.hour': '{h} h temu',
    'ago.yesterday': 'wczoraj',
    'ago.days': '{d} dni temu',
    'nav.of': '{i} z {n}',
    'nav.prev': 'Wstecz',
    'nav.next': 'Dalej',
    'kind.new': 'Nowa',
    'kind.draft': 'Odpowiedź gotowa',
    'kind.posted': 'Opublikowano',
    'kind.gone': 'Szkic zniknął',
    'why.toggle': 'Dlaczego?',
    'why.person': 'Dlaczego ta osoba',
    'why.angle': 'Proponowany kąt',
    'reply.labelReply': 'Twoja odpowiedź, tak jak wyjdzie',
    'reply.labelPost': 'Twój post, tak jak wyjdzie',
    'reply.now': 'teraz',
    'reply.to': 'W odpowiedzi do',
    'reply.more': 'Pokaż całość',
    'reply.less': 'Zwiń',
    'reply.cut': '(+{n} znaków)',
    'b.send': 'Wyślij w Wenext ↗',
    'b.edit': 'Popraw',
    'b.skip': 'Pomiń',
    'b.copy': 'Skopiuj i otwórz wątek ↗',
    'b.postX': 'Opublikuj na X ↗',
    'b.original': 'Oryginał ↗',
    'b.openPost': 'Otwórz post ↗',
    'b.copyReply': 'Skopiuj',
    'b.save': 'Zapisz',
    'b.cancel': 'Anuluj',
    'b.reconnect': 'Połącz ponownie {who}',
    'b.connect': 'Połącz {who}',
    'b.limits': 'Limity',
    'hint.goes': 'Wyjdzie z konta {who}, po Twoim kliknięciu na stronie Wenext.',
    'hint.goesProject':
      'Wyjdzie z konta przypisanego do projektu, po Twoim kliknięciu na stronie Wenext.',
    'exp.min': 'Wygasa za {m} min.',
    'exp.hour': 'Wygasa za {h} h.',
    'exp.closed': 'Okno zgody minęło.',
    'edit.label': 'Popraw',
    'edit.placeholder': 'Wpisz nowy tekst i naciśnij Enter',
    'edit.submit': 'poproś agenta o zapis',
    'edit.hint':
      'Jedna linia: ↵ oznacza nowy wiersz. Nic się nie publikuje; agent zapisze nowy szkic.',
    'edit.same': 'W tekście nic się nie zmieniło.',
    'edit.empty': 'Tekst jest pusty.',
    'edit.dash': 'Zamiast długiej pauzy użyj kropki, przecinka albo dwukropka.',
    'note.asked': 'Poprosiłem agenta o zapis tekstu jako nowego szkicu. Karta się odświeży.',
    'note.skip': 'Poprosiłem agenta o pominięcie tej rozmowy. Karta się odświeży.',
    'note.copied': 'Skopiowano. Wklej w wątku.',
    'note.copyFailed': 'Nie udało się skopiować do schowka.',
    'note.opened': 'Otwarto w przeglądarce.',
    'note.noLink': 'Brak linku. Otwórz Wenext w przeglądarce.',
    'note.openFailed': 'Nie udało się otworzyć przeglądarki. Link jest skopiowany.',
    'note.send': 'Otwarto stronę Wenext: zatwierdzenie tam to kliknięcie, które wysyła.',
    'note.reconnect':
      'Otwarto stronę Wenext. Zaloguj się na {platform} w przeglądarce agenta. W Codexie to jedno kliknięcie na karcie.',
    'note.limits': 'Otwarto stronę Obecności, gdzie są limity i wstrzymanie.',
    'note.working': 'Czekam na agenta. Karta odświeży się, gdy pokaże Pokój jeszcze raz.',
    'acc.ok': 'połączone',
    'acc.lost': 'sesja utracona',
    'acc.never': 'jeszcze niepołączone',
    'acc.other': 'zalogowane inne konto',
    'acc.held': 'wstrzymane',
    'acc.unknown': 'nie mogę teraz sprawdzić',
    'acc.awaiting': 'czeka na Twoje logowanie',
    'sc.accounts': ['{n} konto', '{n} konta', '{n} kont'],
    'sc.hour': '{h}/h',
    'sc.day': '{d}/dobę',
    'sc.until': 'do {d}',
    'sc.paused': 'Wstrzymane',
    'sc.expired': 'Zakres wygasł',
    'sc.none': 'Brak zakresu',
    'res.votes': 'Głosy',
    'res.replies': 'Odpowiedzi',
    'fig.none': 'brak pomiaru',
    'man.hint': 'W tym trybie Wenext niczego nie wysyła: publikujesz sam.',
    'empty.body': 'Poproś agenta o znalezienie rozmów. Pojawią się tutaj.',
    'pane.none': 'Nie ma jeszcze karty Wenext. Poproś agenta o pokazanie Pokoju.',
    'band.text': [
      'Wenext: {n} osoba do odpowiedzi',
      'Wenext: {n} osoby do odpowiedzi',
      'Wenext: {n} osób do odpowiedzi',
    ],
    'band.attention': 'Wenext: {who} wymaga Twojego logowania',
    'band.open': 'Otwórz',
    'band.dismiss': 'Ukryj',
    'keys.line': 'Klawisze: s wyślij · e popraw · k pomiń · c kopiuj · p/n osoby',
    'cmd.description': 'Pokaż, kto pisze o Twoim problemie, z gotową odpowiedzią',
    'cmd.none': 'Nie ma jeszcze karty Wenext. Poproś agenta o znalezienie rozmów i pokazanie Pokoju.',
    'cmd.opened': 'Karta Wenext jest otwarta.',
    'toast.ready': 'Wenext: karta jest gotowa. Wpisz /wenext, żeby ją otworzyć.',
    'ask.editReply':
      'Poprawiłem odpowiedź czekającą na {who}{thread}. Zapisz mój tekst słowo w słowo jako nowy szkic dla tego samego wątku przez draft.create, niczego więcej nie zmieniaj i niczego nie publikuj, a potem zawołaj room.show jeszcze raz.',
    'ask.editPost':
      'Poprawiłem szkic mojego posta{where}. Zapisz mój tekst słowo w słowo jako nowy szkic przez draft.create, to samo konto i miejsce, niczego więcej nie zmieniaj i niczego nie publikuj, a potem zawołaj room.show jeszcze raz.',
    'ask.inThread': ' w {url}',
    'ask.inCommunity': ' w {c}',
    'ask.skip':
      'Pomiń tę rozmowę: {title} ({url}). Zapisz ją przez opportunities.mark z pomin=true i powod "Pominięte przez Ciebie w panelu Wenext", a potem zawołaj room.show jeszcze raz.',
    'ask.who': 'tej osoby',
  },
}

/** `pl`, `pl-PL`, `pl_PL` are Polish; everything else, and no signal at all, is English. */
export function langOf(pinned: Lang | null, locale: string | undefined): Lang {
  if (pinned === 'pl' || pinned === 'en') return pinned
  const text = (locale ?? '').trim().toLowerCase().replace(/_/g, '-')
  return /^pl(-|\.|$)/.test(text) ? 'pl' : 'en'
}

function fill(template: string, vars: Record<string, string | number> | undefined): string {
  return template.replace(/\{(\w+)\}/g, (_, name: string) => String(vars?.[name] ?? ''))
}

/** Polish has three forms (1, 2 to 4, the rest), English two (1, the rest). */
export function formIndex(lang: Lang, n: number): number {
  if (lang !== 'pl') return n === 1 ? 0 : 1
  if (n === 1) return 0
  const tens = n % 10
  const hundreds = n % 100
  return tens >= 2 && tens <= 4 && (hundreds < 12 || hundreds > 14) ? 1 : 2
}

export function say(lang: Lang, key: string, vars?: Record<string, string | number>): string {
  const value = WORDS[lang][key] ?? WORDS.en[key]
  if (value === undefined) return key
  return fill(Array.isArray(value) ? (value[0] ?? key) : value, vars)
}

/** A count: the form that agrees with `n`, filled with `{n}` and the other values. */
export function sayN(
  lang: Lang,
  key: string,
  n: number,
  vars?: Record<string, string | number>,
): string {
  const value = WORDS[lang][key] ?? WORDS.en[key]
  if (value === undefined) return key
  const forms = Array.isArray(value) ? value : [value]
  return fill(forms[Math.min(formIndex(lang, n), forms.length - 1)] ?? key, { n, ...vars })
}

/** "3 h ago", the way the card says it. */
export function ago(lang: Lang, minutes: number | null): string | null {
  if (minutes === null) return null
  if (minutes < 2) return say(lang, 'ago.now')
  if (minutes < 60) return say(lang, 'ago.min', { m: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return say(lang, 'ago.hour', { h: hours })
  const days = Math.floor(hours / 24)
  return days === 1 ? say(lang, 'ago.yesterday') : say(lang, 'ago.days', { d: days })
}
