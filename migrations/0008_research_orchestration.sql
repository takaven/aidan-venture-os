-- Gate 2 / Slice 4 — durable research-run identity bound to a canonical Mandate.
--
-- A research_run ties one bounded, synchronous research execution to an exact
-- canonical venture_mandate_version. research_question rows record the derived
-- (reasoning) questions for provenance. Neither carries lifecycle or investment
-- semantics. Existing research_result (Slice 3) is not duplicated — the run's
-- terminal outcome lives on research_run.
--
-- Builds on immutable 0001-0007. Forward-only: never edit after use.

-- ==========================================================================
-- Research run (bounded; identity immutable; terminal outcome set once)
-- ==========================================================================
CREATE TABLE research_run (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id      uuid NOT NULL REFERENCES venture(id),
    mandate_version integer NOT NULL,
    mandate_hash    text NOT NULL,
    run_key         text NOT NULL,
    outcome         text CHECK (outcome IS NULL OR outcome IN
                      ('OPPORTUNITIES_FOUND', 'INSUFFICIENT_EVIDENCE', 'NO_CREDIBLE_OPPORTUNITY', 'FAILED')),
    started_at      timestamptz NOT NULL DEFAULT now(),
    ended_at        timestamptz,
    UNIQUE (venture_id, run_key),
    UNIQUE (id, venture_id),
    -- The run must reference an exact canonical Mandate version of this venture.
    FOREIGN KEY (venture_id, mandate_version) REFERENCES venture_mandate_version (venture_id, version)
);

CREATE FUNCTION research_run_guard() RETURNS trigger AS $$
BEGIN
    IF NEW.venture_id IS DISTINCT FROM OLD.venture_id
       OR NEW.mandate_version IS DISTINCT FROM OLD.mandate_version
       OR NEW.mandate_hash IS DISTINCT FROM OLD.mandate_hash
       OR NEW.run_key IS DISTINCT FROM OLD.run_key
       OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
        RAISE EXCEPTION 'research_run identity is immutable';
    END IF;
    IF OLD.outcome IS NOT NULL AND NEW.outcome IS DISTINCT FROM OLD.outcome THEN
        RAISE EXCEPTION 'research_run outcome is terminal (set once)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER research_run_immutable BEFORE UPDATE ON research_run FOR EACH ROW EXECUTE FUNCTION research_run_guard();
CREATE TRIGGER research_run_no_delete BEFORE DELETE ON research_run FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- ==========================================================================
-- Research question (reasoning/planning artifact; append-only)
-- ==========================================================================
CREATE TABLE research_question (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    research_run_id uuid NOT NULL,
    venture_id      uuid NOT NULL,
    ordinal         integer NOT NULL,
    question        text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (research_run_id, ordinal),
    FOREIGN KEY (research_run_id, venture_id) REFERENCES research_run (id, venture_id)
);

CREATE TRIGGER research_question_no_update BEFORE UPDATE ON research_question FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER research_question_no_delete BEFORE DELETE ON research_question FOR EACH ROW EXECUTE FUNCTION append_only_guard();
