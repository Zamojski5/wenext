BEGIN;
ALTER TABLE approval_decisions
    ALTER COLUMN approved_by DROP NOT NULL,
    ADD COLUMN approved_membership_id uuid,
    ADD CONSTRAINT approval_decisions_membership_signer
        FOREIGN KEY (organization_id, approved_membership_id)
        REFERENCES memberships (organization_id, id),
    ADD CONSTRAINT approval_decisions_one_signer
        CHECK (num_nonnulls(approved_by, approved_membership_id) = 1);

ALTER TABLE project_delegations
    DROP CONSTRAINT project_delegations_membership_research_only;

ALTER TABLE communities
    ADD COLUMN own_posts_membership_id uuid,
    ADD CONSTRAINT communities_own_posts_membership_signer
        FOREIGN KEY (organization_id, own_posts_membership_id)
        REFERENCES memberships (organization_id, id),
    DROP CONSTRAINT communities_own_posts_decision_has_an_author;

ALTER TABLE communities ADD CONSTRAINT communities_own_posts_decision_has_an_author CHECK (
    (own_posts_allowed IS NULL AND own_posts_decided_at IS NULL
        AND num_nonnulls(own_posts_decided_by, own_posts_membership_id) = 0)
    OR (own_posts_allowed IS NOT NULL AND own_posts_decided_at IS NOT NULL
        AND num_nonnulls(own_posts_decided_by, own_posts_membership_id) = 1)
);
ALTER TABLE wakeup_events DROP CONSTRAINT wakeup_events_kind_is_known;
ALTER TABLE wakeup_events ADD CONSTRAINT wakeup_events_kind_is_known
    CHECK (kind IN ('NEW_THREAD', 'REPLY', 'MODERATION', 'DUE', 'OUTCOME'));
COMMIT;
