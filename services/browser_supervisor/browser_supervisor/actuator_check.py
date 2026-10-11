"""The comment actuator measured with REAL Playwright against a stub page on the loopback (D-028).

Why this exists: the unit tests of this package drive the `/act/...` endpoints with a fake
actuator, so the path that actually attaches to Chrome, fills a textarea and clicks a button was
never executed before the first live smoke (2026-09-09 00:4x), which then failed before touching
the page. This check runs `CdpCommentActuator` itself, inside the capsule image, against pages
that mimic the pieces of old.reddit.com the actuator relies on: the account link in the header,
`div.commentarea > form.usertext` with `textarea[name=text]` and `button.save`, and the new
`.thing.comment[data-author][data-fullname]` with `a.bylink` that appears after saving; and, since
0023, `form#newlink` with its title and body fields, whose sending NAVIGATES to the new thread,
where `.thing.link[data-author][data-fullname]` is the proof that the thread is ours.

Like gate 2 it manages its own capsule (HEADLESS is enough for a loopback page) and writes an
artifact on every exit path; a run that could not measure leaves an UNCLOSED artifact, never a pass.
Invocation, in a throwaway container with its own profile directory so it never touches the live
profile:

    docker compose run --rm --no-deps -e WENEXT_PROFILES_DIR=/tmp/actuator-profiles capsule \\
        python3 -m browser_supervisor.main --actuator-check --artifacts /tmp/artifacts
"""

from __future__ import annotations

import http.server
import json
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

from community_os_contracts import CapsuleMode

from .contract import CheckResult, ContractResult
from .supervisor import BrowserSupervisor
from .writer import CdpCommentActuator, CommentBlocked, CommentRefused, WriteBlocked

ARTIFACT_NAME = "actuator-check.json"
ACCOUNT = "Demo_Maintainer"
STRANGER = "SomebodyElse"
POST_ID = "abc123"
TEXT = "A JSON schema fixes argument hallucination, not authority. Stub check."
TITLE = "Co robi kontrola aktuatora, kiedy nikt nie patrzy"


def _page(*, account: str, locked: bool = False) -> bytes:
    """An old.reddit-shaped thread page. Saving appends a comment by `account` to the DOM, the way
    old reddit's JS does, so the actuator's read-back has something real to read."""
    header = (
        '<div id="header-bottom-right"><span class="user">'
        f'<a href="/user/{account}">{account}</a></span></div>'
    )
    if locked:
        area = (
            '<div class="commentarea"><p class="archived">This is an archived post. '
            "You won't be able to vote or comment.</p></div>"
        )
    else:
        area = f"""
        <div class="commentarea">
          <form class="usertext cloneable" id="form-t3_{POST_ID}" onsubmit="return save(event)">
            <div class="usertext-edit"><textarea name="text" rows="4"></textarea></div>
            <div class="usertext-buttons"><button type="submit" class="save">save</button></div>
            <span class="error" style="display:none"></span>
          </form>
          <div class="sitetable nestedlisting" id="siteTable_t3_{POST_ID}">
            <div class="thing comment" data-author="earlier_user" data-fullname="t1_e0">
              <p>earlier</p>
              <a class="bylink" href="/r/testsub/comments/{POST_ID}/stub/e0/">permalink</a>
            </div>
          </div>
        </div>
        <script>
          function save(ev) {{
            ev.preventDefault();
            var text = document.querySelector('textarea[name=text]').value;
            var id = 'n' + Math.floor(Math.random() * 1e6).toString(36);
            var div = document.createElement('div');
            div.className = 'thing comment';
            div.setAttribute('data-author', '{account}');
            div.setAttribute('data-fullname', 't1_' + id);
            div.innerHTML = '<p></p><a class="bylink" href="/r/testsub/comments/{POST_ID}/stub/'
              + id + '/">permalink</a>';
            div.querySelector('p').textContent = text;
            document.getElementById('siteTable_t3_{POST_ID}').appendChild(div);
            return false;
          }}
        </script>
        """
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>stub</title></head>"
        f"<body>{header}{area}</body></html>"
    )
    return html.encode("utf-8")


def _www_thread_page(
    *, account: str, locked: bool = False, collapsed: bool = False, delayed_account: bool = False
) -> bytes:
    """Strona wątku NOWEGO interfejsu, odwzorowana z artefaktu sondy z 21.09.

    Nazwy elementów i atrybutów są przepisane z pliku
    `output/community-probe/www_reddit_com_r_ecommerce_comments_1wixhex_….html`, nie wymyślone:
    `rs-current-user[display-name]` niesie zalogowane konto, `comment-composer-host` opakowuje
    `faceplate-form` z `div[slot=rte][contenteditable]` i przyciskiem
    `#comment-composer-submit-button`, a komentarze to `shreddit-comment` z `author`, `thingid`
    i `permalink`.

    `locked=True` to wątek BEZ kompozytora: tak wygląda strona, na której konto nie ma prawa
    pisać, i aktuator ma to rozpoznać PRZED wpisaniem czegokolwiek.

    WYSYŁKĘ PRZECHWYTUJE KLIKNIĘCIE, NIE ZDARZENIE FORMULARZA, i to jest poprawka z pomiaru
    21.09, nie wygodniejszy zapis. `faceplate-form` jest ELEMENTEM WŁASNYM, a nie `<form>`:
    `type="submit"` nie ma tam czego wysłać, więc `onsubmit` nie zdarza się nigdy. Pierwsza
    wersja tej atrapy stała na `onsubmit` i kontrola aktuatora wyszła z „po wysłaniu przez 30 s
    nie widać komentarza", co wyglądało jak wina wpisywania. Zmierzone w prawdziwej przeglądarce:
    `fill()` na `contenteditable` DZIAŁA (treść ląduje), a kliknięcie nie robiło nic. Prawdziwy
    komponent też nasłuchuje kliknięcia i sam wysyła żądanie, więc atrapa jest teraz bliżej
    artefaktu, a nie bliżej kodu.
    """
    composer = (
        ""
        if locked
        else f"""
        <comment-body-header>
        <faceplate-textarea-input data-testid="trigger-button"
                                  {"hidden" if not collapsed else ""}>
          <template shadowrootmode="open">
            <textarea placeholder="Join the conversation"
              onclick="const host=this.getRootNode().host;
                       host.nextElementSibling.querySelector('[data-testid]').hidden=false;
                       host.hidden=true;"></textarea>
          </template>
        </faceplate-textarea-input>
        <comment-composer-host slot="ready" post-id="t3_{POST_ID}" user-id="t2_stub">
          <faceplate-textarea-input data-testid="trigger-button" hidden>
            <template shadowrootmode="open">
              <textarea placeholder="Join the conversation"
                onclick="const host=this.getRootNode().host;
                         host.nextElementSibling.style.display='block';
                         host.hidden=true;"></textarea>
            </template>
          </faceplate-textarea-input>
          <faceplate-form action="/svc/shreddit/t3_{POST_ID}/create-comment"
                          style="display: {"none" if collapsed else "block"}"
                          target="#next-comment" partial-mode="replace" method="post">
            <input type="hidden" name="recaptchaToken">
            <shreddit-composer name="content" mode="richText" required
                               placeholder="Join the conversation">
              <div slot="rte" contenteditable="true" role="textbox" tabindex="1"
                   name="body"></div>
            </shreddit-composer>
            <button id="comment-composer-cancel-button" slot="cancel-button"
                    type="reset">anuluj</button>
            <button id="comment-composer-submit-button" slot="submit-button"
                    type="submit" onclick="return saved(event)">Comment</button>
          </faceplate-form>
        </comment-composer-host>
        </comment-body-header>
        <div id="next-comment"></div>
        <script>
          function saved(ev) {{
            ev.preventDefault();
            var body = document.querySelector('div[slot=rte]');
            var id = 'n' + Math.floor(Math.random() * 1e6).toString(36);
            var node = document.createElement('shreddit-comment');
            node.setAttribute('author', '{account}');
            node.setAttribute('thingid', 't1_' + id);
            node.setAttribute('postid', 't3_{POST_ID}');
            node.setAttribute('permalink', '/r/testsub/comments/{POST_ID}/comment/' + id + '/');
            node.textContent = body.innerText;
            document.getElementById('next-comment').appendChild(node);
            return false;
          }}
        </script>
        """
    )
    identity = f'<rs-current-user id="t2_stub" display-name="{account}"></rs-current-user>'
    if delayed_account:
        identity = (
            f'<template id="identity">{identity}</template>'
            "<script>setTimeout(() => document.body.appendChild("
            'document.getElementById("identity").content.cloneNode(true)), 300);</script>'
        )
    head = (
        identity + f'<shreddit-comment author="earlier_user" thingid="t1_e0" postid="t3_{POST_ID}" '
        f'permalink="/r/testsub/comments/{POST_ID}/comment/e0/">earlier</shreddit-comment>'
    )
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>watek www</title></head>"
        f"<body>{head}{composer}</body></html>"
    )
    return html.encode("utf-8")


def _submit_page(*, account: str) -> bytes:
    """An old.reddit-shaped submit form (0023). Sending it NAVIGATES to the new thread's address,
    the way old reddit does, because that navigation is what the actuator reads as proof."""
    header = (
        '<div id="header-bottom-right"><span class="user">'
        f'<a href="/user/{account}">{account}</a></span></div>'
    )
    form = f"""
        <form id="newlink" onsubmit="return submitted(event)">
          <input name="title" type="text" />
          <div><textarea name="text" rows="4"></textarea></div>
          <button name="submit" type="submit">submit</button>
          <span class="error" style="display:none"></span>
        </form>
        <script>
          function submitted(ev) {{
            ev.preventDefault();
            var title = document.querySelector('#newlink [name=title]').value;
            var id = 'p' + Math.floor(Math.random() * 1e6).toString(36);
            location.href = '/r/testsub/comments/' + id + '/?account={account}'
              + '&title=' + encodeURIComponent(title);
            return false;
          }}
        </script>
    """
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>submit</title></head>"
        f"<body>{header}{form}</body></html>"
    )
    return html.encode("utf-8")


def _new_thread_page(*, account: str, post_id: str, title: str) -> bytes:
    """The page the submit form lands on: our thread, with the author and the permalink the
    actuator reads off `.thing.link`."""
    header = (
        '<div id="header-bottom-right"><span class="user">'
        f'<a href="/user/{account}">{account}</a></span></div>'
    )
    thing = (
        f'<div class="thing link" data-author="{account}" data-fullname="t3_{post_id}">'
        f"<p class='title'>{title}</p>"
        f'<a class="comments" href="/r/testsub/comments/{post_id}/stub/">komentarze</a></div>'
    )
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>watek</title></head>"
        f"<body>{header}{thing}</body></html>"
    )
    return html.encode("utf-8")


class _Stub(http.server.BaseHTTPRequestHandler):
    """Cztery strony: `/r/testsub/comments/abc123/?account=…&locked=1` (wątek starego
    interfejsu), `/www/r/testsub/comments/abc123/?account=…&locked=1` (ten sam wątek w NOWYM
    interfejsie, z kompozytorem w komponencie),
    `/r/testsub/submit?account=…` (the form) and `/r/testsub/comments/<other id>/` (the thread
    the form lands on after sending)."""

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = parse_qs(query)
        account = (params.get("account") or [ACCOUNT])[0]
        trimmed = path.rstrip("/")
        if trimmed == f"/r/testsub/comments/{POST_ID}":
            locked = (params.get("locked") or ["0"])[0] == "1"
            self.send_response(200)
            self._send(_page(account=account, locked=locked))
            return
        if trimmed == "/r/testsub/submit":
            self.send_response(200)
            self._send(_submit_page(account=account))
            return
        if trimmed == f"/www/r/testsub/comments/{POST_ID}":
            locked = (params.get("locked") or ["0"])[0] == "1"
            self.send_response(200)
            self._send(
                _www_thread_page(
                    account=account,
                    locked=locked,
                    collapsed=(params.get("collapsed") or ["0"])[0] == "1",
                    delayed_account=(params.get("delayed_account") or ["0"])[0] == "1",
                )
            )
            return
        if trimmed.startswith("/r/testsub/comments/"):
            post_id = trimmed.rsplit("/", 1)[-1]
            title = (params.get("title") or [""])[0]
            self.send_response(200)
            self._send(_new_thread_page(account=account, post_id=post_id, title=title))
            return
        self.send_response(404)
        self._send(b"nie ma")

    def _send(self, content: bytes) -> None:
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_args):
        return


def _start_stub() -> tuple[http.server.HTTPServer, int]:
    server = http.server.HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


@dataclass
class CheckRun:
    rozpoczety: str
    zakonczony: str | None = None
    szczegol: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_artifact(directory: Path, result: ContractResult, run: CheckRun) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    closed = run.zakonczony is not None
    kontrole = []
    for check in result.kontrole:
        entry = asdict(check)
        entry["szczegoly"] = {**(entry.get("szczegoly") or {}), "przebieg": asdict(run)}
        if not closed:
            entry["szczegoly"]["wymaga_czlowieka"] = True
        kontrole.append(entry)
    data = {
        "zdane": bool(kontrole) and closed and result.zdane,
        "liczba_kontroli": len(kontrole),
        "kontrole": kontrole,
        "przebieg": asdict(run),
    }
    (directory / ARTIFACT_NAME).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def run_actuator_check(supervisor: BrowserSupervisor, *, artifacts_dir: Path) -> ContractResult:
    result = ContractResult()
    run = CheckRun(rozpoczety=_now())
    stub: http.server.HTTPServer | None = None
    try:
        stub, port = _start_stub()
        base = f"http://127.0.0.1:{port}/r/testsub/comments/{POST_ID}/"
        capsule = supervisor.start(CapsuleMode.HEADLESS)
        # The check is the sole Playwright user of this thread: its own driver is right here.
        actuator = CdpCommentActuator(
            capsule.endpoint.http_base,
            url_policy=lambda u: u,
            # Atrapa stoi na pętli zwrotnej, więc polityka adresu przepuszcza to, co dostała:
            # tutaj mierzymy wpisywanie, klikanie i ODCZYT DOWODU, a nie listę dozwolonych
            # adresów, którą mierzą testy jednostkowe (`authorize_submit_url`).
            submit_policy=lambda c: c,
            # Trzecia polityka, z tego samego powodu: mierzymy kompozytor nowego interfejsu na
            # atrapie z pętli zwrotnej, a nie listę dozwolonych adresów.
            www_policy=lambda u: u,
        )

        # 1. The happy path: fill, save, read the new comment back from the DOM.
        try:
            outcome = actuator.comment(
                thread_url=f"{base}?account={ACCOUNT}",
                parent_fullname=f"t3_{POST_ID}",
                text=TEXT,
                account=ACCOUNT,
                timeout_s=30.0,
            )
            ok = (
                outcome.fullname.startswith("t1_n")
                and outcome.account == ACCOUNT
                and outcome.permalink.startswith("https://www.reddit.com/r/testsub/comments/")
            )
            result.add(
                CheckResult(
                    nazwa="aktuator_wypelnia_klika_i_odczytuje_dowod",
                    wymog_z_dokumentu="D-028: dowód = odczyt komentarza z DOM po zapisie",
                    zdane=ok,
                    dowod=f"fullname={outcome.fullname} permalink={outcome.permalink}",
                    szczegoly={"raw": dict(outcome.raw)},
                )
            )
        except Exception as exc:
            result.add(
                CheckResult(
                    nazwa="aktuator_wypelnia_klika_i_odczytuje_dowod",
                    wymog_z_dokumentu="D-028: dowód = odczyt komentarza z DOM po zapisie",
                    zdane=False,
                    dowod=f"{type(exc).__name__}: {exc}"[:400],
                )
            )

        # 2. Another account in the header: BLOCKED, nothing typed.
        try:
            actuator.comment(
                thread_url=f"{base}?account={STRANGER}",
                parent_fullname=f"t3_{POST_ID}",
                text=TEXT,
                account=ACCOUNT,
                timeout_s=30.0,
            )
            verdict, evidence = False, "aktuator zapisał mimo obcego konta w nagłówku"
        except CommentBlocked as exc:
            verdict, evidence = True, f"CommentBlocked: {exc}"[:300]
        except Exception as exc:
            verdict, evidence = False, f"{type(exc).__name__}: {exc}"[:300]
        result.add(
            CheckResult(
                nazwa="obce_konto_w_naglowku_blokuje",
                wymog_z_dokumentu="D-028: konto na stronie = konto zgody, inaczej BLOCKED",
                zdane=verdict,
                dowod=evidence,
            )
        )

        # 3. An archived thread without a form: REFUSED, nothing typed.
        try:
            actuator.comment(
                thread_url=f"{base}?account={ACCOUNT}&locked=1",
                parent_fullname=f"t3_{POST_ID}",
                text=TEXT,
                account=ACCOUNT,
                timeout_s=30.0,
            )
            verdict, evidence = False, "aktuator zapisał w zarchiwizowanym wątku"
        except CommentRefused as exc:
            verdict, evidence = True, f"CommentRefused: {exc}"[:300]
        except Exception as exc:
            verdict, evidence = False, f"{type(exc).__name__}: {exc}"[:300]
        result.add(
            CheckResult(
                nazwa="zarchiwizowany_watek_odmawia",
                wymog_z_dokumentu="D-028: brak formularza = odmowa przed wysłaniem",
                zdane=verdict,
                dowod=evidence,
            )
        )
        # 4. Własny wątek: wypełnij tytuł i treść, wyślij, odczytaj wątek z adresu i z DOM.
        submit = f"http://127.0.0.1:{port}/r/testsub/submit"
        try:
            posted = actuator.own_post(
                community=f"{submit}?account={ACCOUNT}",
                title=TITLE,
                text=TEXT,
                account=ACCOUNT,
                timeout_s=30.0,
            )
            ok = (
                posted.fullname.startswith("t3_p")
                and posted.account == ACCOUNT
                and posted.permalink.startswith("https://www.reddit.com/r/testsub/comments/")
            )
            result.add(
                CheckResult(
                    nazwa="aktuator_zaklada_watek_i_odczytuje_dowod",
                    wymog_z_dokumentu="0023: dowód = wątek odczytany po adresie i po autorze",
                    zdane=ok,
                    dowod=f"fullname={posted.fullname} permalink={posted.permalink}",
                    szczegoly={"raw": dict(posted.raw), "tytul": posted.title},
                )
            )
        except Exception as exc:
            result.add(
                CheckResult(
                    nazwa="aktuator_zaklada_watek_i_odczytuje_dowod",
                    wymog_z_dokumentu="0023: dowód = wątek odczytany po adresie i po autorze",
                    zdane=False,
                    dowod=f"{type(exc).__name__}: {exc}"[:400],
                )
            )

        # 5. Obce konto w nagłówku formularza: BLOCKED, nic nie wpisane.
        try:
            actuator.own_post(
                community=f"{submit}?account={STRANGER}",
                title=TITLE,
                text=TEXT,
                account=ACCOUNT,
                timeout_s=30.0,
            )
            verdict, evidence = False, "aktuator założył wątek mimo obcego konta w nagłówku"
        except WriteBlocked as exc:
            verdict, evidence = True, f"WriteBlocked: {exc}"[:300]
        except Exception as exc:
            verdict, evidence = False, f"{type(exc).__name__}: {exc}"[:300]
        result.add(
            CheckResult(
                nazwa="obce_konto_blokuje_wlasny_watek",
                wymog_z_dokumentu="0023: konto na stronie = konto zgody, inaczej BLOCKED",
                zdane=verdict,
                dowod=evidence,
            )
        )

        # 6. NOWY INTERFEJS, ta sama trójka pytań. Powstała, bo 21.09 stary interfejs zaczął
        # oddawać tej przeglądarce 403 i stronę blokady, także na stronie wątku, który pilot
        # komentował 17.09. Atrapa odwzorowuje markup z artefaktu sondy, nie z pamięci.
        www = f"http://127.0.0.1:{port}/www/r/testsub/comments/{POST_ID}/"
        try:
            outcome = actuator.comment_www(
                thread_url=f"{www}?account={ACCOUNT}&collapsed=1&delayed_account=1",
                parent_fullname=f"t3_{POST_ID}",
                text=TEXT,
                account=ACCOUNT,
                timeout_s=30.0,
            )
            ok = (
                outcome.fullname.startswith("t1_n")
                and outcome.account == ACCOUNT
                and outcome.permalink.startswith("https://www.reddit.com/r/testsub/comments/")
            )
            result.add(
                CheckResult(
                    nazwa="aktuator_www_wpisuje_klika_i_odczytuje_dowod",
                    wymog_z_dokumentu=(
                        "plan przeniesienia na www: dowodem jest `shreddit-comment` naszego "
                        "konta, którego przed wysłaniem nie było"
                    ),
                    zdane=ok,
                    dowod=f"fullname={outcome.fullname} permalink={outcome.permalink}",
                    szczegoly={"raw": dict(outcome.raw)},
                )
            )
        except Exception as exc:
            result.add(
                CheckResult(
                    nazwa="aktuator_www_wpisuje_klika_i_odczytuje_dowod",
                    wymog_z_dokumentu=(
                        "plan przeniesienia na www: dowodem jest `shreddit-comment` naszego "
                        "konta, którego przed wysłaniem nie było"
                    ),
                    zdane=False,
                    dowod=f"{type(exc).__name__}: {exc}"[:400],
                )
            )

        try:
            actuator.comment_www(
                thread_url=f"{www}?account={STRANGER}",
                parent_fullname=f"t3_{POST_ID}",
                text=TEXT,
                account=ACCOUNT,
                timeout_s=30.0,
            )
            verdict, evidence = False, "aktuator www zapisał mimo obcego konta na stronie"
        except CommentBlocked as exc:
            verdict, evidence = True, f"CommentBlocked: {exc}"[:300]
        except Exception as exc:
            verdict, evidence = False, f"{type(exc).__name__}: {exc}"[:300]
        result.add(
            CheckResult(
                nazwa="obce_konto_blokuje_komentarz_www",
                wymog_z_dokumentu="D-028: konto na stronie = konto zgody, inaczej BLOCKED",
                zdane=verdict,
                dowod=evidence,
            )
        )

        try:
            actuator.comment_www(
                thread_url=f"{www}?account={ACCOUNT}&locked=1",
                parent_fullname=f"t3_{POST_ID}",
                text=TEXT,
                account=ACCOUNT,
                timeout_s=15.0,
            )
            verdict, evidence = False, "aktuator www zapisał w wątku bez kompozytora"
        except CommentRefused as exc:
            verdict, evidence = True, f"CommentRefused: {exc}"[:300]
        except Exception as exc:
            verdict, evidence = False, f"{type(exc).__name__}: {exc}"[:300]
        result.add(
            CheckResult(
                nazwa="brak_kompozytora_www_odmawia",
                wymog_z_dokumentu="D-028: brak formularza = odmowa przed wysłaniem",
                zdane=verdict,
                dowod=evidence,
            )
        )

        run.zakonczony = _now()
        return result
    except Exception as exc:
        run.szczegol = f"{type(exc).__name__}: {exc}"[:400]
        return result
    finally:
        for label, step in (
            ("serwer stub", stub.shutdown if stub is not None else None),
            ("supervisor.stop", supervisor.stop),
        ):
            if step is None:
                continue
            try:
                step()
            except Exception as exc:
                run.szczegol = f"{run.szczegol}; sprzątanie {label}: {type(exc).__name__}: {exc}"
        _write_artifact(artifacts_dir, result, run)
