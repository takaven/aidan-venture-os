-- Gate 7 / Slice 1 — governed market action authority.
--
-- Establishes the authority boundary between an OPERATING venture and any external
-- market action. Before a market WorkerAdapter may be dispatched, the exact channel,
-- audience, content, offer/price and authorized spend must be FROZEN in an immutable
-- market_action_spec (1:1 with the market ActionRequest) and bound at the generic
-- Factory execution-spec boundary (enforced for ANY caller).
--
-- market_action_spec freezes EXECUTION INTENT only. It does NOT duplicate the Gate-3
-- commercial hypothesis: success/kill criteria, WTP modality and the experiment-level
-- max_spend remain owned by validation_test (referenced by FK). This spec stores only
-- this action's exact content/offer/audience and its authorized_spend_amount, which the
-- kernel proves is bounded by the referenced validation_test's max_spend AND canonical
-- budget state.
--
-- Scope: market_action_spec + the SEND_OUTREACH capability only. NO market_observation,
-- NO market_interpretation, NO metrics/proof/decision, NO external send. Builds on
-- immutable 0001-0018. Forward-only: never edit after use.

-- Extend the execution_spec capability vocabulary (0018's constraint) with the single
-- market capability this slice needs. Forward-only: drop + re-add; 0012/0018 unedited.
ALTER TABLE execution_spec DROP CONSTRAINT IF EXISTS execution_spec_capability_scope_check;
ALTER TABLE execution_spec ADD CONSTRAINT execution_spec_capability_scope_check
    CHECK (capability_scope <@ ARRAY[
        'READ_REPOSITORY', 'WRITE_ISOLATED_WORKSPACE', 'RUN_TESTS',
        'PRODUCE_PATCH', 'READ_DECLARED_INPUTS', 'DEPLOY_CANDIDATE', 'SEND_OUTREACH']::text[]);

-- ==========================================================================
-- Immutable market action specification (1:1 with the market ActionRequest)
-- ==========================================================================
CREATE TABLE market_action_spec (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id            uuid NOT NULL,
    action_request_id     uuid NOT NULL,
    -- concrete Gate 2/3 commercial provenance (no duplicated hypothesis/criteria)
    opportunity_id        uuid NOT NULL,
    validation_test_id    uuid NOT NULL,
    -- exact frozen execution intent
    channel_kind          text NOT NULL,                       -- provider-neutral adapter key
    audience_ref          text NOT NULL,                       -- opaque, venture-bounded
    audience_provenance   jsonb NOT NULL DEFAULT '{}'::jsonb,   -- consent/source (no secrets)
    content               text NOT NULL,                       -- exact frozen message body
    content_hash          text NOT NULL,                       -- kernel-derived over content
    offer_ref             text,
    price_amount          numeric(20,4) CHECK (price_amount IS NULL OR price_amount >= 0),
    price_currency        text,
    offer_terms           text,
    authorized_spend_amount numeric(20,4) NOT NULL DEFAULT 0 CHECK (authorized_spend_amount >= 0),
    spend_currency        text NOT NULL DEFAULT 'USD',
    action_spec_hash      text NOT NULL,                       -- identity of the complete intent
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (action_request_id),                                -- one spec per market ActionRequest
    UNIQUE (id, venture_id),                                   -- composite-FK target
    FOREIGN KEY (action_request_id, venture_id)
        REFERENCES action_request (id, venture_id),
    FOREIGN KEY (opportunity_id, venture_id)
        REFERENCES opportunity (id, venture_id),
    FOREIGN KEY (validation_test_id, venture_id)
        REFERENCES validation_test (id, venture_id)
);
CREATE TRIGGER market_action_spec_no_update
    BEFORE UPDATE ON market_action_spec FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER market_action_spec_no_delete
    BEFORE DELETE ON market_action_spec FOR EACH ROW EXECUTE FUNCTION append_only_guard();
