-- Gate 3 / Slice 2 — highest-value next-action recommendations.
--
-- A next_action_recommendation is REASONING, not a canonical investment decision.
-- It never spends, executes, approves, or moves lifecycle. It is append-only:
-- later evidence produces a NEW recommendation and never rewrites a prior one.
-- Each recommendation links to the exact Assumptions / Validation Tests /
-- Validation Results it considered, so its basis is inspectable historically.
--
-- Builds on immutable 0001-0009. Forward-only: never edit after use.
-- Does NOT alter investment_decision / investment_decision_record / opportunity
-- status / lifecycle / validation schema.

-- Composite-FK target so a recommendation can link the exact results considered.
ALTER TABLE validation_result
    ADD CONSTRAINT validation_result_id_venture_uk UNIQUE (id, venture_id);

CREATE TABLE next_action_recommendation (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id                  uuid NOT NULL REFERENCES venture(id),
    recommendation_key          text NOT NULL,
    opportunity_id              uuid NOT NULL,
    action_type                 text NOT NULL CHECK (action_type IN
                                  ('RESEARCH_MORE', 'VALIDATE', 'BUILD', 'HOLD', 'KILL')),
    dominant_reason_code        text NOT NULL CHECK (dominant_reason_code IN
                                  ('KILL_CRITERION_TRIGGERED', 'INSUFFICIENT_EVIDENCE',
                                   'CRITICAL_ASSUMPTION_UNRESOLVED', 'VALIDATION_TEST_AVAILABLE',
                                   'VALIDATION_CONTRADICTORY', 'WTP_UNRESOLVED', 'ACQUISITION_UNRESOLVED',
                                   'VALIDATION_POSITIVE', 'BUILD_CONSIDERATION_READY', 'NO_HIGH_VALUE_ACTION_NOW')),
    rationale                   text,
    input_hash                  text NOT NULL,       -- digest of the considered input state (idempotency)
    selected_validation_test_id uuid,                 -- the test chosen for a VALIDATE action, where applicable
    created_at                  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venture_id, recommendation_key),
    UNIQUE (id, venture_id),
    FOREIGN KEY (opportunity_id, venture_id) REFERENCES opportunity (id, venture_id),
    FOREIGN KEY (selected_validation_test_id, venture_id) REFERENCES validation_test (id, venture_id)
);
CREATE TRIGGER next_action_recommendation_no_update BEFORE UPDATE ON next_action_recommendation FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER next_action_recommendation_no_delete BEFORE DELETE ON next_action_recommendation FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- Provenance: exactly which records the recommendation considered (append-only).
CREATE TABLE recommendation_assumption (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id uuid NOT NULL,
    assumption_id     uuid NOT NULL,
    venture_id        uuid NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (recommendation_id, assumption_id),
    FOREIGN KEY (recommendation_id, venture_id) REFERENCES next_action_recommendation (id, venture_id),
    FOREIGN KEY (assumption_id, venture_id) REFERENCES assumption (id, venture_id)
);
CREATE TRIGGER recommendation_assumption_no_update BEFORE UPDATE ON recommendation_assumption FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER recommendation_assumption_no_delete BEFORE DELETE ON recommendation_assumption FOR EACH ROW EXECUTE FUNCTION append_only_guard();

CREATE TABLE recommendation_validation_test (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id  uuid NOT NULL,
    validation_test_id uuid NOT NULL,
    venture_id         uuid NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (recommendation_id, validation_test_id),
    FOREIGN KEY (recommendation_id, venture_id) REFERENCES next_action_recommendation (id, venture_id),
    FOREIGN KEY (validation_test_id, venture_id) REFERENCES validation_test (id, venture_id)
);
CREATE TRIGGER recommendation_validation_test_no_update BEFORE UPDATE ON recommendation_validation_test FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER recommendation_validation_test_no_delete BEFORE DELETE ON recommendation_validation_test FOR EACH ROW EXECUTE FUNCTION append_only_guard();

CREATE TABLE recommendation_validation_result (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id    uuid NOT NULL,
    validation_result_id uuid NOT NULL,
    venture_id           uuid NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (recommendation_id, validation_result_id),
    FOREIGN KEY (recommendation_id, venture_id) REFERENCES next_action_recommendation (id, venture_id),
    FOREIGN KEY (validation_result_id, venture_id) REFERENCES validation_result (id, venture_id)
);
CREATE TRIGGER recommendation_validation_result_no_update BEFORE UPDATE ON recommendation_validation_result FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER recommendation_validation_result_no_delete BEFORE DELETE ON recommendation_validation_result FOR EACH ROW EXECUTE FUNCTION append_only_guard();

CREATE INDEX next_action_recommendation_opp_idx ON next_action_recommendation (opportunity_id);
