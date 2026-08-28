-- Gate 3 / Slice 1 — validation precommitment substrate.
--
-- validation_hypothesis: the uncertain proposition whose resolution could change
-- the venture decision (reasoning; links back to Gate 2 Opportunity/Assumption).
-- validation_test: the IMMUTABLE precommitted definition of what will be tested
-- and how success/kill are judged. It is NOT an execution-state machine — Gate 1
-- ActionRequest/run_status/proof remain the execution authority. An optional
-- action_request_id is a set-once provenance link.
-- validation_result: append-only observed outcomes, with observed measurement
-- separated from interpretation; contradictory results coexist and are never
-- overwritten. WTP modality and acquisition/usage measurement are separate
-- categorical domains (no global evidence-strength score).
--
-- Builds on immutable 0001-0008. Forward-only: never edit after use.
-- Does NOT alter investment_decision / investment_decision_record / lifecycle /
-- run_status / Gate 2 schema.

-- ==========================================================================
-- Validation hypothesis (append-only; reasoning, not evidence)
-- ==========================================================================
CREATE TABLE validation_hypothesis (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id       uuid NOT NULL REFERENCES venture(id),
    hypothesis_key   text NOT NULL,
    opportunity_id   uuid NOT NULL,
    assumption_id    uuid,
    statement        text NOT NULL,
    statement_hash   text NOT NULL,
    critical_unknown text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venture_id, hypothesis_key),
    UNIQUE (id, venture_id),
    FOREIGN KEY (opportunity_id, venture_id) REFERENCES opportunity (id, venture_id),
    FOREIGN KEY (assumption_id, venture_id) REFERENCES assumption (id, venture_id)
);
CREATE TRIGGER validation_hypothesis_no_update BEFORE UPDATE ON validation_hypothesis FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER validation_hypothesis_no_delete BEFORE DELETE ON validation_hypothesis FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- ==========================================================================
-- Validation test (IMMUTABLE precommitted definition; no execution status)
-- ==========================================================================
CREATE TABLE validation_test (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id               uuid NOT NULL,
    test_key                 text NOT NULL,
    validation_hypothesis_id uuid NOT NULL,
    test_type                text NOT NULL CHECK (test_type IN
                               ('INTERVIEW', 'LOI', 'PILOT', 'PREORDER', 'LANDING_PAGE',
                                'OUTREACH', 'PRICING', 'USAGE', 'BENCHMARK', 'OTHER')),
    method                   text NOT NULL,
    target_segment           text,
    success_criterion        text NOT NULL,          -- precommitted, human-readable
    kill_criterion           text,                    -- precommitted where applicable
    evidence_required        text NOT NULL,           -- precommitted
    max_spend                numeric(20,4) CHECK (max_spend IS NULL OR max_spend >= 0),
    max_duration_days        integer CHECK (max_duration_days IS NULL OR max_duration_days >= 0),
    -- Optional structured criteria enabling deterministic PASS/FAIL evaluation.
    success_metric           text,
    success_comparator       text CHECK (success_comparator IS NULL OR success_comparator IN ('GTE','LTE','GT','LT','EQ','IS_TRUE')),
    success_threshold        numeric,
    kill_metric              text,
    kill_comparator          text CHECK (kill_comparator IS NULL OR kill_comparator IN ('GTE','LTE','GT','LT','EQ','IS_TRUE')),
    kill_threshold           numeric,
    definition_hash          text NOT NULL,          -- digest of the precommitted definition (idempotency)
    -- Set-once provenance link to the Gate 1 ActionRequest that executes this test.
    action_request_id        uuid,
    created_at               timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venture_id, test_key),
    UNIQUE (id, venture_id),
    FOREIGN KEY (validation_hypothesis_id, venture_id) REFERENCES validation_hypothesis (id, venture_id),
    FOREIGN KEY (action_request_id, venture_id) REFERENCES action_request (id, venture_id)
);

-- Definition is immutable; only a NULL action_request_id may be set once.
CREATE FUNCTION validation_test_guard() RETURNS trigger AS $$
BEGIN
    IF NEW.venture_id IS DISTINCT FROM OLD.venture_id
       OR NEW.test_key IS DISTINCT FROM OLD.test_key
       OR NEW.validation_hypothesis_id IS DISTINCT FROM OLD.validation_hypothesis_id
       OR NEW.test_type IS DISTINCT FROM OLD.test_type
       OR NEW.method IS DISTINCT FROM OLD.method
       OR NEW.target_segment IS DISTINCT FROM OLD.target_segment
       OR NEW.success_criterion IS DISTINCT FROM OLD.success_criterion
       OR NEW.kill_criterion IS DISTINCT FROM OLD.kill_criterion
       OR NEW.evidence_required IS DISTINCT FROM OLD.evidence_required
       OR NEW.max_spend IS DISTINCT FROM OLD.max_spend
       OR NEW.max_duration_days IS DISTINCT FROM OLD.max_duration_days
       OR NEW.success_metric IS DISTINCT FROM OLD.success_metric
       OR NEW.success_comparator IS DISTINCT FROM OLD.success_comparator
       OR NEW.success_threshold IS DISTINCT FROM OLD.success_threshold
       OR NEW.kill_metric IS DISTINCT FROM OLD.kill_metric
       OR NEW.kill_comparator IS DISTINCT FROM OLD.kill_comparator
       OR NEW.kill_threshold IS DISTINCT FROM OLD.kill_threshold
       OR NEW.definition_hash IS DISTINCT FROM OLD.definition_hash
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'validation_test definition is immutable (precommitted); create a new test instead';
    END IF;
    IF OLD.action_request_id IS NOT NULL AND NEW.action_request_id IS DISTINCT FROM OLD.action_request_id THEN
        RAISE EXCEPTION 'validation_test action_request_id is set once';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER validation_test_immutable BEFORE UPDATE ON validation_test FOR EACH ROW EXECUTE FUNCTION validation_test_guard();
CREATE TRIGGER validation_test_no_delete BEFORE DELETE ON validation_test FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- ==========================================================================
-- Validation result (append-only; observed vs interpretation; contradictions coexist)
-- ==========================================================================
CREATE TABLE validation_result (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id         uuid NOT NULL,
    validation_test_id uuid NOT NULL,
    result_key         text NOT NULL,
    observed_value     jsonb NOT NULL DEFAULT '{}'::jsonb,   -- structured observed measurement
    observed_hash      text NOT NULL,
    interpretation     text,                                  -- explicitly typed as interpretation, NOT evidence
    outcome            text NOT NULL CHECK (outcome IN ('PASS', 'FAIL', 'INCONCLUSIVE')),
    -- Two separate categorical evidence domains (no cross-domain score):
    wtp_modality       text CHECK (wtp_modality IS NULL OR wtp_modality IN
                         ('STATED_INTEREST', 'STATED_WILLINGNESS', 'SIGNED_COMMITMENT', 'ACTUAL_PAYMENT', 'NOT_APPLICABLE')),
    measurement_kind   text CHECK (measurement_kind IS NULL OR measurement_kind IN
                         ('OUTREACH_RESPONSE', 'LANDING_CONVERSION', 'ACQUISITION_COST', 'ACTIVATION',
                          'RETENTION', 'USAGE', 'OTHER_MEASURED_METRIC')),
    observation_id     uuid,                                  -- optional canonical evidence provenance
    external_result_ref text,
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venture_id, result_key),
    FOREIGN KEY (validation_test_id, venture_id) REFERENCES validation_test (id, venture_id),
    FOREIGN KEY (observation_id, venture_id) REFERENCES observation (evidence_record_id, venture_id)
);
CREATE TRIGGER validation_result_no_update BEFORE UPDATE ON validation_result FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER validation_result_no_delete BEFORE DELETE ON validation_result FOR EACH ROW EXECUTE FUNCTION append_only_guard();

CREATE INDEX validation_test_hypothesis_idx ON validation_test (validation_hypothesis_id);
CREATE INDEX validation_result_test_idx ON validation_result (validation_test_id);
