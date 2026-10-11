"""A static answer to "does anything write outwards without passing through the adapter?" (L5217).

READ THIS BEFORE READING THE RESULT. In phase E1.F1 this repository contains **no path that
writes to an external platform**: the Action Adapter is E1.F5 (L5323-5329), capability probing and
the headless capsule are E1.F2 (L5220-5240). So the honest reading of a clean scan today is
"there is nothing to bypass", and NOT "nobody bypasses". A test that reported the second sentence
would be `all([])` raised to the level of a phase — the ninth false pass of this project.

What the scan can therefore prove today is narrower and worth stating exactly:

  * the instrument reads a non-empty set of production files (it reports how many);
  * its vocabulary matches real code — it finds the one dispatch inside `adapter.py`, and a test
    plants a synthetic bypass and watches the scan name it;
  * the number of writing call sites outside the permitted point is a NUMBER, reported on every
    run. Today it is zero because the numerator is empty. When E1.F2 makes it non-zero, this test
    starts meaning what its name says.

WHAT COUNTS AS A WRITING CALL SITE. Two vocabularies, both closed and both listed below, because
a scanner whose rule lives in the reader's head cannot be argued with:

  * a CALL of a method named like an outbound write — `post`, `put`, `patch`, `delete` (HTTP),
    `click`, `fill`, `press`, `select_option`, `set_input_files` (browser input), `submit`
    (this package's own port);
  * a STRING CONSTANT naming a Chrome DevTools Protocol method that changes something outside the
    process — the `Input.` domain, `Runtime.evaluate`, `Runtime.callFunctionOn`.

NAVIGATION IS NOT A WRITE, and this line is here because the first version of the vocabulary said
otherwise. With `goto` and `Page.navigate` in it, the scan reported two sites — `page.goto(
"about:blank")` in `browser_supervisor/contract.py`, which exists to make the tracing buffer
flush, and a navigation to the LOCAL fixture site in `gate2.py`, immediately followed by reading
text off the page. Both are reads. Keeping them would have bought a scanner that cries wolf, and a
scanner that cries wolf gets an exclusion list, and an exclusion list is where a real bypass goes
to hide. L5217 is about changing state on an external platform, so the vocabulary is what changes
state: clicking, filling, pressing, uploading, and the four HTTP verbs. The two sites above are
named here rather than excluded silently, so that a reader who disagrees knows exactly what to
argue with.

FOUR THINGS THE SCAN DOES NOT SEE, listed rather than left to be discovered (the D-019 pattern):

  1.  **Decorator positions are skipped.** `@router.post("/session")` is a call of a method named
      `post` and it registers an INBOUND route; counting it would make every FastAPI application
      a bypass. The blind spot is real: a decorator that performed a write at import time would be
      invisible here.
  2.  **Only Python.** `apps/dashboard/` is TypeScript, and a Next.js server route that posted to
      a platform would not be seen by this file at all.
  3.  **Local writes are not side effects here.** `Path.write_text`, `unlink`, an `INSERT` into
      our own database — none of them is a write on an external platform, and L5217 is about the
      external one.
  4.  **A name this vocabulary does not know is invisible.** `send`, `execute`, `run`, an HTTP
      verb spelled through `request("POST", …)` — deliberately out, because each of them occurs
      dozens of times in this repository meaning something else, and a scanner that cries wolf
      gets an exclusion list, and an exclusion list is where a real bypass goes to hide. Widen the
      vocabulary when a real writing path arrives with a new spelling; do NOT widen it with
      guesses.
"""

from __future__ import annotations

import ast
import pathlib
import re
from dataclasses import dataclass

#: The one file in this repository that is allowed to hold a writing call site. Repo-relative and
#: written out in full: a pattern would also match a file somebody adds next to it.
PERMITTED_WRITE_POINT = "services/control_plane/control_plane/execution/adapter.py"

#: Files whose ONLY outbound `post` is an OAuth token exchange — a request for permission to
#: READ, not a write to a community. Named in full, one per line, each with the reason, because
#: the module docstring says what an exclusion list becomes when it is quiet: the place a real
#: bypass hides. The test that uses this list asserts the exact number of such sites and that each
#: sits in a file named here, so adding a `.post()` to one of these files is still caught.
#:
#:   * `community_observer/reddit.py` — `POST https://www.reddit.com/api/v1/access_token`, the
#:     password-grant exchange of the *script* app (D-023). It changes nothing on any subreddit;
#:     every write to Reddit would be a `.post()` against `oauth.reddit.com`, and there is none.
PERMITTED_AUTH_EXCHANGES: frozenset[str] = frozenset(
    {"services/community_observer/community_observer/reddit.py"}
)

# A public search MCP transports its read with POST. Unlike a platform actuator this
# cannot publish. Only this literal address and one call site qualify; runtime tests
# also pin the method and remote tool name. Changed/dynamic targets remain violations.
PERMITTED_READ_MCP = {
    "services/control_plane/control_plane/knowledge/newsletter.py": "https://unknow.news/mcp/",
}
READ_MCP_CALL = "read-only MCP search POST"

#: Files whose `.post()` calls go to OUR OWN containers, never to a platform (D-026, plan B
#: level 2), with the exact number of such call sites each file may hold. Named one by one,
#: counted exactly, for the same reason as the list above: this is where a real bypass would try
#: to hide, so the test asserts the number, not the presence.
#:
#:   * `community_observer/reddit_browser.py` — `POST /mode` on the capsule's reader: asks the
#:     capsule for headful rendering (`display_only`) because Reddit refuses headless. Changes the
#:     shape of OUR Chrome process; changes nothing on Reddit. One call site.
#:   * `control_plane/observe/capsule_client.py` — `POST /mode` on the capsule (open a human's
#:     screen / close it again), `POST /browser-sessions` on the Stream Gateway (mint a one-time
#:     ticket for that human) and `POST /stream/revoke` on the gateway (cut every open stream when
#:     the human says "done"). All address our containers; what the human then does at the screen
#:     is the human's action, not the agent's. Three call sites.
#:
#: Spelled as `.post()` ON PURPOSE. `request("POST", …)` is outside this scanner's vocabulary
#: (blind spot 4 in the module docstring), and writing internal calls that way would have made them
#: invisible here — which is exactly the shape a bypass takes. Visible and counted is the rule.
PERMITTED_INTERNAL_CONTROL: dict[str, int] = {
    "services/community_observer/community_observer/reddit_browser.py": 1,
    "services/control_plane/control_plane/observe/capsule_client.py": 3,
}

#: The only hosts an internal-control `.post()` may address: the compose service names of OUR
#: containers (D-027). The allowance above is per FILE; this rule is per ADDRESS, and it is what
#: keeps a file on the list from growing a `.post()` to `oauth.reddit.com` that the per-file count
#: would happily accept as "the one permitted call". Two checks on every `.post()` in a named file:
#:
#:   * its URL argument must be rooted in `self._base_url` — an f-string that starts with it, or a
#:     name bound in the same function to such an f-string. A literal address, or an address from
#:     anywhere else, is a violation whatever host it names;
#:   * no string literal inside that argument may carry a host outside this set.
#:
#: The runtime half lives in the clients themselves (`CapsuleClient`, `GatewayClient`,
#: `HttpBrowserReader` refuse a base URL whose host is not on this list), because a scanner reads
#: source and an environment variable is not source. Publication on a platform goes through
#: `ExecutionIntent` and the adapter, never through these files.
INTERNAL_CONTROL_HOSTS: frozenset[str] = frozenset({"capsule", "gateway", "capsule-x", "gateway-x"})

#: The platform ACTUATOR (R4 minimal, D-028): the one file in the capsule that types into a page
#: and clicks, counted to the exact number of such sites. It is reachable only through the two
#: `POST /act/...` endpoints on the capsule's reader, behind a token of its own, and those are
#: addressed only from `PERMITTED_WRITE_POINT` (the two ports inside `adapter.py`, which run only
#: under `SideEffectAdapter.perform` with a consumed `ExecutionIntent`). Ten sites:
#: three for old-interface comments, three for own posts, four for www comments.
#:
#: The www path may load the editor bundle, open its collapsed editor, fill, then submit.
#: Opening was added after the 2026-09-23 live failure: attached markup is not an active
#: editor. All three still run behind the same execution grant; no alternate write path.
#: An additional unaccounted input operation must fail this scan.
#:
#: The X actuator (`x_writer.py`, 2026-09-26) holds FIVE sites, shared by its two writes (a reply
#: and an own post go through one `_compose_and_submit`): `fill` of the composer, then the
#: documented keyboard fallback for a DraftJS editor that did not register the insert (`press`
#: select-all, `press` Backspace, `press_sequentially` the consented text), and ONE `click` of
#: the composer's button. It is reachable only through `POST /act/x/reply` and `/act/x/post` on
#: the reader, behind the same writer token and the same `_act_allowed` gate as Reddit, called
#: only from the X ports in `PERMITTED_WRITE_POINT`.
#:
#: SIX since 2026-09-27: `set_input_files` of the composer's file input, the ONE image an own
#: post may carry (`_attach_image`). Same endpoint (`/act/x/post`), same token, same gate, and
#: the image is part of the consent: the digest covers the text AND the image's checksum, which
#: the publisher and the capsule check against the bytes before this site is reached.
PERMITTED_PLATFORM_ACTUATORS: dict[str, int] = {
    # Includes opening the collapsed Reddit editor before entering the approved body.
    "services/browser_supervisor/browser_supervisor/writer.py": 10,
    "services/browser_supervisor/browser_supervisor/x_writer.py": 6,
}

#: String constants that ADDRESS the actuator: the endpoint path and its token header. A file
#: that carries either one can call the actuator, so the set of files allowed to carry them is
#: closed: the adapter (the caller), and the two capsule files that serve it. Anywhere else the
#: constant is a violation, whatever the surrounding code does with it.
ACTUATOR_ADDRESS_MARKERS: frozenset[str] = frozenset(
    {"/act/comment", "/act/post", "/act/x/reply", "/act/x/post", "X-Wenext-Writer-Token"}
)
PERMITTED_ACTUATOR_ADDRESSES: frozenset[str] = frozenset(
    {
        PERMITTED_WRITE_POINT,
        "services/browser_supervisor/browser_supervisor/reader.py",
        "services/browser_supervisor/browser_supervisor/writer.py",
        # This file: it has to spell the markers to look for them. It holds no client.
        "services/control_plane/control_plane/execution/bypass_scan.py",
    }
)
ACTUATOR_ADDRESS = "adres aktuatora"

#: A URL literal, with its host in group 1. `[^/:?#]` stops before the port, so `http://x:8765/y`
#: yields `x`.
URL_LITERAL = re.compile(r"^https?://([^/:?#]+)")

#: The description every counted internal-control site must carry. Anything else found in a named
#: file — a foreign host, an address not rooted in `base_url` — is a violation, not an allowance.
INTERNAL_POST = "wywołanie .post()"

#: Method names whose CALL is treated as an outbound write. See the module docstring for what is
#: deliberately absent.
WRITING_METHOD_NAMES: frozenset[str] = frozenset(
    {
        "post",
        "put",
        "patch",
        "delete",
        "click",
        "fill",
        "press",
        "select_option",
        "set_input_files",
        "submit",
        # D-028: the rest of Playwright's ways to put input into a page, and the three ways to run
        # script inside it. Added when the first real actuator arrived, not as a guess.
        "type",
        "insert_text",
        "dispatch_event",
        "evaluate",
        "evaluate_all",
        "evaluate_handle",
        # 2026-09-26: Playwright's current name for typing key by key, arrived with the X
        # actuator's keyboard fallback. Unlisted, it would have been an uncounted write site.
        "press_sequentially",
    }
)

#: Files allowed to run script inside OUR OWN Chrome for a purpose that changes nothing on any
#: platform, with the exact count: `contract.py` opens `about:blank` in a new tab to flush the
#: tracing buffer (measured by the CDP contract check). Counted, not excluded: a second call there
#: is a violation.
PERMITTED_LOCAL_EVALUATIONS: dict[str, int] = {
    "services/browser_supervisor/browser_supervisor/contract.py": 1,
}
LOCAL_EVALUATION_WHAT = "wywołanie .evaluate()"

#: Names of the module constants that carry the actuator's address. A file that imports one of them
#: (`from ... import ACT_COMMENT_PATH`) knows the address without spelling it; the scan treats a
#: reference to these names exactly like the string constants in `ACTUATOR_ADDRESS_MARKERS`.
ACTUATOR_ADDRESS_NAMES: frozenset[str] = frozenset(
    {
        "ACT_COMMENT_PATH",
        "ACT_POST_PATH",
        "ACT_X_REPLY_PATH",
        "ACT_X_POST_PATH",
        "WRITER_TOKEN_HEADER",
    }
)

#: CDP methods that change something outside this process. `Browser.close`, `Target.*` and
#: `Page.navigate` are NOT here on purpose: they steer OUR capsule's lifecycle or move it between
#: pages, neither of which changes anything on a platform. That judgement is written down so it
#: can be disagreed with — `services/browser_supervisor/browser_supervisor/supervisor.py` contains
#: exactly one such call and it is the reason this sentence exists.
WRITING_CDP_METHOD = re.compile(r"^(Input\.[A-Za-z]+|Runtime\.(evaluate|callFunctionOn))$")

#: Directories no scan enters. `tests` is here because a test that drives a fake platform is not a
#: production write path — and because the suites of this repository are full of `client.post`.
IGNORED_DIRECTORY_NAMES: frozenset[str] = frozenset(
    {".git", ".venv", "__pycache__", "node_modules", ".next", ".pytest_cache", "tests"}
)


@dataclass(frozen=True)
class WriteCallSite:
    """One place that looks like an outbound write. `where` is repo-relative, never absolute."""

    where: str
    line: int
    what: str

    def __str__(self) -> str:
        return f"{self.where}:{self.line} — {self.what}"


@dataclass(frozen=True)
class ScanResult:
    """What one scan read and what it found. Both halves matter; see `files_read`."""

    #: Every file the scan actually parsed. Reported so a caller can assert the instrument had
    #: something to look at: a scan of zero files also finds zero violations.
    files_read: tuple[str, ...]
    sites: tuple[WriteCallSite, ...]

    @property
    def violations(self) -> tuple[WriteCallSite, ...]:
        """Writing call sites outside the permitted file, the named auth exchanges and the named
        internal-control files. A named file with MORE sites than its count is reported here too —
        the allowance is per site, not per file."""
        excess = self.internal_control_excess
        actuator_excess = self.actuator_excess
        local_excess = self.local_evaluation_excess
        read_mcp = [site for site in self.sites if site.what == READ_MCP_CALL]
        return tuple(
            site
            for site in self.sites
            if (
                site.what.startswith(ACTUATOR_ADDRESS)
                and site.where not in PERMITTED_ACTUATOR_ADDRESSES
            )
            or (
                not site.what.startswith(ACTUATOR_ADDRESS)
                and site.where != PERMITTED_WRITE_POINT
                and site.where not in PERMITTED_AUTH_EXCHANGES
                and not (site.what == READ_MCP_CALL and site in read_mcp[:1])
                and (
                    site.where not in PERMITTED_INTERNAL_CONTROL
                    or site in excess
                    or site.what != INTERNAL_POST
                )
                and (site.where not in PERMITTED_PLATFORM_ACTUATORS or site in actuator_excess)
                and (
                    site.where not in PERMITTED_LOCAL_EVALUATIONS
                    or site in local_excess
                    or site.what != LOCAL_EVALUATION_WHAT
                )
            )
        )

    @property
    def local_evaluations(self) -> tuple[WriteCallSite, ...]:
        """The counted `.evaluate()` sites in the files of `PERMITTED_LOCAL_EVALUATIONS`."""
        return tuple(
            site
            for site in self.sites
            if site.where in PERMITTED_LOCAL_EVALUATIONS and site.what == LOCAL_EVALUATION_WHAT
        )

    @property
    def local_evaluation_excess(self) -> tuple[WriteCallSite, ...]:
        excess: list[WriteCallSite] = []
        for where, allowed in PERMITTED_LOCAL_EVALUATIONS.items():
            sites = [site for site in self.local_evaluations if site.where == where]
            excess.extend(sites[allowed:])
        return tuple(excess)

    @property
    def actuators(self) -> tuple[WriteCallSite, ...]:
        """The counted input sites inside the files named in `PERMITTED_PLATFORM_ACTUATORS`
        (D-028). Reported, not hidden; the test asserts the exact number per file."""
        return tuple(
            site
            for site in self.sites
            if site.where in PERMITTED_PLATFORM_ACTUATORS
            and not site.what.startswith(ACTUATOR_ADDRESS)
        )

    @property
    def actuator_excess(self) -> tuple[WriteCallSite, ...]:
        """Sites in an actuator file BEYOND its permitted count — violations."""
        excess: list[WriteCallSite] = []
        for where, allowed in PERMITTED_PLATFORM_ACTUATORS.items():
            sites = [site for site in self.actuators if site.where == where]
            excess.extend(sites[allowed:])
        return tuple(excess)

    @property
    def actuator_addresses(self) -> tuple[WriteCallSite, ...]:
        """Every place a string constant names the actuator's endpoint or its token header. Only
        the files in `PERMITTED_ACTUATOR_ADDRESSES` may hold one; the rest are in `violations`."""
        return tuple(site for site in self.sites if site.what.startswith(ACTUATOR_ADDRESS))

    @property
    def auth_exchanges(self) -> tuple[WriteCallSite, ...]:
        """Call sites inside the files named in `PERMITTED_AUTH_EXCHANGES`. Reported, not hidden."""
        return tuple(site for site in self.sites if site.where in PERMITTED_AUTH_EXCHANGES)

    @property
    def internal_control(self) -> tuple[WriteCallSite, ...]:
        """The counted `.post()` sites inside the files named in `PERMITTED_INTERNAL_CONTROL`
        (D-026). A foreign address found in the same file is NOT here — see `foreign_targets`."""
        return tuple(
            site
            for site in self.sites
            if site.where in PERMITTED_INTERNAL_CONTROL and site.what == INTERNAL_POST
        )

    @property
    def foreign_targets(self) -> tuple[WriteCallSite, ...]:
        """`.post()` calls in a named internal-control file whose address is not rooted in
        `self._base_url` or names a host outside `INTERNAL_CONTROL_HOSTS` (D-027). Violations."""
        return tuple(
            site
            for site in self.sites
            if site.where in PERMITTED_INTERNAL_CONTROL and site.what != INTERNAL_POST
        )

    @property
    def internal_control_excess(self) -> tuple[WriteCallSite, ...]:
        """Sites in a named internal-control file BEYOND its permitted count — violations."""
        excess: list[WriteCallSite] = []
        for where, allowed in PERMITTED_INTERNAL_CONTROL.items():
            sites = [site for site in self.internal_control if site.where == where]
            excess.extend(sites[allowed:])
        return tuple(excess)

    @property
    def permitted(self) -> tuple[WriteCallSite, ...]:
        """Writing call sites inside `adapter.py`, i.e. the ones that are supposed to be there."""
        return tuple(site for site in self.sites if site.where == PERMITTED_WRITE_POINT)


def scan_source(source: str, *, where: str) -> tuple[WriteCallSite, ...]:
    """Every writing call site in one file's text. Raises `SyntaxError` on a file it cannot parse.

    A parser that skipped what it could not read would report a clean scan of a file nobody
    checked, which is the same disease as a gate announcing a verdict with no measurement.
    """
    tree = ast.parse(source, filename=where)

    # Calls sitting in a decorator position, collected first so the walk below can skip them by
    # identity. See blind spot 1 in the module docstring.
    decorator_calls: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            for decorator in node.decorator_list:
                decorator_calls.update(id(inner) for inner in ast.walk(decorator))

    found: list[WriteCallSite] = []
    for node in ast.walk(tree):
        if id(node) in decorator_calls:
            continue
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in WRITING_METHOD_NAMES
        ):
            read_mcp = (
                node.func.attr == "post"
                and where in PERMITTED_READ_MCP
                and bool(node.args)
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == PERMITTED_READ_MCP[where]
            )
            found.append(
                WriteCallSite(
                    where=where,
                    line=node.lineno,
                    what=READ_MCP_CALL if read_mcp else f"wywołanie .{node.func.attr}()",
                )
            )
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and WRITING_CDP_METHOD.match(node.value)
        ):
            found.append(
                WriteCallSite(where=where, line=node.lineno, what=f"metoda CDP {node.value!r}")
            )
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in ACTUATOR_ADDRESS_MARKERS
        ):
            found.append(
                WriteCallSite(
                    where=where, line=node.lineno, what=f"{ACTUATOR_ADDRESS} {node.value!r}"
                )
            )
        elif (isinstance(node, ast.Name) and node.id in ACTUATOR_ADDRESS_NAMES) or (
            isinstance(node, ast.Attribute) and node.attr in ACTUATOR_ADDRESS_NAMES
        ):
            name = node.id if isinstance(node, ast.Name) else node.attr
            found.append(
                WriteCallSite(where=where, line=node.lineno, what=f"{ACTUATOR_ADDRESS} {name}")
            )
        elif isinstance(node, ast.ImportFrom) and any(
            alias.name in ACTUATOR_ADDRESS_NAMES for alias in node.names
        ):
            found.append(
                WriteCallSite(where=where, line=node.lineno, what=f"{ACTUATOR_ADDRESS} import")
            )
    if where in PERMITTED_INTERNAL_CONTROL:
        found.extend(_foreign_post_targets(tree, where=where, skip=decorator_calls))
    return tuple(sorted(found, key=lambda site: (site.where, site.line, site.what)))


def _foreign_post_targets(tree: ast.AST, *, where: str, skip: set[int]) -> list[WriteCallSite]:
    """The per-address rule for a named internal-control file (D-027): every `.post()` must
    address `self._base_url`, and no literal inside its URL argument may name a foreign host.

    A `Name` argument is resolved to its binding in the same function (`url = f"{self._base_url}
    {path}"` is how `capsule_client.py` and `reddit_browser.py` spell it); anything the resolver
    cannot see — a parameter, an attribute, a global — is reported, because "could not tell" must
    read as a violation here, not as a pass.
    """
    sites: list[WriteCallSite] = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        bound: dict[str, ast.expr] = {}
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bound[target.id] = node.value
        for node in ast.walk(function):
            if id(node) in skip or not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "post"
            ):
                continue
            target: ast.expr | None = node.args[0] if node.args else None
            if target is None:
                target = next((kw.value for kw in node.keywords if kw.arg == "url"), None)
            if isinstance(target, ast.Name):
                target = bound.get(target.id, target)
            problem = _describe_foreign_target(target)
            if problem is not None:
                sites.append(WriteCallSite(where=where, line=node.lineno, what=problem))
    return sites


def _describe_foreign_target(target: ast.expr | None) -> str | None:
    """Why a `.post()` address is not an internal-control address, or `None` when it is one."""
    if target is None:
        return "wywołanie .post() bez adresu"
    for node in ast.walk(target):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            match = URL_LITERAL.match(node.value)
            if match and match.group(1) not in INTERNAL_CONTROL_HOSTS:
                return f"adres .post() spoza usług compose: {match.group(1)!r}"
    rooted = any(
        isinstance(node, ast.Attribute)
        and node.attr == "_base_url"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        for node in ast.walk(target)
    )
    if not rooted:
        return "adres .post() nie pochodzi z self._base_url"
    return None


def scan_tree(root: pathlib.Path, *, subtree: str = "services") -> ScanResult:
    """Scan the production Python of `root / subtree`. `root` is the repository root.

    Test files are not production code and are excluded by `IGNORED_DIRECTORY_NAMES`; so is every
    virtualenv. The paths in the result are relative to `root`, so the same result reads the same
    on every machine and inside the container.
    """
    files_read: list[str] = []
    sites: list[WriteCallSite] = []
    for path in sorted((root / subtree).rglob("*.py")):
        if IGNORED_DIRECTORY_NAMES.intersection(path.parts):
            continue
        where = path.relative_to(root).as_posix()
        files_read.append(where)
        sites.extend(scan_source(path.read_text(encoding="utf-8"), where=where))
    return ScanResult(files_read=tuple(files_read), sites=tuple(sites))
