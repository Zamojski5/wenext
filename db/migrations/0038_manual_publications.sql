-- Migracja 0038: odpowiedzi opublikowane ręcznie przez założyciela (wersja hostowana).
-- A reply the founder posted BY HAND (hosted MVP: nothing is published by Wenext). One row per
-- revision. `draft.mark_posted` writes it; the Pokoj read (`opportunities/store.py` _VIEW_SQL,
-- `opportunities/room.py` _drafts and _results) reads it so the card flips to "Posted".
-- The revision itself is immutable (0003 trigger), so the fact lives next to it, not on it.

BEGIN;

CREATE TABLE manual_publications (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     uuid NOT NULL REFERENCES organizations (id),
    workspace_id        uuid NOT NULL,
    project_id          uuid NOT NULL,
    revision_id         uuid NOT NULL,
    platform            text NOT NULL,
    -- The reply target, spelled like `project_opportunities.external_id` and
    -- `publication_requests.target_external_id`: `t3_<id>` or `x:<status id>`.
    target_external_id  text NOT NULL CHECK (length(target_external_id) BETWEEN 1 AND 100),
    target_url          text NOT NULL CHECK (target_url ~ '^https://' AND length(target_url) <= 2000),
    -- The text of the revision at the time of the mark, so the result row needs no join.
    text                text NOT NULL,
    -- The link the founder pasted to their own post, when they did. NULL means they did not.
    url                 text CHECK (url IS NULL OR (url ~ '^https://' AND length(url) <= 2000)),
    posted_at           timestamptz NOT NULL DEFAULT now(),
    session_id          uuid,
    UNIQUE (organization_id, revision_id),
    CONSTRAINT manual_publications_platform_is_known CHECK (platform IN ('REDDIT', 'X')),
    CONSTRAINT manual_publications_point_at_revision
        FOREIGN KEY (organization_id, workspace_id, revision_id)
        REFERENCES action_proposal_revisions (organization_id, workspace_id, id),
    CONSTRAINT manual_publications_point_at_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id)
);

CREATE INDEX manual_publications_by_project
    ON manual_publications (organization_id, project_id, posted_at DESC);
CREATE INDEX manual_publications_by_target
    ON manual_publications (organization_id, project_id, target_external_id);

COMMIT;
