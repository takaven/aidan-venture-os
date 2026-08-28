-- Gate 8 / Slice 3 — deterministic no-response completion + autonomy classification.
--
-- Two pre-real-run truth problems, each requiring the SMALLEST durable canonical fact that
-- cannot be reconstructed from existing immutable state:
--
--   1. market_window_completion — a deterministic NO_RESPONSE completion fact. The response
--      HORIZON is NOT duplicated here: window_start is the exact VERIFIED MARKET_ACTION
--      proof_receipt.created_at and the duration is the precommitted Gate-3
--      validation_test.max_duration_days (no new deadline field on market_action_spec). This
--      row exists only because a NO_RESPONSE recommendation cites ZERO observations and would
--      otherwise be unable to prove which deterministic completion fact it consumed. NO_RESPONSE
--      is a derived fact, never a market_observation event.
--      recommendation_market_window_completion binds a recommendation to the exact completion.
--
--   2. alpha_intervention — unplanned human intervention relevant to autonomous-run
--      classification. audit_event has no event type (and no writer) for human reasoning/code/
--      deployment/provider/outcome-transcription correction, so it cannot distinguish these from
--      predefined governance. PREDEFINED_APPROVAL is intentionally ABSENT from the vocabulary —
--      it is already proven by the immutable `approval` record and must not be duplicated here.
--
-- Scope: exactly these three entities. NO closed_loop_run, NO workflow/run engine, NO generic
-- audit platform, NO provider/credential table, NO response-window duplicate, NO market/autonomy
-- score, NO NO_RESPONSE observation type. Builds on immutable 0001-0022. Forward-only.

CREATE TABLE market_window_completion (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id            uuid NOT NULL,
    market_action_spec_id uuid NOT NULL,
    proof_receipt_id      uuid NOT NULL REFERENCES proof_receipt (id),   -- window_start anchor
    validation_test_id    uuid NOT NULL,
    window_start_at       timestamptz NOT NULL,   -- = the VERIFIED MARKET_ACTION proof time
    window_end_at         timestamptz NOT NULL,   -- = window_start_at + max_duration_days
    completion_type       text NOT NULL CHECK (completion_type IN ('NO_RESPONSE')),
    completion_hash       text NOT NULL,          -- kernel-derived over exact provenance + window
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (market_action_spec_id, completion_type),  -- one deterministic completion per action
    UNIQUE (id, venture_id),                          -- composite-FK target
    FOREIGN KEY (market_action_spec_id, venture_id)
        REFERENCES market_action_spec (id, venture_id),
    FOREIGN KEY (validation_test_id, venture_id)
        REFERENCES validation_test (id, venture_id)
);
CREATE TRIGGER market_window_completion_no_update
    BEFORE UPDATE ON market_window_completion FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER market_window_completion_no_delete
    BEFORE DELETE ON market_window_completion FOR EACH ROW EXECUTE FUNCTION append_only_guard();

CREATE TABLE recommendation_market_window_completion (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id    uuid NOT NULL,
    window_completion_id uuid NOT NULL,
    venture_id           uuid NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (recommendation_id, window_completion_id),
    FOREIGN KEY (recommendation_id, venture_id)
        REFERENCES next_action_recommendation (id, venture_id),
    FOREIGN KEY (window_completion_id, venture_id)
        REFERENCES market_window_completion (id, venture_id)
);
CREATE TRIGGER recommendation_market_window_completion_no_update
    BEFORE UPDATE ON recommendation_market_window_completion FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER recommendation_market_window_completion_no_delete
    BEFORE DELETE ON recommendation_market_window_completion FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- Unplanned human intervention ONLY (predefined approval lives in `approval`, not here).
CREATE TABLE alpha_intervention (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id               uuid NOT NULL REFERENCES venture (id),
    intervention_kind        text NOT NULL CHECK (intervention_kind IN
                               ('REASONING_CORRECTION', 'CODE_REPAIR', 'DEPLOYMENT_REPAIR',
                                'PROVIDER_REPAIR', 'OUTCOME_TRANSCRIPTION')),
    intervention_stage       text NOT NULL,
    related_action_request_id uuid,
    reason                   text,
    occurred_at              timestamptz NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (related_action_request_id, venture_id)
        REFERENCES action_request (id, venture_id)
);
CREATE TRIGGER alpha_intervention_no_update
    BEFORE UPDATE ON alpha_intervention FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER alpha_intervention_no_delete
    BEFORE DELETE ON alpha_intervention FOR EACH ROW EXECUTE FUNCTION append_only_guard();
