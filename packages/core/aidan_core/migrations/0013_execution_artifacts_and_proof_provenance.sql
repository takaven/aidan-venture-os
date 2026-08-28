-- Gate 4 / Slice 2 — durable execution artifacts + Proof Receipt provenance.
--
-- execution_artifact preserves machine-inspectable references/hashes to
-- worker-produced outputs. Artifacts are output provenance, NOT success:
-- artifact existence (or a worker-declared hash) never implies verification.
-- Deterministic verification, expressed through the EXISTING canonical
-- proof_receipt, remains the only consequential verification receipt; this
-- migration adds no second verification-truth table. proof_receipt gains an
-- optional execution_attempt provenance link so a consequential success can name
-- the attempt whose machine verification produced it, and so a proof cannot cite
-- another action's attempt.
--
-- Builds on immutable 0001-0012. Forward-only: never edit after use.
-- Scope: NO retry/timeout/failure-taxonomy fields, NO scheduler/queue/job table,
-- NO recovery state, NO change to execution_spec or migration 0012.

-- Composite-FK target so artifacts/proofs can be pinned to (attempt, action).
ALTER TABLE execution_attempt
    ADD CONSTRAINT execution_attempt_id_action_uk UNIQUE (id, action_request_id);

-- ==========================================================================
-- Execution artifact (append-only worker output provenance)
-- ==========================================================================
CREATE TABLE execution_artifact (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id           uuid NOT NULL,
    action_request_id    uuid NOT NULL,
    execution_attempt_id uuid NOT NULL,
    artifact_key         text NOT NULL,
    artifact_type        text NOT NULL CHECK (artifact_type IN
                           ('STRUCTURED_RESULT', 'FILE', 'PATCH', 'TEST_REPORT', 'LOG_REFERENCE', 'OTHER')),
    artifact_ref         text NOT NULL,
    content_hash         text NOT NULL,           -- computed by the kernel, never trusted from the worker
    size_bytes           bigint CHECK (size_bytes IS NULL OR size_bytes >= 0),
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (execution_attempt_id, artifact_key),  -- one identity per attempt (idempotency)
    -- artifact belongs to exactly one attempt of exactly this action:
    FOREIGN KEY (execution_attempt_id, action_request_id)
        REFERENCES execution_attempt (id, action_request_id),
    -- and the action is venture-consistent:
    FOREIGN KEY (action_request_id, venture_id) REFERENCES action_request (id, venture_id)
);

CREATE TRIGGER execution_artifact_no_update
    BEFORE UPDATE ON execution_artifact FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER execution_artifact_no_delete
    BEFORE DELETE ON execution_artifact FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER execution_artifact_no_truncate
    BEFORE TRUNCATE ON execution_artifact FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard();

CREATE INDEX execution_artifact_attempt_idx ON execution_artifact (execution_attempt_id);

-- ==========================================================================
-- Proof Receipt provenance: which execution attempt produced this verification?
-- ==========================================================================
ALTER TABLE proof_receipt
    ADD COLUMN execution_attempt_id uuid;

-- A proof may only cite an attempt of its OWN action (venture/action integrity).
ALTER TABLE proof_receipt
    ADD CONSTRAINT proof_receipt_attempt_action_fk
        FOREIGN KEY (execution_attempt_id, action_request_id)
        REFERENCES execution_attempt (id, action_request_id);
