-- Gate 3 / Slice 3 — governed conversion of a next-action recommendation into a
-- canonical investment decision (and, only when a bounded consequential amount
-- is honestly supplied, a Gate 1 ActionRequest).
--
-- This slice adds NO new table, type or function. It only strengthens the
-- existing append-only investment_decision_record (0002) so a governed decision
-- can record WHICH recommendation basis produced it, and so its resulting action
-- is venture-consistent:
--   * source_recommendation_id  — set-at-insert provenance link to the exact
--     next_action_recommendation (0010) whose basis was verified non-stale at
--     commit time. NULL for the pre-existing manual decisions.record_decision
--     path, which is unaffected.
--   * a composite FK on (resulting_action_id, venture_id) so a decision can only
--     ever claim a resulting ActionRequest belonging to the SAME venture.
--   * one governed decision per recommendation (partial UNIQUE), so a
--     recommendation cannot be silently committed twice.
--
-- Recording an investment decision remains a decision only: it never spends,
-- approves, executes, sets ActionRequest SUCCESS, creates a Proof Receipt, or
-- moves venture lifecycle. Those stay the authority of the Gate 1 chain
-- (ActionRequest -> Policy -> Approval -> Execution -> Proof Receipt).
--
-- The investment_decision ENUM is intentionally NOT extended: RESEARCH_MORE is a
-- next-action recommendation only and maps to NO investment decision.
--
-- Builds on immutable 0001-0010. Forward-only: never edit after use.
-- Does NOT alter investment_decision / next_action_recommendation / validation /
-- opportunity / lifecycle / run_status schema.

ALTER TABLE investment_decision_record
    ADD COLUMN source_recommendation_id uuid;

-- The recommendation basis must belong to the same venture as the decision.
ALTER TABLE investment_decision_record
    ADD CONSTRAINT investment_decision_source_recommendation_fk
        FOREIGN KEY (source_recommendation_id, venture_id)
        REFERENCES next_action_recommendation (id, venture_id);

-- A decision's resulting action (when present) must belong to the same venture.
ALTER TABLE investment_decision_record
    ADD CONSTRAINT investment_decision_resulting_action_venture_fk
        FOREIGN KEY (resulting_action_id, venture_id)
        REFERENCES action_request (id, venture_id);

-- At most one governed investment decision per recommendation (the manual path
-- leaves source_recommendation_id NULL and is not constrained here).
CREATE UNIQUE INDEX investment_decision_source_recommendation_uk
    ON investment_decision_record (source_recommendation_id)
    WHERE source_recommendation_id IS NOT NULL;
