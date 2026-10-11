"""Reddit through the official Data API (OAuth2, *script* app) — behind the same port as HN.

Why the official API and not the browser (`docs/OUTCOMES.md` §6 point 1, D-023): on 2026-09-03
`https://www.reddit.com/r/<sub>/new.json` answered 403 from the development machine to a
non-browser client, and the roadmap's rule already prefers the cheaper, contract-bearing path
where one exists (3.1 point 42). The Data API's free tier allows 100 requests per minute per
OAuth client, which is two orders of magnitude more than observing one subreddit needs.

Why `AccessMode.ACCOUNT_REQUIRED` although this is an API: a *script* app authenticates AS the
founder's Reddit account (password grant), so every read here is a read by that account —
which is precisely the founders' decision "odczyt jest funkcją połączonego konta"
(`STAN-PROJEKTU.md` point 4), just without a browser.

WHAT HAPPENS WITHOUT CREDENTIALS, because this is the one place the temptation to return `[]`
is strongest: `CredentialsMissing`, on every call, with the names of the variables. Never an
empty tuple. The control plane parks the job in `WAITING_CAPABILITY` and the operator sees it.

NOT VERIFIED AGAINST THE LIVE API as of 2026-09-03 — no application was registered yet. The
payload shapes below are the documented ones (Listing → `data.children[]` → `data`), and
`tests/test_reddit.py` exercises them through `httpx.MockTransport`. The first live run is the
founder's, after `reddit.com/prefs/apps`; `tests/test_reddit_live.py` does not exist until then,
on purpose — a live test that skips for lack of credentials is a green light over nothing.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from community_os_contracts import AccessMode, ObservationPlatform

from .models import CommunityRef, ObservedThread, ThreadSnapshot
from .reddit_payload import (
    PERMALINK_BASE,
    parse_listing_threads,
    parse_thread_pair,
    require_post_id,
    subreddit_name,
)
from .source import MAX_LISTING, CredentialsMissing, SourceUnavailable, ThreadNotFound

__all__ = ["PERMALINK_BASE"]  # re-exported: the browser adapter and tests spell it from here too

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE_URL = "https://oauth.reddit.com"
DEFAULT_USER_AGENT = "wenext-community-observer/0.1 (read-only observation)"

#: Environment variable names, in one place, spelled the way `.env.example` documents them.
ENV_CLIENT_ID = "WENEXT_REDDIT_CLIENT_ID"
ENV_CLIENT_SECRET = "WENEXT_REDDIT_CLIENT_SECRET"
ENV_USERNAME = "WENEXT_REDDIT_USERNAME"
ENV_PASSWORD = "WENEXT_REDDIT_PASSWORD"
ENV_USER_AGENT = "WENEXT_REDDIT_USER_AGENT"
REQUIRED_ENV = (ENV_CLIENT_ID, ENV_CLIENT_SECRET, ENV_USERNAME, ENV_PASSWORD)

TIMEOUT = httpx.Timeout(20.0)

#: Refresh a token this long before Reddit says it expires — a request racing the expiry is a
#: 401 that looks like a revoked credential.
TOKEN_SAFETY_MARGIN = timedelta(seconds=60)

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class RedditCredentials:
    client_id: str
    client_secret: str
    username: str
    password: str
    user_agent: str = DEFAULT_USER_AGENT

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> RedditCredentials | None:
        """All four required variables present and non-empty → credentials; otherwise `None`.

        `None` and not a partial object: half a credential is not a credential, and the adapter
        must then say `CredentialsMissing` with the whole list rather than fail on the first
        missing field deep inside a token request.
        """
        if any(not env.get(name) for name in REQUIRED_ENV):
            return None
        return cls(
            client_id=env[ENV_CLIENT_ID],
            client_secret=env[ENV_CLIENT_SECRET],
            username=env[ENV_USERNAME],
            password=env[ENV_PASSWORD],
            user_agent=env.get(ENV_USER_AGENT) or DEFAULT_USER_AGENT,
        )


class RedditSource:
    """`CommunitySource` for Reddit. Official API, the founder's account, read-only."""

    platform = ObservationPlatform.REDDIT
    access_mode = AccessMode.ACCOUNT_REQUIRED

    def __init__(
        self,
        credentials: RedditCredentials | None,
        client: httpx.AsyncClient | None = None,
        *,
        token_url: str = TOKEN_URL,
        api_base_url: str = API_BASE_URL,
        clock: Clock = _utc_now,
    ) -> None:
        self._credentials = credentials
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT)
        self._token_url = token_url
        self._api_base_url = api_base_url.rstrip("/")
        self._clock = clock
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def configured(self) -> bool:
        return self._credentials is not None

    # -- port -----------------------------------------------------------------

    def describe(self, community_key: str) -> CommunityRef:
        name = subreddit_name(community_key)
        return CommunityRef(
            platform=self.platform,
            key=f"r/{name}",
            display_name=f"Reddit · r/{name}",
            access_mode=self.access_mode,
        )

    async def list_recent_threads(
        self, community_key: str, *, since: datetime | None, limit: int = MAX_LISTING
    ) -> tuple[ObservedThread, ...]:
        name = subreddit_name(community_key)
        if not 1 <= limit <= MAX_LISTING:
            raise ValueError(f"limit={limit} poza zakresem 1..{MAX_LISTING}")
        if since is not None and since.tzinfo is None:
            raise ValueError("since musi mieć strefę czasową")
        payload = await self._get_json(
            f"/r/{name}/new", params={"limit": str(limit), "raw_json": "1"}
        )
        # Parsing is shared with the browser adapter (`reddit_payload.py`, D-025): same payload
        # shape on both transports, so the evidence a thread carries cannot depend on the road.
        return parse_listing_threads(
            payload,
            community_key=f"r/{name}",
            observed_at=self._clock(),
            since=since,
            where=f"/r/{name}/new",
        )

    async def fetch_thread(self, community_key: str, external_id: str) -> ThreadSnapshot:
        name = subreddit_name(community_key)
        require_post_id(external_id)
        payload = await self._get_json(
            f"/r/{name}/comments/{external_id}",
            params={"raw_json": "1", "limit": "500", "depth": "10", "sort": "old"},
            not_found=external_id,
        )
        return parse_thread_pair(
            payload,
            community_key=f"r/{name}",
            external_id=external_id,
            observed_at=self._clock(),
            where=f"/comments/{external_id}",
        )

    # -- helpers ----------------------------------------------------------------

    async def _bearer(self) -> str:
        if self._credentials is None:
            raise CredentialsMissing(
                "Reddit wymaga aplikacji OAuth typu script i konta: ustaw "
                + ", ".join(REQUIRED_ENV)
                + " (rejestracja: https://www.reddit.com/prefs/apps). Bez tego adapter nie czyta "
                "— i nie udaje, że community jest puste."
            )
        now = self._clock()
        if (
            self._token is not None
            and self._token_expires_at is not None
            and now < self._token_expires_at - TOKEN_SAFETY_MARGIN
        ):
            return self._token
        try:
            response = await self._client.post(
                self._token_url,
                data={
                    "grant_type": "password",
                    "username": self._credentials.username,
                    "password": self._credentials.password,
                },
                auth=(self._credentials.client_id, self._credentials.client_secret),
                headers={"User-Agent": self._credentials.user_agent},
            )
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"Reddit: {type(exc).__name__} przy wymianie tokenu") from exc
        if response.status_code in (401, 403):
            raise CredentialsMissing(
                f"Reddit odrzucił poświadczenia ({response.status_code}). Sprawdź client_id/secret "
                "i konto; ten błąd nie minie sam"
            )
        if response.status_code != 200:
            raise SourceUnavailable(f"Reddit: wymiana tokenu odpowiedziała {response.status_code}")
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise SourceUnavailable("Reddit: odpowiedź tokenu nie jest JSON-em") from exc
        token = body.get("access_token") if isinstance(body, Mapping) else None
        if not isinstance(token, str) or not token:
            error = body.get("error") if isinstance(body, Mapping) else body
            raise CredentialsMissing(f"Reddit nie wydał tokenu: {error!r}")
        expires_in = body.get("expires_in") if isinstance(body, Mapping) else None
        seconds = int(expires_in) if isinstance(expires_in, (int, float)) else 3600
        self._token = token
        self._token_expires_at = now + timedelta(seconds=seconds)
        return token

    async def _get_json(
        self, path: str, *, params: Mapping[str, str], not_found: str | None = None
    ) -> Any:
        token = await self._bearer()
        assert self._credentials is not None
        url = f"{self._api_base_url}{path}"
        try:
            response = await self._client.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": self._credentials.user_agent,
                },
            )
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"Reddit: {type(exc).__name__} przy {url}: {exc}") from exc
        if response.status_code == 404 and not_found is not None:
            raise ThreadNotFound(f"Reddit nie zna posta {not_found}")
        if response.status_code == 401:
            # The token died early. Forget it; the next call re-authenticates, and if THAT fails
            # `CredentialsMissing` says so.
            self._token = None
            raise SourceUnavailable("Reddit: token odrzucony (401) — zostanie odnowiony")
        if response.status_code == 429 or response.status_code >= 500:
            raise SourceUnavailable(f"Reddit odpowiedział {response.status_code} na {url}")
        if response.status_code != 200:
            raise SourceUnavailable(
                f"Reddit odpowiedział {response.status_code} na {url}: {response.text[:200]}"
            )
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise SourceUnavailable(f"Reddit: odpowiedź na {url} nie jest JSON-em") from exc
