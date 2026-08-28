-- Gate 8 / Slice 1 — connect Gate-7 market evidence to the existing allocator.
--
-- Two bounded, additive changes so a next-action recommendation can be reconstructed as
-- having CONSIDERED exact canonical market observations, and can select another bounded
-- market test:
--   1. extend next_action_recommendation.action_type with 'MARKET' (mirrors the existing
--      investment_decision enum value MARKET). 'MARKET' means "run another bounded governed
--      market-validation action" — NOT market success, demand, scale, or revenue.
--   2. add recommendation_market_observation: exact relational provenance from a
--      recommendation to the canonical market_observation rows it used as evidence.
--
-- The allocator consumes OBSERVATIONS only in this slice, so NO recommendation↔interpretation
-- table is added (no speculative schema). Observations remain evidence; interpretation stays
-- advisory and is not consumed here. NO market score, NO closed_loop_run, NO autonomy/
-- intervention table, NO observation window, NO provider/credential state. Builds on immutable
-- 0001-0021. Forward-only: never edit after use.

-- 1. extend the recommendation action vocabulary with MARKET (additive; existing values kept).
ALTER TABLE next_action_recommendation DROP CONSTRAINT IF EXISTS next_action_recommendation_action_type_check;
ALTER TABLE next_action_recommendation ADD CONSTRAINT next_action_recommendation_action_type_check
    CHECK (action_type IN ('RESEARCH_MORE', 'VALIDATE', 'BUILD', 'HOLD', 'KILL', 'MARKET'));

-- 2. exact provenance: which canonical market observations a recommendation used as evidence.
CREATE TABLE recommendation_market_observation (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id  uuid NOT NULL,
    observation_id     uuid NOT NULL,
    venture_id         uuid NOT NULL,          -- must equal both parents' venture (FK-enforced)
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (recommendation_id, observation_id),  -- each observation cited at most once
    FOREIGN KEY (recommendation_id, venture_id)
        REFERENCES next_action_recommendation (id, venture_id),
    FOREIGN KEY (observation_id, venture_id)
        REFERENCES market_observation (id, venture_id)
);
CREATE TRIGGER recommendation_market_observation_no_update
    BEFORE UPDATE ON recommendation_market_observation FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER recommendation_market_observation_no_delete
    BEFORE DELETE ON recommendation_market_observation FOR EACH ROW EXECUTE FUNCTION append_only_guard();
