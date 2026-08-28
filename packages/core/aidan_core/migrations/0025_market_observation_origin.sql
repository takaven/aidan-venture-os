-- Gate 8 / Slice 4 — durable REAL vs SIMULATED market-observation provenance.
--
-- A REAL_PROVIDER outbound action proof does NOT make an observed OUTCOME real: canonical state
-- must distinguish a trusted, authenticated/reconciled provider observation from a generic one.
-- market_observation_origin binds an exact market_observation to the trusted mechanism by which
-- it was authenticated. It is written ONLY by the trusted Postmark ingestion path, deriving
-- REAL_PROVIDER from the actual production transport type AND the action's REAL_PROVIDER action
-- proof — never from a caller flag, webhook JSON, or metadata. Absence of a REAL_PROVIDER row
-- means SIMULATED (fail-closed): a generic record_market_observation call confers no origin.
--
-- Scope: this one append-only entity. NO second observation/event system, NO provider-event
-- table, NO credential/webhook/workflow state, NO market score. Builds on immutable 0001-0024.
CREATE TABLE market_observation_origin (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id            uuid NOT NULL,
    market_observation_id uuid NOT NULL,
    market_action_spec_id uuid NOT NULL,
    proof_receipt_id      uuid NOT NULL REFERENCES proof_receipt (id),
    origin_kind           text NOT NULL CHECK (origin_kind IN ('REAL_PROVIDER', 'SIMULATED')),
    provider_kind         text NOT NULL,
    source_instance_ref   text NOT NULL,
    provider_event_ref    text NOT NULL,          -- the canonical external event identity
    origin_hash           text NOT NULL,          -- kernel-derived over exact provenance
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (market_observation_id),               -- at most one origin binding per observation
    FOREIGN KEY (market_observation_id, venture_id)
        REFERENCES market_observation (id, venture_id),
    FOREIGN KEY (market_action_spec_id, venture_id)
        REFERENCES market_action_spec (id, venture_id)
);
CREATE TRIGGER market_observation_origin_no_update
    BEFORE UPDATE ON market_observation_origin FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER market_observation_origin_no_delete
    BEFORE DELETE ON market_observation_origin FOR EACH ROW EXECUTE FUNCTION append_only_guard();
