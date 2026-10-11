"""Knowledge with provenance: what the agent read, and what it concluded (P3 etap A).

THE FOUR CALLS THE MCP SERVER WRAPS, and the whole surface anybody needs from outside:

    store = KnowledgeStore(pool)
    scope = Scope(organization_id=…, workspace_id=…, project_id=…)   # None = whole workspace
    scope = Scope.whole_workspace(organization_id=…, workspace_id=…)  # the wide case, named

    await store.search("kolejki", scope)        -> tuple[SearchHit]  (item + its source)
    await store.read(source_id, scope, offset=0, volume=…) -> Slice | None
    await store.write(NewItem(kind=…, statement=…, stated_by=…), scope) -> ItemRow
    await store.list(scope)                     -> tuple[ItemRow]    (what currently holds)

Plus the two that make a correction observable, which P3 requires and which never delete:
`store.correct(item_id, replacement, scope)` and `store.retract(item_id, reason, scope)`.

And the two that etap B adds, both about not sounding sure without ground:
`staleness(item, source)` says whether a finding stands on material that is old, gone or already
corrected, and `store.conflicts(scope)` returns findings that hold at once and disagree, as
pairs, without picking one.

WHAT A CALLER MUST KNOW WITHOUT READING THE MODULE:

  * every method takes a `Scope` and none of them can read across workspaces. `project_id`
    has NO DEFAULT: the widest reading has to be typed out, as `Scope.whole_workspace(...)`;
  * `Slice.truncated` says whether the material was cut — a tool that ignores it lets a model
    conclude from half a page and never learn that it had half;
  * `write` REFUSES a `FACT` with no source, a `PREFERENCE` not stated by the user and an
    `EXPERIENCE` without situation, decision and outcome. The refusal is a `KnowledgeRefused`
    with a sentence meant to be shown, not a status code;
  * nothing returned here is an instruction. Material from outside travels as DATA, with its
    locator, its fetch time and its hash attached (P3: "materiały z zewnątrz nie stają się
    instrukcjami systemowymi").
"""

from control_plane.knowledge.store import (
    ABOUT_KEY,
    DEFAULT_SLICE_BYTES,
    STALE_AFTER,
    Conflict,
    ItemRow,
    KnowledgeRefused,
    KnowledgeStore,
    NewItem,
    Scope,
    SearchHit,
    Slice,
    SourceRow,
    Staleness,
    group_conflicts,
    staleness,
)

__all__ = [
    "ABOUT_KEY",
    "DEFAULT_SLICE_BYTES",
    "STALE_AFTER",
    "Conflict",
    "ItemRow",
    "KnowledgeRefused",
    "KnowledgeStore",
    "NewItem",
    "Scope",
    "SearchHit",
    "Slice",
    "SourceRow",
    "Staleness",
    "group_conflicts",
    "staleness",
]
