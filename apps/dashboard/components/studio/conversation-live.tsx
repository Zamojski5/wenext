/**
 * The conversation, arriving while the agent works (P2 etap B).
 *
 * WHAT THIS COMPONENT IS. A server-rendered container plus ONE inline script. There is no client
 * bundle, no `"use client"`, no framework on the browser side: the script opens an `EventSource`
 * against THIS origin (`/studio/stream`, which proxies the control plane server-side, D-016) and
 * appends one line per event to a list that already exists in the HTML.
 *
 * WHY AN INLINE SCRIPT IS ALLOWED HERE. The same rule the shell's panel script follows: it
 * touches no session, no credential and no control plane address, it needs no bundler, and the
 * page works without it — with JavaScript off the conversation is still rendered by
 * `Conversation` above it and a reload still shows everything. The live part is an enhancement,
 * and `tests/source_scan.test.ts` keeps the package free of client components either way.
 *
 * THE MARK WAVES ONLY WHILE THE WORK IS REAL. `data-live-state` is set from the `status` frames
 * the stream sends, never from the arrival of events: a screen that animates because something
 * arrived would animate after the turn ended, and ZAKRES-FUNKCJONALNY §5 forbids showing
 * activity with no model running. `TURN_COMPLETED`, any terminal status and the stream's own
 * `koniec` frame all stop it.
 *
 * `wznow` IS NOT `koniec`, AND THAT DISTINCTION IS A BUG THIS FILE ALREADY HAD. The stream has a
 * two-minute deadline on one connection. It used to end that connection with `koniec`, this
 * script read `koniec` as "the work is over", and a session still running after two minutes
 * stopped updating on the screen for good (independent audit, 2026-09-19). Now the deadline
 * sends `wznow` and the script opens the stream again from the last sequence number it saw,
 * without touching the failure counter: a planned reconnect is not a fault. `koniec` with
 * `reason: "deadline"` is treated the same way, so a dashboard newer than the control plane
 * does not inherit the bug during a deployment.
 *
 * IF THE STREAM DIES, POLLING TAKES OVER. `EventSource` reconnects by itself, but a proxy that
 * refuses the stream entirely would leave the page frozen; after two failed connections the
 * script falls back to reloading the page every few seconds, which is what the product did
 * before this feature and is never worse than it.
 *
 * DWA MIEJSCA NA EKRANIE, BO TO SĄ DWIE RÓŻNE RZECZY (uwaga założyciela z 00:21, 20.09).
 * Rozmowa to odpowiedź agenta i zdanie człowieka; praca agenta to kroki narzędzi i szum silnika.
 * Wcześniej wszystko leciało jednym strumieniem wierszy, więc odpowiedź tonęła w liniach
 * „zdarzenie silnika: item/agentMessage/delta". Teraz odpowiedź ROŚNIE W JEDNYM AKAPICIE
 * (delta dopisuje tekst, nie zakłada wiersza), a kroki idą do zwiniętego `details` z licznikiem.
 * Nic nie jest wyrzucane: zdarzenie, którego nie widać nigdzie, jest nie do odróżnienia od
 * zdarzenia, którego nie było.
 *
 * MARKDOWN ODPOWIEDZI ZOSTAJE NA ŻYWO SUROWY, świadomie. Tutaj tekst wchodzi przez
 * `textContent`, bo to jest wstrzyknięty skrypt bez bundlera; bezpieczne minimalne renderowanie
 * markdownu robi widok historii (React, strona wenext-84), a po odświeżeniu widać właśnie jego.
 *
 * NOTHING HERE RENDERS UNTRUSTED MARKUP. Event text reaches the DOM through `textContent`, never
 * `innerHTML`: the trace carries other people's words (a fetched page, a model's answer), and a
 * screen that pastes them as HTML is a screen that executes them.
 */
import type { SessionStatus } from "../../lib/session-schema";

export interface ConversationLiveProps {
  readonly workspaceId: string;
  readonly projectId: string;
  readonly sessionId: string;
  /** The last sequence number already rendered by the server. The stream continues from it. */
  readonly afterSeq: number;
  readonly status: SessionStatus;
  /**
   * Odcisk kart, którymi serwer wyrenderował tę stronę (D-046).
   *
   * Pusty znaczy „ten obraz api kart nie liczy". Skrypt porównuje go z odciskiem z ramki
   * `karty` i przy różnicy PRZEŁADOWUJE stronę, zamiast rysować karty drugi raz z drugiej
   * kopii reguł. Patrz komentarz przy tej ramce w SCRIPT: to jest miejsce, w którym ekran
   * najłatwiej zacząłby mówić „Połączono" wcześniej niż serwer.
   */
  readonly cardsDigest?: string;
}

/** Statuses that mean the stream has something to wait for. Same list as the backend's. */
const LIVE: readonly SessionStatus[] = ["RUNNING", "QUEUED"];

//: NAZWY PÓL Z KONTRAKTU ZDARZEŃ, uzgodnionego z sesją, która trzyma zapis (20.09). W jednym
//: miejscu, więc zmiana nazwy to jedna linia, a nie pięć miejsc w skrypcie.
//:
//: DELT NIE MA I NIE BĘDZIE: backend nie zapisuje już `agentMessage/delta`, więc każdy
//: `ASSISTANT_MESSAGE` niesie CAŁĄ wypowiedź. `message_id` powtórzony drugi raz (wznowienie,
//: ponowny odczyt śladu) ma ZASTĄPIĆ akapit, nie dostawić drugiego obok.
const FIELDS = {
  message: "message_id",
  level: "level",
  engine: "engine",
  summary: "summary",
  typing: "wenext_pisze",
} as const;

const SCRIPT = `(function(){
if(typeof EventSource==='undefined'){return}
// NAJPIERW HYDRATACJA, POTEM WĘZŁY (27.09). Skrypt z HTML-u biegnie, zanim React przejmie stronę.
// Otwierał strumień od razu, pierwsza ramka zmieniała etykietę stanu, React widział tekst inny niż
// z serwera (błąd #418), wyrzucał DOM i rysował go od nowa, a skrypt pisał dalej do odłączonych
// węzłów: 56 ramek w 6 s i zero kroków na ekranie. Znacznik stawia komponent kliencki po
// zamontowaniu; bez niego po 3 s skrypt rusza i tak, bo strona bez hydratacji też ma żyć.
var begin=function(){
var root=document.querySelector('[data-live-root]');
if(!root){return}
var flow=root.querySelector('[data-live-flow]');
var steps=root.querySelector('[data-live-steps]');
var stepList=root.querySelector('[data-live-step-list]');
var stepCount=root.querySelector('[data-live-step-count]');
var engineBlock=root.querySelector('[data-live-engine]');
var engineList=root.querySelector('[data-live-engine-list]');
var engineCount=root.querySelector('[data-live-engine-count]');
var mark=root.querySelector('[data-live-state]');
var work=root.querySelector('[data-live-work]');
var workLabel=root.querySelector('[data-live-work-label]');
var url=root.getAttribute('data-live-url');
var seq=parseInt(root.getAttribute('data-live-seq')||'0',10);
var F=${JSON.stringify(FIELDS)};
var currentStatus=root.getAttribute('data-session-status')||'';
var wasLive=currentStatus==='RUNNING'||currentStatus==='QUEUED';
var failures=0;
var source=null;
var stepsSeen=0;
var engineSeen=0;
var pen=root.querySelector('[data-live-typing]');
var reveal=function(){if(flow&&flow.hasAttribute('hidden')){flow.removeAttribute('hidden')}};
// ZNACZNIK „PISZE" JEST WIDOKIEM, NIE WIERSZEM. Silnik przysyła go raz na wypowiedź, żeby ekran
// nie milczał przez pół minuty generowania; gdy przyjdzie gotowa odpowiedź, znika. Wiersz na
// liście zostałby po nim na zawsze i mówiłby o czymś, co się już skończyło.
var typing=function(on){if(pen){pen.hidden=!on}};
var setState=function(value){if(mark){mark.setAttribute('data-live-state',value)}if(work){work.hidden=value==='idle';work.setAttribute('data-playing',String(value==='running'))}};
var refresh=function(){
if(window.wenextSoftRefresh){window.dispatchEvent(new CustomEvent('wenext:karty'));return}
window.location.reload();};
var counted=function(){
root.setAttribute('data-live-count',String((flow?flow.children.length:0)+stepsSeen+engineSeen));};
var plural=function(n,one,few,many){return n===1?one:(n%10>=2&&n%10<=4&&(n%100<10||n%100>=20)?few:many)};
// JEDNA WYPOWIEDŹ, JEDEN AKAPIT. Ten sam „message_id” przychodzi drugi raz przy wznowieniu
// strumienia i przy ponownym czytaniu śladu; wtedy PODMIENIAMY treść zamiast dostawiać drugi
// akapit z tym samym tekstem. To jest cała różnica między rozmową a listą duplikatów.
var say=function(text,id){
var existing=id?flow.querySelector('[data-live-answer="'+id+'"]'):null;
if(existing){existing.textContent=text;typing(false);counted();return}
var item=document.createElement('p');
item.className='st-live__answer';
item.setAttribute('data-live-answer',id||'1');
item.textContent=text;
reveal();flow.appendChild(item);
typing(false);
counted();};
var said=function(kind,text){
var item=document.createElement('p');
item.className='st-live__said';
item.setAttribute('data-live-event',kind);
item.textContent=text;
reveal();flow.appendChild(item);
counted();};
var row=function(list,kind,text){
var item=document.createElement('li');
item.className='st-note';
item.setAttribute('data-live-event',kind);
item.textContent=text;
list.appendChild(item);};
// DWA POZIOMY, DWA PYTANIA. „Co agent zrobił" to kroki pracy: wywołania narzędzi i zdania
// o przebiegu. „Co się działo w silniku" to mechanika, o jedną zapadkę głębiej i POZA licznikiem
// kroków. Ten sam podział ma widok historii; jeden licznik na obie rzeczy znaczyłby, że ta sama
// tura pokazuje na dwóch ekranach dwie różne liczby, a człowiek pyta, która kłamie.
var step=function(kind,text){
if(!stepList){return}
row(stepList,kind,text);
stepsSeen=stepsSeen+1;
if(steps){steps.hidden=false}
if(stepCount){stepCount.textContent='Przebieg pracy'}
counted();};
// SZUM SILNIKA: chowany o poziom głębiej, nigdy wyrzucany. Tu ląduje też podsumowanie ramek
// pominiętych w zapisie, żeby liczba, której nikt nie zapisał wierszami, nie zginęła całkiem.
var engine=function(kind,text){
if(!engineList){return}
row(engineList,kind,text);
engineSeen=engineSeen+1;
if(steps){steps.hidden=false}
if(engineBlock){engineBlock.hidden=false}
if(engineCount){engineCount.textContent='Szczegóły techniczne: '+engineSeen+' '+plural(engineSeen,'wpis silnika','wpisy silnika','wpisów silnika')}
counted();};
var stop=function(){if(source){source.close();source=null}setState('idle')};
// Planowane wznowienie: zamykamy TO połączenie i otwieramy następne od ostatniego numeru.
// Stan zostaje bez zmiany, bo sesja nadal pracuje, i licznik awarii zostaje wyzerowany, bo to
// nie jest awaria. Zamknięcie własnym close() nie odpala 'error', więc nic się nie dubluje.
var again=function(){if(source){source.close();source=null}failures=0;open()};
var fallback=function(){
// The stream is unavailable. Reloading every few seconds is what the product did before this
// feature; it is slower and it is never wrong.
setState('polling');
window.setTimeout(function(){window.location.reload()},5000);};
// Odcisk kart, którymi serwer wyrenderował tę stronę. Pusty znaczy „ten obraz api kart nie
// liczy", i wtedy żadna ramka 'karty' nie przyjdzie, więc nic się nie przeładuje.
var cards=root.getAttribute('data-cards-digest')||'';
var open=function(){
source=new EventSource(url+'&after_seq='+seq);
source.addEventListener('open',function(){failures=0});
// RAMKA STATUS STERUJE WYŁĄCZNIE ZNAKIEM. Nie dopisuje wiersza i nie rusza odpowiedzi: stan
// sesji jest jedną rzeczą, a treść rozmowy drugą, i mieszanie ich było połową tego bałaganu.
source.addEventListener('status',function(message){
var data=JSON.parse(message.data);
setState(data.status==='RUNNING'?'running':data.status==='QUEUED'?'queued':'idle');
var labels={QUEUED:['W kolejce','Rozmowa ruszy, gdy zwolni się silnik.'],RUNNING:['Pracuje','Możesz dopisać kontekst w trakcie.'],WAITING_USER:['Odpowiedź gotowa','Możesz kontynuować rozmowę.']};
var pair=labels[data.status];
if(pair){
var label=document.querySelector('[data-session-label]');
var note=document.querySelector('[data-session-note]');
if(label){label.textContent=pair[0]}
if(note){note.textContent=pair[1]}}
currentStatus=data.status;
wasLive=wasLive||currentStatus==='RUNNING'||currentStatus==='QUEUED';});
source.addEventListener('event',function(message){
var data=JSON.parse(message.data);
if(typeof data.seq==='number'&&data.seq>seq){seq=data.seq}
var payload=data.payload||{};
if(payload[F.level]===F.engine){
// Znacznik pisania: pokaż „pisze…" i NIE dokładaj wiersza. To jest stan, nie zdarzenie.
if(payload[F.typing]){typing(true);return}
engine(data.kind,payload[F.summary]||payload.detail||data.kind);return}
if(data.kind==='ASSISTANT_MESSAGE'){
var text=payload.text||'';
if(text){say(text,payload[F.message]||'')}
return}
if(data.kind==='USER_MESSAGE'){said(data.kind,payload.text||'');return}
if(data.kind==='TOOL_CALL'||data.kind==='TOOL_RESULT'){
var activities={'guidance.list':'Dobieram podejście','guidance.read':'Analizuję strategię','news.search':'Szukam źródeł w newsletterze','browser.inspect':'Oglądam stronę','x.search':'Szukam na X','x.read':'Czytam X','materials.save':'Zachowuję materiał','catalog.list':'Sprawdzam dostępne możliwości','project.read':'Czytam kontekst projektu','sources.list':'Przeglądam materiały','sources.search':'Szukam źródeł','sources.find':'Szukam fragmentów w materiale','sources.read':'Czytam źródła','memory.search':'Wracam do ustaleń','memory.write':'Zapisuję ustalenia','threads.search':'Szukam rozmów','thread.read':'Czytam rozmowę'};
if(workLabel&&data.kind==='TOOL_CALL'){workLabel.textContent=activities[payload.tool]||'Pracuję nad Twoim zadaniem'}
step(data.kind,payload[F.summary]||((payload.tool||'narzędzie')+(payload.status?': '+payload.status:'')));
return}
if(data.kind==='TURN_COMPLETED'){setState('idle');return}
// STATUS ZE STANEM SESJI TO TREŚĆ, NIE KROK. „Sesja nie ma połączenia Codex, na którym mogłaby
// ruszyć" jest odpowiedzią produktu do człowieka; schowana pod zapadką byłaby odpowiedzią ukrytą
// przed tym, kto na nią czeka. Pozostałe STATUS-y są zdaniem o przebiegu i idą do kroków.
if(data.kind==='STATUS'&&payload.status){said(data.kind,payload.detail||payload[F.summary]||String(payload.status));return}
step(data.kind,payload[F.summary]||payload.detail||data.kind);});
// KARTY ZMIENIŁY STAN (D-046). Skrypt ICH NIE PRZERYSOWUJE i to jest decyzja, nie brak czasu:
// karty rysuje serwer z projekcji śladu, a drugie rysowanie, w przeglądarce, z drugiej kopii
// reguł, jest dokładnie tym miejscem, w którym ekran zaczyna pisać „Połączono" wcześniej niż
// serwer. Zamiast tego przeładowujemy stronę, gdy odcisk kart się zmieni: przeładowanie pokazuje
// to, co serwer ma naprawdę, a kart zmienia się w rozmowie kilka, nie kilkaset.
source.addEventListener('karty',function(message){
var next='';
try{next=(JSON.parse(message.data)||{}).odcisk||''}catch(error){next=''}
if(!next||next===cards)return;
cards=next;
// MIĘKKIE ODŚWIEŻENIE, gdy jest komu je wykonać: RefreshOnCards woła router.refresh(),
// więc serwer rysuje stronę na nowo, a DOM jest różnicowany w miejscu. Tekst w polu, fokus,
// przewinięcie i przejścia CSS zostają. Gdy hydratacja się nie udała, znacznika nie ma i
// wracamy do przeładowania: lepiej zabrać komuś wpisane zdanie niż pokazywać stary stan karty.
if(window.wenextSoftRefresh){window.dispatchEvent(new CustomEvent('wenext:karty'));return}
window.location.reload();});
source.addEventListener('wznow',function(){again()});
source.addEventListener('koniec',function(message){
// Stary control plane kończył połączenie po limicie czasu ramką 'koniec' z powodem 'deadline'.
// Ten warunek jest po to, żeby nowszy dashboard na starszym backendzie nie odziedziczył błędu.
var reason='';
try{reason=(JSON.parse(message.data)||{}).reason||''}catch(error){reason=''}
if(reason==='deadline'){again();return}
stop();
// Render the completed answer with the same markdown and cards as its history.
// Hide only the live enhancement once the server takes over; the stored trace remains.
if(wasLive){root.hidden=true;refresh()}});
source.addEventListener('error',function(){
if(source){source.close();source=null}
failures=failures+1;
if(failures>=2){fallback();return}
window.setTimeout(open,2000);});};
open();};
var waited=0;
var ready=function(){
if(window.wenextSoftRefresh||waited>=60){begin();return}
waited=waited+1;
window.setTimeout(ready,50);};
ready();})();`;

export function ConversationLive({
  workspaceId,
  projectId,
  sessionId,
  afterSeq,
  status,
  cardsDigest = "",
}: ConversationLiveProps) {
  const live = LIVE.includes(status);
  const url =
    `/studio/stream?workspace_id=${encodeURIComponent(workspaceId)}` +
    `&project_id=${encodeURIComponent(projectId)}&session_id=${encodeURIComponent(sessionId)}`;

  return (
    <section
      className="st__section"
      data-cards-digest={cardsDigest}
      data-session-status={status}
      data-live-count="0"
      data-live-root="1"
      data-live-seq={afterSeq}
      data-live-url={url}
    >
      {/* NAGŁÓWEK „NA ŻYWO" I AKAPIT O STRUMIENIU ZNIKŁY Z EKRANU (odbiór 1, różnica nr 8).
          Referencja nie ma pod polem niczego: pole zamyka kolumnę. Strumień zostaje MECHANIZMEM,
          a nie sekcją do czytania, bo człowiek nie potrzebuje wiedzieć, że coś dopisuje się bez
          odświeżania; potrzebuje widzieć, że rozmowa trwa, a to mówi pasek stanu tury na górze
          i zdanie w pasku pola.

          Znacznik stanu ZOSTAJE w drzewie, ukryty: skrypt strumienia ustawia na nim
          `data-live-state` przy każdej ramce, a czytnik ekranu czyta z niego `aria-label`.
          Usunięcie go zabrałoby mechanizmowi jedyne miejsce, w którym mówi, co się dzieje. */}
      <span
        aria-label={live ? "tura trwa" : "nic nie jest w toku"}
        className="st-live-state"
        data-live-state={live ? "running" : "idle"}
        hidden
      >
        {live ? "tura trwa" : "cicho"}
      </span>
      {/* Puste na serwerze, wypełniane strumieniem. DWA MIEJSCA, NIE JEDNO: w nurcie stoi to,
          co powiedziano (odpowiedź agenta rośnie w jednym akapicie, wiadomość człowieka jest
          osobnym), a kroki narzędzi i szum silnika mieszkają w zwiniętym bloku pod spodem.
          Licznik jest na kontenerze, żeby test mógł go przeczytać.

          `hidden` DOPÓKI NIC NIE PRZYSZŁO (odbiór 2, nr 8): pusta powierzchnia 720 x 8 px pod
          każdą rozmową to był pasek znikąd, a referencja nie ma pod polem niczego. Skrypt zdejmuje
          `hidden` przy pierwszej ramce, bo wtedy jest co pokazać. */}
      <div className="st-live-work" data-live-work="1" data-playing={String(status === "RUNNING")} hidden={!live} role="status">
        <img alt="" src="/brand/mark-white.png" width={22} height={20} />
        <span data-live-work-label="1">{status === "QUEUED" ? "Czekam na uruchomienie" : "Pracuję nad Twoim zadaniem"}</span>
      </div>
      <div className="st-live-flow" data-live-flow="1" hidden />
      {/* „pisze…" pojawia się na znacznik silnika i znika, gdy przyjdzie gotowa odpowiedź.
          Ukryte na serwerze: dopóki nic nie przyszło, nikt nie pisze. */}
      <p className="st-note" data-live-typing="1" hidden>
        Agent pisze odpowiedź.
      </p>
      <details className="st-note" data-live-steps="1" hidden>
        <summary data-live-step-count="1">Przebieg pracy</summary>
        <ul data-live-step-list="1" />
        {/* Mechanika o jedną zapadkę głębiej i POZA licznikiem kroków: ten sam podział ma widok
            historii, więc ta sama tura pokazuje po obu stronach tę samą liczbę. */}
        <details className="st-note" data-live-engine="1" hidden>
          <summary data-live-engine-count="1">Szczegóły techniczne: 0 wpisów silnika</summary>
          <ul data-live-engine-list="1" />
        </details>
      </details>
      <noscript>
        <p className="st-note">
          Bez JavaScriptu strumień nie działa. Rozmowa powyżej jest kompletna, a odświeżenie
          strony pokazuje wszystko, co przyszło.
        </p>
      </noscript>
      {/* Komponent kliencki, który słucha zdarzenia z tego skryptu, renderuje STRONA, nie ten
          blok. Powód jest prozaiczny: `useRouter` wymaga routera, a ten blok jest renderowany
          też w testach komponentów, gdzie routera nie ma i nie powinno być. */}
      <script dangerouslySetInnerHTML={{ __html: SCRIPT }} />
    </section>
  );
}
