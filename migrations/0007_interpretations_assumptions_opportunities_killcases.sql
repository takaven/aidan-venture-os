-- Gate 2 / Slice 3 — reasoning artifacts: Interpretations, Assumptions,
-- Opportunities and adversarial Kill Cases.
--
-- CRITICAL BOUNDARY: none of these are evidence. They are NOT subtypes of
-- evidence_record. They are standalone typed tables that link BACK to canonical
-- Claims (which are evidence). Reasoning never becomes evidence, and it never
-- mutates Claim structural state.
--
-- Builds on immutable 0001-0006. Forward-only: never edit after use.
-- Reuses claim's UNIQUE (evidence_record_id, venture_id) as a composite-FK
-- target so reasoning links to a Claim of the same venture.

-- ==========================================================================
-- INTERPRETATION (reasoning over Claims; append-only)
-- ==========================================================================
CREATE TABLE interpretation (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id         uuid NOT NULL REFERENCES venture(id),
    interpretation_key text NOT NULL,
    statement          text NOT NULL,
    statement_hash     text NOT NULL,
    produced_by        text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venture_id, interpretation_key),
    UNIQUE (id, venture_id)
);
CREATE TRIGGER interpretation_no_update BEFORE UPDATE ON interpretation FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER interpretation_no_delete BEFORE DELETE ON interpretation FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER interpretation_no_truncate BEFORE TRUNCATE ON interpretation FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard();

CREATE TABLE interpretation_claim (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    interpretation_id uuid NOT NULL,
    claim_id          uuid NOT NULL,
    venture_id        uuid NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (interpretation_id, claim_id),
    FOREIGN KEY (interpretation_id, venture_id) REFERENCES interpretation (id, venture_id),
    FOREIGN KEY (claim_id, venture_id) REFERENCES claim (evidence_record_id, venture_id)
);
CREATE TRIGGER interpretation_claim_no_update BEFORE UPDATE ON interpretation_claim FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER interpretation_claim_no_delete BEFORE DELETE ON interpretation_claim FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- ==========================================================================
-- ASSUMPTION (explicit uncertainty; categorical only; append-only)
-- ==========================================================================
CREATE TABLE assumption (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id           uuid NOT NULL REFERENCES venture(id),
    assumption_key       text NOT NULL,
    proposition          text NOT NULL,
    proposition_hash     text NOT NULL,
    importance           text NOT NULL CHECK (importance IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    confidence           text NOT NULL CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH')),
    consequence_if_false text NOT NULL,
    cheapest_test        text NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venture_id, assumption_key),
    UNIQUE (id, venture_id)
);
CREATE TRIGGER assumption_no_update BEFORE UPDATE ON assumption FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER assumption_no_delete BEFORE DELETE ON assumption FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER assumption_no_truncate BEFORE TRUNCATE ON assumption FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard();

CREATE TABLE assumption_claim (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    assumption_id uuid NOT NULL,
    claim_id      uuid NOT NULL,
    venture_id    uuid NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (assumption_id, claim_id),
    FOREIGN KEY (assumption_id, venture_id) REFERENCES assumption (id, venture_id),
    FOREIGN KEY (claim_id, venture_id) REFERENCES claim (evidence_record_id, venture_id)
);
CREATE TRIGGER assumption_claim_no_update BEFORE UPDATE ON assumption_claim FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER assumption_claim_no_delete BEFORE DELETE ON assumption_claim FOR EACH ROW EXECUTE FUNCTION append_only_guard();

CREATE TABLE assumption_interpretation (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    assumption_id     uuid NOT NULL,
    interpretation_id uuid NOT NULL,
    venture_id        uuid NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (assumption_id, interpretation_id),
    FOREIGN KEY (assumption_id, venture_id) REFERENCES assumption (id, venture_id),
    FOREIGN KEY (interpretation_id, venture_id) REFERENCES interpretation (id, venture_id)
);
CREATE TRIGGER assumption_interpretation_no_update BEFORE UPDATE ON assumption_interpretation FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER assumption_interpretation_no_delete BEFORE DELETE ON assumption_interpretation FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- ==========================================================================
-- OPPORTUNITY (research candidate; content immutable, status guarded)
-- ==========================================================================
CREATE TABLE opportunity (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id            uuid NOT NULL REFERENCES venture(id),
    opportunity_key       text NOT NULL,
    buyer_hypothesis      text,
    problem_hypothesis    text,
    acquisition_hypothesis text,
    critical_unknown      text,
    payload_hash          text NOT NULL,
    status                text NOT NULL DEFAULT 'DRAFT'
                            CHECK (status IN ('DRAFT', 'INSUFFICIENT_EVIDENCE', 'CANDIDATE', 'KILLED')),
    status_reason         text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venture_id, opportunity_key),
    UNIQUE (id, venture_id)
);

-- Content is immutable; only status/status_reason/updated_at may change.
CREATE FUNCTION opportunity_content_immutable() RETURNS trigger AS $$
BEGIN
    IF NEW.venture_id IS DISTINCT FROM OLD.venture_id
       OR NEW.opportunity_key IS DISTINCT FROM OLD.opportunity_key
       OR NEW.buyer_hypothesis IS DISTINCT FROM OLD.buyer_hypothesis
       OR NEW.problem_hypothesis IS DISTINCT FROM OLD.problem_hypothesis
       OR NEW.acquisition_hypothesis IS DISTINCT FROM OLD.acquisition_hypothesis
       OR NEW.critical_unknown IS DISTINCT FROM OLD.critical_unknown
       OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'opportunity content is immutable; only status may transition';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER opportunity_content_guard BEFORE UPDATE ON opportunity FOR EACH ROW EXECUTE FUNCTION opportunity_content_immutable();
CREATE TRIGGER opportunity_no_delete BEFORE DELETE ON opportunity FOR EACH ROW EXECUTE FUNCTION append_only_guard();

CREATE TABLE opportunity_claim (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id uuid NOT NULL,
    claim_id       uuid NOT NULL,
    venture_id     uuid NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (opportunity_id, claim_id),
    FOREIGN KEY (opportunity_id, venture_id) REFERENCES opportunity (id, venture_id),
    FOREIGN KEY (claim_id, venture_id) REFERENCES claim (evidence_record_id, venture_id)
);
CREATE TRIGGER opportunity_claim_no_update BEFORE UPDATE ON opportunity_claim FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER opportunity_claim_no_delete BEFORE DELETE ON opportunity_claim FOR EACH ROW EXECUTE FUNCTION append_only_guard();

CREATE TABLE opportunity_assumption (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id uuid NOT NULL,
    assumption_id  uuid NOT NULL,
    venture_id     uuid NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (opportunity_id, assumption_id),
    FOREIGN KEY (opportunity_id, venture_id) REFERENCES opportunity (id, venture_id),
    FOREIGN KEY (assumption_id, venture_id) REFERENCES assumption (id, venture_id)
);
CREATE TRIGGER opportunity_assumption_no_update BEFORE UPDATE ON opportunity_assumption FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER opportunity_assumption_no_delete BEFORE DELETE ON opportunity_assumption FOR EACH ROW EXECUTE FUNCTION append_only_guard();

CREATE TABLE opportunity_interpretation (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id    uuid NOT NULL,
    interpretation_id uuid NOT NULL,
    venture_id        uuid NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (opportunity_id, interpretation_id),
    FOREIGN KEY (opportunity_id, venture_id) REFERENCES opportunity (id, venture_id),
    FOREIGN KEY (interpretation_id, venture_id) REFERENCES interpretation (id, venture_id)
);
CREATE TRIGGER opportunity_interpretation_no_update BEFORE UPDATE ON opportunity_interpretation FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER opportunity_interpretation_no_delete BEFORE DELETE ON opportunity_interpretation FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- ==========================================================================
-- KILL CASE (adversarial reasoning; one per opportunity; append-only)
-- ==========================================================================
CREATE TABLE kill_case (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id uuid NOT NULL UNIQUE,
    venture_id     uuid NOT NULL,
    kill_case_key  text NOT NULL,
    disposition    text NOT NULL CHECK (disposition IN ('PROCEED_WITH_RISKS', 'KILL', 'INSUFFICIENT_EVIDENCE')),
    content_hash   text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, venture_id),
    FOREIGN KEY (opportunity_id, venture_id) REFERENCES opportunity (id, venture_id)
);
CREATE TRIGGER kill_case_no_update BEFORE UPDATE ON kill_case FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER kill_case_no_delete BEFORE DELETE ON kill_case FOR EACH ROW EXECUTE FUNCTION append_only_guard();

CREATE TABLE kill_case_dimension (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kill_case_id uuid NOT NULL,
    venture_id   uuid NOT NULL,
    dimension    text NOT NULL CHECK (dimension IN (
                   'DOMINANT_ALTERNATIVES', 'WTP_WEAKNESS', 'DISTRIBUTION_DIFFICULTY', 'COMMODITISATION',
                   'SWITCHING_BARRIERS', 'REGULATION', 'DATA_CONSTRAINTS', 'UNIT_ECONOMICS',
                   'SUPPORT_BURDEN', 'MARKET_SIZE', 'TECHNOLOGY_RISK')),
    assessment   text NOT NULL CHECK (assessment IN ('LOW_RISK', 'MATERIAL_RISK', 'SEVERE_RISK', 'INSUFFICIENT_EVIDENCE')),
    rationale    text NOT NULL,
    content_hash text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kill_case_id, dimension),
    UNIQUE (id, venture_id),
    FOREIGN KEY (kill_case_id, venture_id) REFERENCES kill_case (id, venture_id)
);
CREATE TRIGGER kill_case_dimension_no_update BEFORE UPDATE ON kill_case_dimension FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER kill_case_dimension_no_delete BEFORE DELETE ON kill_case_dimension FOR EACH ROW EXECUTE FUNCTION append_only_guard();

CREATE TABLE kill_case_dimension_claim (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dimension_id uuid NOT NULL,
    claim_id     uuid NOT NULL,
    venture_id   uuid NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dimension_id, claim_id),
    FOREIGN KEY (dimension_id, venture_id) REFERENCES kill_case_dimension (id, venture_id),
    FOREIGN KEY (claim_id, venture_id) REFERENCES claim (evidence_record_id, venture_id)
);
CREATE TRIGGER kill_case_dimension_claim_no_update BEFORE UPDATE ON kill_case_dimension_claim FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER kill_case_dimension_claim_no_delete BEFORE DELETE ON kill_case_dimension_claim FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- ==========================================================================
-- RESEARCH RESULT (durable research-context outcome; append-only)
-- ==========================================================================
CREATE TABLE research_result (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id uuid NOT NULL REFERENCES venture(id),
    result_key text NOT NULL,
    outcome    text NOT NULL CHECK (outcome IN ('OPPORTUNITIES_FOUND', 'NO_CREDIBLE_OPPORTUNITY', 'INSUFFICIENT_EVIDENCE')),
    reason     text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venture_id, result_key)
);
CREATE TRIGGER research_result_no_update BEFORE UPDATE ON research_result FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER research_result_no_delete BEFORE DELETE ON research_result FOR EACH ROW EXECUTE FUNCTION append_only_guard();

CREATE INDEX opportunity_claim_opp_idx ON opportunity_claim (opportunity_id);
CREATE INDEX opportunity_assumption_opp_idx ON opportunity_assumption (opportunity_id);
CREATE INDEX kill_case_dimension_kc_idx ON kill_case_dimension (kill_case_id);
