-- Gate 5 / Slice 1 — governed venture build authority + isolated venture repository.
--
-- Establishes the immutable authority boundary between a governed Gate 3 BUILD
-- decision and any builder execution. A BUILD decision is NOT direct coding
-- authority: before a builder may run, the venture-specific product intent and
-- build acceptance contract must be FROZEN in an immutable build_spec bound 1:1
-- to the BUILD ActionRequest, and the isolated venture repository the product is
-- built in must be registered.
--
-- The build_spec product-intent fields (buyer, problem, value_proposition,
-- primary_workflow, differentiators, ...) are frozen REQUIREMENTS/decisions, NOT
-- market evidence. Commercial/market truth stays in the canonical Gate 2/3
-- records this spec references (opportunity, recommendation, investment
-- decision) and is never duplicated here. Provenance is expressed with CONCRETE
-- composite foreign keys — there is no polymorphic "reference anything" column.
--
-- Scope: build_spec + venture_repository, plus the one composite-FK target this
-- needs (investment_decision_record.(id, venture_id)). NO build manifest, NO
-- quality tables, NO substrate implementation, and NO substrate_release: binding
-- a substrate identity now would record provenance for a substrate that does not
-- yet exist, so substrate identity is deferred to Slice 2. NO deployment, NO
-- lifecycle change. The Builder is a Gate 4 WorkerAdapter; this migration adds no
-- second execution/worker runtime.
--
-- Builds on immutable 0001-0014. Forward-only: never edit after use.

-- Composite-FK target so a build_spec can bind the exact BUILD investment
-- decision that authorizes it, venture-consistently. Additive only; 0011's
-- append-only investment_decision_record is otherwise unchanged.
ALTER TABLE investment_decision_record
    ADD CONSTRAINT investment_decision_id_venture_uk UNIQUE (id, venture_id);

-- ==========================================================================
-- Immutable, venture-specific build specification (1:1 with the BUILD action)
-- ==========================================================================
CREATE TABLE build_spec (
    id                            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id                    uuid NOT NULL,
    action_request_id             uuid NOT NULL,
    -- Concrete Gate 3 BUILD provenance (no polymorphic reference column).
    source_investment_decision_id uuid NOT NULL,
    source_recommendation_id      uuid NOT NULL,
    opportunity_id                uuid NOT NULL,
    -- Frozen venture-specific product intent + build acceptance contract.
    buyer                         text NOT NULL,
    problem                       text NOT NULL,
    value_proposition             text NOT NULL,
    product_category              text NOT NULL,
    primary_workflow              text NOT NULL,
    differentiators               jsonb NOT NULL DEFAULT '[]'::jsonb,
    required_capabilities         jsonb NOT NULL DEFAULT '[]'::jsonb,
    excluded_capabilities         jsonb NOT NULL DEFAULT '[]'::jsonb,
    experience_principles         jsonb NOT NULL DEFAULT '[]'::jsonb,
    expected_output_contract      jsonb NOT NULL DEFAULT '{}'::jsonb,
    spec_hash                     text NOT NULL,   -- deterministic identity of the frozen build authority
    created_at                    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (action_request_id),                    -- one build_spec per BUILD ActionRequest
    UNIQUE (id, venture_id),                       -- composite-FK target
    FOREIGN KEY (action_request_id, venture_id)
        REFERENCES action_request (id, venture_id),
    FOREIGN KEY (source_investment_decision_id, venture_id)
        REFERENCES investment_decision_record (id, venture_id),
    FOREIGN KEY (source_recommendation_id, venture_id)
        REFERENCES next_action_recommendation (id, venture_id),
    FOREIGN KEY (opportunity_id, venture_id)
        REFERENCES opportunity (id, venture_id)
);

-- Fully immutable: no field may change, and rows cannot be deleted.
CREATE TRIGGER build_spec_no_update
    BEFORE UPDATE ON build_spec FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER build_spec_no_delete
    BEFORE DELETE ON build_spec FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- ==========================================================================
-- Isolated venture repository identity (one canonical product repo per venture)
-- ==========================================================================
-- Identity/authority only: no GitHub provisioning, no workspace mechanics, no
-- network. The canonical-OS-repository protection (a builder may never target the
-- OS monorepo) is enforced in the trusted kernel/workspace layer, NOT by a DB
-- string CHECK that would only pretend an opaque ref identifies the OS repo.
CREATE TABLE venture_repository (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id        uuid NOT NULL REFERENCES venture(id),
    repository_ref    text NOT NULL,
    repository_scheme text NOT NULL DEFAULT 'mock',
    provenance        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venture_id),         -- one canonical product repository per venture (Alpha)
    UNIQUE (repository_ref),     -- a repository backs at most one venture (isolation)
    UNIQUE (id, venture_id)      -- composite-FK target
);

CREATE TRIGGER venture_repository_no_update
    BEFORE UPDATE ON venture_repository FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER venture_repository_no_delete
    BEFORE DELETE ON venture_repository FOR EACH ROW EXECUTE FUNCTION append_only_guard();
