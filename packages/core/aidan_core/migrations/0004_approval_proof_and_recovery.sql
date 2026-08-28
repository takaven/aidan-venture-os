-- Gate 1 / Slice 4 — approvals, evidence, execution, proof, recovery.
--
-- Builds on immutable 0001–0003. Completes the governed consequential-action
-- loop: durable approvals bound to an exact policy decision; an append-only
-- Evidence Ledger primitive; raw execution results (distinct from proof);
-- deterministic Proof Receipts; execution attempts with leases and safety
-- modes; and the run_status values needed for the execution state machine.
--
-- Forward-only: once applied, this file must never be edited. The new
-- run_status values are added here but only USED by application code in later
-- (committed) transactions — never within this migration's transaction.

-- --------------------------------------------------------------------------
-- Extend the canonical run status vocabulary (not used within this migration).
-- --------------------------------------------------------------------------
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'AWAITING_APPROVAL';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'AUTHORIZED';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'CANCELLED';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'RECOVERY_REQUIRED';

-- --------------------------------------------------------------------------
-- Approval (bound to the exact policy decision it approves)
-- --------------------------------------------------------------------------
CREATE TABLE approval (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    action_request_id  uuid NOT NULL REFERENCES action_request(id),
    policy_decision_id uuid NOT NULL REFERENCES policy_decision(id),
    state              text NOT NULL DEFAULT 'PENDING'
                         CHECK (state IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED')),
    bound_inputs_hash  text NOT NULL,
    requested_at       timestamptz NOT NULL DEFAULT now(),
    expires_at         timestamptz NOT NULL,
    decided_by         text,
    decided_at         timestamptz,
    reason             text
);

-- PENDING is the only mutable state; terminal approvals cannot change, nor be deleted.
CREATE FUNCTION approval_terminal_guard() RETURNS trigger AS $$
BEGIN
    IF OLD.state <> 'PENDING' THEN
        RAISE EXCEPTION 'approval % is terminal (%): no further changes', OLD.id, OLD.state;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER approval_no_terminal_update
    BEFORE UPDATE ON approval
    FOR EACH ROW EXECUTE FUNCTION approval_terminal_guard();
CREATE TRIGGER approval_no_delete
    BEFORE DELETE ON approval
    FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- --------------------------------------------------------------------------
-- Evidence Ledger primitive (append-only; interpretation is NOT evidence)
-- --------------------------------------------------------------------------
CREATE TABLE evidence_record (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id        uuid NOT NULL REFERENCES venture(id),
    action_request_id uuid REFERENCES action_request(id),
    kind              text NOT NULL CHECK (kind IN ('SOURCE', 'OBSERVATION', 'CLAIM')),
    source_ref        text,
    content_hash      text NOT NULL,
    payload           jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER evidence_no_update
    BEFORE UPDATE ON evidence_record FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER evidence_no_delete
    BEFORE DELETE ON evidence_record FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER evidence_no_truncate
    BEFORE TRUNCATE ON evidence_record FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard();

-- --------------------------------------------------------------------------
-- Execution attempt (stable key across retries; one active claim per action)
-- --------------------------------------------------------------------------
CREATE TABLE execution_attempt (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    action_request_id uuid NOT NULL REFERENCES action_request(id),
    execution_key     text NOT NULL,
    attempt_number    integer NOT NULL,
    executor_ref      text,
    safety_mode       text NOT NULL CHECK (safety_mode IN ('IDEMPOTENT', 'RECONCILABLE', 'UNSAFE')),
    lease_token       uuid NOT NULL DEFAULT gen_random_uuid(),
    lease_acquired_at timestamptz NOT NULL DEFAULT now(),
    lease_expires_at  timestamptz NOT NULL,
    status            text NOT NULL DEFAULT 'CLAIMED'
                        CHECK (status IN ('CLAIMED', 'COMPLETED', 'FAILED', 'ABANDONED', 'RECOVERY_REQUIRED')),
    external_ref      text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (execution_key, attempt_number)
);

-- At most one active (CLAIMED) attempt per action -> concurrency-safe claim.
CREATE UNIQUE INDEX execution_attempt_active_uk
    ON execution_attempt (action_request_id) WHERE status = 'CLAIMED';

-- --------------------------------------------------------------------------
-- Raw execution result (append-only; NOT canonical success). Deduped per action.
-- --------------------------------------------------------------------------
CREATE TABLE execution_result (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    action_request_id    uuid NOT NULL REFERENCES action_request(id),
    execution_attempt_id uuid REFERENCES execution_attempt(id),
    external_result_id   text NOT NULL,
    raw_payload          jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw_hash             text NOT NULL,
    reported_outcome     text NOT NULL,
    received_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (action_request_id, external_result_id)
);

CREATE TRIGGER execution_result_no_update
    BEFORE UPDATE ON execution_result FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER execution_result_no_delete
    BEFORE DELETE ON execution_result FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER execution_result_no_truncate
    BEFORE TRUNCATE ON execution_result FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard();

-- --------------------------------------------------------------------------
-- Proof receipt (append-only). At most one VERIFIED per action -> success once.
-- --------------------------------------------------------------------------
CREATE TABLE proof_receipt (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    action_request_id   uuid NOT NULL REFERENCES action_request(id),
    execution_result_id uuid REFERENCES execution_result(id),
    verification_type   text NOT NULL,
    verifier            text NOT NULL,
    result              text NOT NULL CHECK (result IN ('VERIFIED', 'FAILED')),
    evidence_hash       text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX proof_receipt_verified_uk
    ON proof_receipt (action_request_id) WHERE result = 'VERIFIED';

CREATE TRIGGER proof_no_update
    BEFORE UPDATE ON proof_receipt FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER proof_no_delete
    BEFORE DELETE ON proof_receipt FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER proof_no_truncate
    BEFORE TRUNCATE ON proof_receipt FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard();
