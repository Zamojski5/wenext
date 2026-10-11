-- Identity introduced users/memberships, but delegation signatures still required a legacy
-- operator. Keep historical signatures intact; new ones cite the actual organization membership.
BEGIN;
ALTER TABLE project_delegations
    ALTER COLUMN signed_by DROP NOT NULL,
    ADD COLUMN signed_membership_id uuid,
    ADD CONSTRAINT project_delegations_signed_by_membership
        FOREIGN KEY (organization_id, signed_membership_id)
        REFERENCES memberships (organization_id, id),
    ADD CONSTRAINT project_delegations_one_signer
        CHECK (num_nonnulls(signed_by, signed_membership_id) = 1),
    -- Publication approvals still cite the legacy operator (0003). Do not accept a scope
    -- whose publication path cannot yet represent its signer. Research has no such action.
    ADD CONSTRAINT project_delegations_membership_research_only
        CHECK (signed_membership_id IS NULL OR mode = 'RESEARCH');
COMMIT;
