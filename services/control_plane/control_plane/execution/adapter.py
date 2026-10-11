"""The ONLY path from consented content to an outbound write (L5217, L5334).

    "żaden zapisujący side effect nie omija `ExecutionIntent`" (L5217)
    "akcja bez ważnego approvala jest niewykonalna także przy ręcznym wywołaniu adaptera" (L5334)

THE PROOF IS IN THE SIGNATURE, NOT IN A COMMENT. `perform` takes `authorization:
SideEffectAuthorization` as a keyword argument with NO DEFAULT, so a call that does not carry a
proof does not run: the interpreter raises `TypeError` at the call itself, before the first line
of the body, and a type checker reports it without running anything. That is the difference this
package exists to make — a rule expressed as a parameter cannot be forgotten the way a rule
expressed as "remember to call the guard first" is forgotten. In `tests/test_side_effect_guard.py`,
`test_the_authorization_is_a_required_argument_of_the_only_write_path` pins the shape and
`test_a_manual_adapter_call_without_an_approval_is_refused` shows what happens to the caller who
forces `None` through anyway.

WHAT `perform` DOES NOT TAKE, and why each absence is a guarantee:

  * no `organization_id`, no `workspace_id` — both are read off `content`, where they are two of
    the fourteen inputs of the digest the operator consented to (L2778-2793). A run cannot be
    filed under a tenant the consent does not name.
  * no `payload_hash`, no `intent_id`, no `revision_id` — those come off the proof, through
    `AutomationRun.from_authorization`.
  * no `adapter_version` — read off `content` for the same reason as the tenant. Whether the port
    on the other side really IS that version is not checked anywhere: it needs an adapter registry
    and is E1.F5 (L5323-5329). Stated, not silently assumed.

THE ORDER OF THE FOUR STEPS IS THE MECHANISM, so it is written down here and measured in tests:

  1.  read the database clock, then refuse or continue (`guard.require_authorization`);
  2.  write the attempt row as `PREPARED` — after the refusal, so a refused call leaves nothing
      behind, and before the port, so a write that happens always has a row that says it was
      attempted;
  3.  move `PREPARED → DISPATCHED → SUBMIT_STARTED` BEFORE handing the content to the port. This
      is the one ordering that matters after a crash: a process that dies before the port call
      leaves a row that permits another attempt, and a process that dies DURING it leaves
      `SUBMIT_STARTED`, out of which the machine draws no automatic retry (L2673, L4560, L5335) —
      because the side effect may already exist on the platform side;
  4.  finish the attempt: `CONFIRMED` when the port returns, `UNKNOWN` when it raises.

WHY A RAISING PORT MEANS `UNKNOWN` AND NOT `FAILED_BEFORE_SIDE_EFFECT`. An exception out of a
network call does not say whether the platform accepted the request; the connection can drop after
the write. `UNKNOWN` is the pessimistic reading and it is the one the document takes: it consumes
the budget and creates an alert, not another attempt (L5337). The consequence, stated rather than
hidden: **this adapter has no path to `FAILED_BEFORE_SIDE_EFFECT`, `CAPABILITY_BLOCKED` or
`IDENTITY_BLOCKED`.** Those three need a capability probe and an identity check, which are E1.F2
and E1.F5; in F1 every failure is `UNKNOWN`.

WHAT THIS FILE IS NOT. It is not the Action Adapter of E1.F5 (L5323-5329). There is no
platform-specific code here, no request building, no response parsing, no retry policy, and
**F1 ships ZERO implementations of `WritingSideEffectPort`** — the first one is capability probing
in E1.F2, and it will find this gate already standing. That last sentence is a reading of the
tree, not an enforced invariant: no test counts the implementations of this protocol. What IS
measured is the neighbouring fact, in `tests/test_no_bypass_static.py` — no call site anywhere in
`services/` looks like an outbound write except the one dispatch below.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from community_os_contracts.enums import ExecutionAttemptState
from community_os_contracts.execution import (
    ApprovalHashInput,
    AutomationRun,
    SideEffectAuthorization,
)

from control_plane.execution.guard import require_authorization
from control_plane.execution.store import ExecutionStore
from control_plane.pilot.publication import MediaRef, split_media, split_own_post

#: Every attempt this adapter records is the first and last one of its intent. Not a policy choice
#: made here: `UNIQUE (intent_id)` on `execution_attempts` (migration 0003) refuses the second one,
#: and L2821 is the sentence that index carries. A retry needs a NEW intent, which needs a new
#: consent — which is the whole point of a single-use grant.
SINGLE_ATTEMPT_PER_INTENT = 1


@dataclass(frozen=True)
class SideEffectEvidence:
    """What the platform showed after the write: the identifier of the thing that now exists there,
    where to see it, under which account, and when it was seen. Written by the port from what it
    READ BACK, never from what it sent (R4: "dowód = odczyt opublikowanego komentarza")."""

    external_id: str
    permalink: str
    account: str
    observed_at: datetime
    raw: Mapping[str, Any] = field(default_factory=dict)


#: Where `perform` hands the evidence of a CONFIRMED attempt. The adapter does not know the table;
#: the caller that owns the request row does (`pilot.publisher`).
EvidenceSink = Callable[[AutomationRun, SideEffectEvidence], Awaitable[None]]


class WritingSideEffectPort(Protocol):
    """The outbound half of a writing action.

    The port receives the consented content OBJECT, not a payload assembled next to it. There is
    therefore nothing between the digest the guard checked and the value handed outwards: to write
    different text a caller would have to build a different `ApprovalHashInput`, whose digest no
    longer matches the proof.

    `submit` returns the evidence the platform gave back, or `None` for a port that knows only that
    the platform accepted (the F1 test doubles). A port that knows anything else raises.
    """

    async def submit(self, content: ApprovalHashInput) -> SideEffectEvidence | None:
        """Perform the write on the external platform. Return on success, raise otherwise."""
        ...


class UnknownSideEffectOutcome(Exception):
    """The port raised, so nobody knows whether the platform wrote anything (L5337).

    Carries the `UNKNOWN` run, because the caller needs the attempt identifier to alert on and
    must not be able to mistake this for a failure that is safe to retry. `may_retry()` on that
    run is `False`, and it is `False` because the state machine says so, not because this class
    says so.
    """

    def __init__(self, run: AutomationRun) -> None:
        self.run = run
        super().__init__(
            f"Próba wykonania {run.execution_attempt_id} zakończyła się stanem "
            f"{run.state.value}: port rzucił wyjątkiem w trakcie submitu, więc NIE WIADOMO, czy "
            "platforma zapisała akcję. Brak automatycznego ponowienia (L2673, L5335); to jest "
            "przypadek na alert dla człowieka (L5337)"
        )


class SideEffectAdapter:
    """One store, one port, one method. The narrowest surface this boundary can have."""

    def __init__(
        self,
        store: ExecutionStore,
        port: WritingSideEffectPort,
        evidence: EvidenceSink | None = None,
    ) -> None:
        self._store = store
        self._port = port
        self._evidence = evidence

    async def perform(
        self, content: ApprovalHashInput, *, authorization: SideEffectAuthorization
    ) -> AutomationRun:
        """Write `content` on the outside, or refuse. Returns the `CONFIRMED` run.

        Raises `guard.SideEffectRefused` when the proof is missing, forged, for other content or
        past its deadline; `store.AttemptAlreadyRecorded` when this intent already has an attempt
        (L2821); `UnknownSideEffectOutcome` when the port raises.

        The refusal happens BEFORE anything is written and before the port is touched — measured
        by `test_a_manual_adapter_call_without_an_approval_is_refused`, which counts the port's
        invocations and reads the table back.
        """
        now = await self._store.database_now()
        proof = require_authorization(content=content, authorization=authorization, now=now)

        run = await self._store.record_attempt(
            authorization=proof,
            organization_id=content.organization,
            workspace_id=content.workspace,
            attempt_no=SINGLE_ATTEMPT_PER_INTENT,
            adapter_version=content.adapter_version,
        )
        run = await self._store.move_attempt(run, target=ExecutionAttemptState.DISPATCHED)
        run = await self._store.move_attempt(run, target=ExecutionAttemptState.SUBMIT_STARTED)

        try:
            evidence = await self._port.submit(content)
        except Exception as failure:
            unknown = await self._store.move_attempt(run, target=ExecutionAttemptState.UNKNOWN)
            raise UnknownSideEffectOutcome(unknown) from failure

        confirmed = await self._store.move_attempt(run, target=ExecutionAttemptState.CONFIRMED)
        if evidence is not None and self._evidence is not None:
            await self._evidence(confirmed, evidence)
        return confirmed


# -- the first implementation of the port (R4 minimal, D-028) --------------------------------------

#: The capsule's actuator endpoint and its token header, spelled here and in the two capsule files
#: that serve it, and nowhere else: `bypass_scan.PERMITTED_ACTUATOR_ADDRESSES` fails the build on a
#: third file that knows this address.
ACT_COMMENT_PATH = "/act/comment"
#: Własny post (0023). Osobna ścieżka, bo to osobny rodzaj zapisu, i osobna klasa portu niżej.
ACT_POST_PATH = "/act/post"
#: X (26.09): odpowiedź pod postem i własny post, dwa zapisy aktuatora `x_writer` w kapsule.
ACT_X_REPLY_PATH = "/act/x/reply"
ACT_X_POST_PATH = "/act/x/post"
WRITER_TOKEN_HEADER = "X-Wenext-Writer-Token"
ENV_WRITER_TOKEN = "WENEXT_BROWSER_WRITER_TOKEN"
#: Compose service names the port may address (D-027): the capsules, and nothing else. Since
#: D-051 the X account writes through its own capsule; a gateway is never a writer.
ACTUATOR_HOSTS: frozenset[str] = frozenset({"capsule", "capsule-x"})
ACT_TIMEOUT_S = 75.0


class PublicationRefused(Exception):
    """The capsule refused before touching the page (state, account, form). Nothing submitted."""

    submit_started: bool | None = False


class PublicationBlocked(Exception):
    """The platform said no in a way that will not pass on retry. The queue must stop.
    `submit_started` says whether the save button had been clicked before the refusal."""

    def __init__(self, message: str, *, submit_started: bool | None = None) -> None:
        super().__init__(message)
        self.submit_started = submit_started


class PublicationUnknown(Exception):
    """Nothing could be read back. `submit_started` is the actuator's own account of whether the
    save button was clicked: `False` only when the capsule SAID so, `None` when nobody knows (a
    timeout, a cut connection, a crash outside the actuator). A requeue may trust only `False`."""

    def __init__(self, message: str, *, submit_started: bool | None = None) -> None:
        super().__init__(message)
        self.submit_started = submit_started


def _submit_started(response: httpx.Response) -> bool | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    value = payload.get("submit_started") if isinstance(payload, Mapping) else None
    return value if isinstance(value, bool) else None


class RedditCapsuleCommentPort:
    """`WritingSideEffectPort` for one Reddit comment through the capsule's actuator.

    Receives the consented `ApprovalHashInput` and hands EXACTLY its fields to the capsule: the
    target's fullname (`target_external_id`), the thread (`target_url`), the text
    (`text_and_media`) and the expected account (`expected_account`, resolved by the caller from
    the account row the digest names). No request building beyond that, no retry policy: the
    outcome classes above are the whole vocabulary, and each one means something different to the
    queue (`pilot.publisher`).
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        expected_account: str,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = ACT_TIMEOUT_S,
    ) -> None:
        host = urlsplit(base_url).hostname
        if urlsplit(base_url).scheme != "http" or host not in ACTUATOR_HOSTS:
            raise ValueError(
                f"port aktuatora przyjmuje tylko http://<{'|'.join(sorted(ACTUATOR_HOSTS))}>, "
                f"nie {base_url!r} (D-027)"
            )
        if not token:
            raise ValueError("port aktuatora bez tokenu nie powstaje")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._account = expected_account
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def submit(self, content: ApprovalHashInput) -> SideEffectEvidence:
        return await self._act(
            ACT_COMMENT_PATH,
            {
                "thread_url": content.target_url,
                "parent_fullname": content.target_external_id,
                "text": content.text_and_media,
                "account": self._account,
            },
            what="komentarz",
        )

    async def _act(self, path: str, body: Mapping[str, str], *, what: str) -> SideEffectEvidence:
        """Jedno wywołanie aktuatora i JEDNA mapa jego odpowiedzi na trzy wyniki.

        Oddzielone od `submit`, bo od 0023 są dwa rodzaje zapisu, a mapa odpowiedzi ma pozostać
        jedna: to ona decyduje, czy kolejka staje, ponawia, czy czeka na człowieka, i dwie jej
        kopie znaczyłyby dwa różne zdania o tym samym statusie HTTP.
        """
        url = f"{self._base_url}{path}"
        try:
            response = await self._client.post(
                url, json=dict(body), headers={WRITER_TOKEN_HEADER: self._token}
            )
        except httpx.TimeoutException as exc:
            raise PublicationUnknown(
                f"aktuator kapsuły nie odpowiedział w czasie ({type(exc).__name__}); {what} "
                "mógł zostać zapisany",
                submit_started=None,
            ) from exc
        except httpx.HTTPError as exc:
            raise PublicationUnknown(
                f"połączenie z aktuatorem kapsuły przerwane ({type(exc).__name__}); skutek "
                "nieznany",
                submit_started=None,
            ) from exc
        detail = _detail(response)
        started = _submit_started(response)
        if response.status_code == 200:
            payload = response.json()
            observed = payload.get("observed_at")
            return SideEffectEvidence(
                # Reddit answers `fullname`; the X actuator answers `post_id`, which
                # `XCapsuleReplyPort._act_x` turns into `x:<id>`.
                external_id=str(payload.get("fullname") or payload.get("post_id") or ""),
                permalink=str(payload["permalink"]),
                account=str(payload.get("account") or self._account),
                observed_at=(
                    datetime.fromisoformat(observed)
                    if isinstance(observed, str)
                    else datetime.now(UTC)
                ),
                raw=dict(payload),
            )
        if response.status_code in (401, 404, 409, 400):
            raise PublicationRefused(f"aktuator odmówił ({response.status_code}): {detail}")
        if response.status_code == 423:
            raise PublicationBlocked(
                f"platforma zablokowała publikację: {detail}", submit_started=started
            )
        raise PublicationUnknown(
            f"aktuator odpowiedział {response.status_code}: {detail}", submit_started=started
        )


class RedditCapsuleOwnPostPort(RedditCapsuleCommentPort):
    """`WritingSideEffectPort` dla jednego WŁASNEGO WĄTKU przez aktuator kapsuły (0023).

    Dziedziczy po porcie komentarza wszystko poza jednym: co wysyła. Reszta, czyli sprawdzenie
    adresu kapsuły, token, mapa odpowiedzi na trzy wyniki i to, że port nie ponawia, jest ta sama
    i ma taka zostać; dwie kopie tej mapy rozjechałyby się przy pierwszej zmianie statusu.

    SKĄD TYTUŁ. Tytuł i treść jadą w jednym polu `text_and_media`, bo to pole wchodzi do odcisku
    zgody (`join_own_post`); tutaj rozdziela je `split_own_post`, ta sama zasada z drugiej strony.
    Platforma dostaje dokładnie to, co człowiek zatwierdził, rozłożone na dwa pola formularza.

    SKĄD SPOŁECZNOŚĆ. Z `target_external_id` zatwierdzonej treści: przy własnym poście celem nie
    jest wątek (jeszcze go nie ma), tylko społeczność, i tak zapisuje ją `draft.create`.
    """

    async def submit(self, content: ApprovalHashInput) -> SideEffectEvidence:
        title, body_text = split_own_post(content.text_and_media)
        return await self._act(
            ACT_POST_PATH,
            {
                "community": content.target_external_id,
                "title": title,
                "text": body_text,
                "account": self._account,
            },
            what="wątek",
        )


class XCapsuleReplyPort(RedditCapsuleCommentPort):
    """`WritingSideEffectPort` for one reply on X through the capsule's actuator (26.09).

    Inherits everything but what it sends and how it reads the answer: the capsule address
    check, the token, the three outcome classes and the refusal to retry are the same, and two
    copies of that map would drift apart on the first status code that changes.

    WHAT IT SENDS is exactly the consented content: the post address (`target_url`), the post id
    taken from the consented target (`x:<id>`), the text and the expected account. The capsule
    checks that the address and the id name the same post and that the browser is logged into
    that account before it types anything.
    """

    async def submit(self, content: ApprovalHashInput) -> SideEffectEvidence:
        post_id = str(content.target_external_id).removeprefix("x:")
        text, media = split_media(content.text_and_media)
        if media is not None:
            # `draft.create` refuses an image on a reply; this is the same rule at the last door.
            raise PublicationRefused(
                "obraz jest dozwolony tylko pod własnym postem; nic nie wysłano"
            )
        return await self._act_x(
            ACT_X_REPLY_PATH,
            {
                "post_url": content.target_url,
                "post_id": post_id,
                "text": text,
                "account": self._account,
            },
            what="odpowiedź na X",
        )

    async def _act_x(self, path: str, body: Mapping[str, str], *, what: str) -> SideEffectEvidence:
        """The capsule's X answer mapped to evidence. The outcome map is the parent's.

        The capsule answers `post_id` and `permalink` READ FROM THE PAGE after the click (toast
        link or the new post under our account), never echoed from the request. Stored as
        `x:<id>` so an X publication can never be mistaken for a Reddit fullname.
        """
        evidence = await self._act(path, body, what=what)
        raw = dict(evidence.raw)
        post_id = str(raw.get("post_id") or "")
        if not post_id.isdigit():
            raise PublicationUnknown(
                f"kapsuła nie podała identyfikatora posta X ({what}); skutek nieznany, "
                "nie publikuj ponownie",
                submit_started=True,
            )
        return SideEffectEvidence(
            external_id=f"x:{post_id}",
            permalink=str(raw.get("permalink") or evidence.permalink),
            account=evidence.account,
            observed_at=evidence.observed_at,
            raw=raw,
        )


#: Reads the bytes of the image a consent names: `(bytes, content type)`, or `PublicationRefused`
#: when they cannot be read or do not match the consented checksum (`observe/wiring.py`).
MediaLoader = Callable[[MediaRef], Awaitable[tuple[bytes, str]]]


class XCapsuleOwnPostPort(XCapsuleReplyPort):
    """`WritingSideEffectPort` for one own post on X: the account's timeline, no title.

    With an image (27.09) the consented field ends in the media line (`join_media`): the text
    typed on X is the field WITHOUT that line, and the image is the artifact it names, read by
    `media_loader` and checked against the consented checksum before anything is sent.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        expected_account: str,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = ACT_TIMEOUT_S,
        media_loader: MediaLoader | None = None,
    ) -> None:
        super().__init__(
            base_url,
            token=token,
            expected_account=expected_account,
            client=client,
            timeout_s=timeout_s,
        )
        self._media_loader = media_loader

    async def submit(self, content: ApprovalHashInput) -> SideEffectEvidence:
        text, media = split_media(content.text_and_media)
        body = {"text": text, "account": self._account}
        if media is not None:
            if self._media_loader is None:
                raise PublicationRefused(
                    "zgoda obejmuje obraz, a ten port nie umie go odczytać; nic nie wysłano"
                )
            image, image_type = await self._media_loader(media)
            body["image_b64"] = base64.b64encode(image).decode("ascii")
            body["image_type"] = image_type
        return await self._act_x(
            ACT_X_POST_PATH, body, what="post na X z obrazem" if media else "post na X"
        )


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(payload, Mapping):
        return str(payload.get("detail") or payload.get("error") or payload)[:300]
    return str(payload)[:300]
