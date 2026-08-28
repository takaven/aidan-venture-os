-- Gate 7 / Slice 2 — externally-attributable market observation evidence.
--
-- market_observation preserves an externally-observed market OUTCOME separately from the
-- market-action Proof Receipt (which proves only that the exact authorized action occurred
-- in the controlled channel) and from interpretation (Slice 3). It is append-only external
-- evidence; the payload is untrusted DATA with no execution/policy/lifecycle/decision
-- authority.
--
-- Dedupe identity is SOURCE-SCOPED: provider event ids are unique only within an
-- account/inbox/channel instance, so the canonical key is
-- (venture_id, channel_kind, source_instance_ref, external_event_id). The same textual
-- external_event_id under a DIFFERENT source instance may legitimately coexist.
--
-- NO_RESPONSE is intentionally NOT an allowed external event type: absence is not an
-- external event; a Slice-3 deterministic derivation may conclude it after a frozen window.
--
-- Scope: market_observation only. NO interpretation, NO metrics, NO decision, NO CRM, NO
-- payment. Builds on immutable 0001-0019. Forward-only: never edit after use.
CREATE TABLE market_observation (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id            uuid NOT NULL,
    market_action_spec_id uuid NOT NULL,
    action_request_id     uuid NOT NULL,
    execution_attempt_id  uuid,
    channel_kind          text NOT NULL,
    source_instance_ref   text NOT NULL,
    external_event_id     text NOT NULL,
    observation_type      text NOT NULL CHECK (observation_type IN
                            ('DELIVERED', 'BOUNCED', 'OPENED', 'CLICKED', 'REPLIED', 'UNSUBSCRIBE')),
    occurred_at           timestamptz,
    source_action_ref     text,
    raw_evidence          jsonb NOT NULL DEFAULT '{}'::jsonb,
    evidence_hash         text NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    -- source-scoped external-event dedupe (NOT globally on channel_kind + external_event_id)
    UNIQUE (venture_id, channel_kind, source_instance_ref, external_event_id),
    FOREIGN KEY (market_action_spec_id, venture_id)
        REFERENCES market_action_spec (id, venture_id),
    FOREIGN KEY (action_request_id, venture_id)
        REFERENCES action_request (id, venture_id)
);
CREATE TRIGGER market_observation_no_update
    BEFORE UPDATE ON market_observation FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER market_observation_no_delete
    BEFORE DELETE ON market_observation FOR EACH ROW EXECUTE FUNCTION append_only_guard();
