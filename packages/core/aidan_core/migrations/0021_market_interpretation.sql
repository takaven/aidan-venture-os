-- Gate 7 / Slice 3 — provenance-bound market interpretation + exact source binding.
--
-- An interpretation is a bounded, provenance-cited reading of one or more canonical
-- market_observation rows. It is NOT evidence: the observations remain the only
-- externally-attributable market evidence, and the market-action Proof Receipt remains the
-- only proof that the exact authorized action occurred. An interpretation has NO decision
-- authority — it cannot write an investment decision, transition lifecycle, move capital,
-- create an ActionRequest, or mutate a validation_result. It is advisory data.
--
-- Provenance is RELATIONAL, not a trusted JSON id list: market_interpretation_source binds
-- each interpretation to the exact immutable observations it cites, with composite FKs that
-- DB-enforce single-venture provenance (a cited observation must belong to the
-- interpretation's venture). New evidence yields a NEW interpretation; history is retained.
--
-- Scope: market_interpretation + market_interpretation_source ONLY. NO market_score / metrics
-- table (metrics are derived by query over observations), NO operate_run orchestration state,
-- NO investment/lifecycle/recommendation/CRM/campaign/payment state. NO NO_RESPONSE window is
-- added (no canonical observation-window primitive exists; absence stays deferred). Builds on
-- immutable 0001-0020. Forward-only: never edit after use.

-- market_observation needs a composite-FK target so a source row can be pinned to the exact
-- (observation, venture) pair; additive constraint on the 0020 table (0020 itself unchanged).
ALTER TABLE market_observation
    ADD CONSTRAINT market_observation_id_venture_uk UNIQUE (id, venture_id);

CREATE TABLE market_interpretation (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id             uuid NOT NULL,
    -- action-scoped: every cited observation belongs to this one market action.
    market_action_spec_id  uuid NOT NULL,
    interpretation_key      text NOT NULL,     -- stable interpretation identity (idempotency)
    interpreter_kind        text NOT NULL,     -- e.g. deterministic-kernel / model / adapter
    interpreter_ref         text,              -- optional model/agent version or ref
    interpretation_type     text NOT NULL CHECK (interpretation_type IN
                              ('MARKET_SUMMARY', 'RESPONSE_PATTERN', 'COMMERCIAL_SIGNAL')),
    interpretation_payload  jsonb NOT NULL DEFAULT '{}'::jsonb,
    interpretation_hash     text NOT NULL,     -- kernel-derived over provenance + payload
    created_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venture_id, interpretation_key),   -- stable identity; re-use must converge
    UNIQUE (id, venture_id),                   -- composite-FK target for sources
    FOREIGN KEY (market_action_spec_id, venture_id)
        REFERENCES market_action_spec (id, venture_id)
);
CREATE TRIGGER market_interpretation_no_update
    BEFORE UPDATE ON market_interpretation FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER market_interpretation_no_delete
    BEFORE DELETE ON market_interpretation FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- Exact many-to-many provenance from an interpretation to its source observations.
CREATE TABLE market_interpretation_source (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    interpretation_id  uuid NOT NULL,
    observation_id     uuid NOT NULL,
    venture_id         uuid NOT NULL,          -- must equal both parents' venture (FK-enforced)
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (interpretation_id, observation_id),  -- each source cited at most once
    FOREIGN KEY (interpretation_id, venture_id)
        REFERENCES market_interpretation (id, venture_id),
    FOREIGN KEY (observation_id, venture_id)
        REFERENCES market_observation (id, venture_id)
);
CREATE TRIGGER market_interpretation_source_no_update
    BEFORE UPDATE ON market_interpretation_source FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER market_interpretation_source_no_delete
    BEFORE DELETE ON market_interpretation_source FOR EACH ROW EXECUTE FUNCTION append_only_guard();
