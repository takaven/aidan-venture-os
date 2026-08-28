-- Gate 4 / Slice 1 — immutable execution specification + DB-enforced success gate.
--
-- execution_spec freezes EXACTLY what executable work, capability boundary and
-- future verification contract are bound to a governed ActionRequest. It is
-- immutable and 1:1 with action_request: task content cannot drift after the
-- authorization used for dispatch is obtained. It is downstream executable
-- detail — NOT an investment decision, a second action, a lifecycle state, or
-- authorization by itself. Authorization that predates a spec cannot authorize
-- that spec's dispatch (enforced in the Gate 4 runtime, which re-authorizes
-- against the frozen spec).
--
-- This migration also closes the carried Gate 1/Gate 3 gap: a raw
-- `UPDATE action_request SET status='SUCCEEDED'` can no longer bypass proof
-- gating. The canonical success path is unaffected because it inserts/derives a
-- VERIFIED proof_receipt before transitioning, in the same transaction.
--
-- Builds on immutable 0001-0011. Forward-only: never edit after use.
-- Scope: NO failure taxonomy, NO artifacts, NO verifier-result table, NO retry
-- fields, NO second run/job table (those are later Gate 4 slices).

-- ==========================================================================
-- Immutable execution specification (1:1 with action_request; venture-consistent)
-- ==========================================================================
CREATE TABLE execution_spec (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    action_request_id        uuid NOT NULL,
    venture_id               uuid NOT NULL,
    worker_kind              text NOT NULL,                 -- provider-neutral; a registry key, not architecture
    task_payload             jsonb NOT NULL DEFAULT '{}'::jsonb,
    task_hash                text NOT NULL,
    expected_output_contract jsonb NOT NULL DEFAULT '{}'::jsonb,
    verifier_kind            text NOT NULL,                 -- used by a later slice; immutable here
    timeout_seconds          integer NOT NULL CHECK (timeout_seconds > 0),
    max_attempts             integer NOT NULL CHECK (max_attempts >= 1),
    capability_scope         text[] NOT NULL DEFAULT '{}'
                               CHECK (capability_scope <@ ARRAY[
                                   'READ_REPOSITORY', 'WRITE_ISOLATED_WORKSPACE', 'RUN_TESTS',
                                   'PRODUCE_PATCH', 'READ_DECLARED_INPUTS']::text[]),
    spec_hash                text NOT NULL,                 -- deterministic identity of the executable authority
    created_at               timestamptz NOT NULL DEFAULT now(),
    UNIQUE (action_request_id),                              -- one spec per ActionRequest
    UNIQUE (id, venture_id),                                 -- composite-FK target
    FOREIGN KEY (action_request_id, venture_id) REFERENCES action_request (id, venture_id)
);

-- Fully immutable: no field may change, and rows cannot be deleted.
CREATE TRIGGER execution_spec_no_update
    BEFORE UPDATE ON execution_spec FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER execution_spec_no_delete
    BEFORE DELETE ON execution_spec FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- ==========================================================================
-- Canonical SUCCESS is impossible without a VERIFIED Proof Receipt (DB-enforced)
-- ==========================================================================
CREATE FUNCTION action_request_success_guard() RETURNS trigger AS $$
BEGIN
    IF NEW.status = 'SUCCEEDED' AND OLD.status IS DISTINCT FROM 'SUCCEEDED' THEN
        IF NOT EXISTS (
            SELECT 1 FROM proof_receipt
            WHERE action_request_id = NEW.id AND result = 'VERIFIED'
        ) THEN
            RAISE EXCEPTION
              'action_request % cannot transition to SUCCEEDED without a VERIFIED proof receipt', NEW.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER action_request_success_requires_proof
    BEFORE UPDATE ON action_request FOR EACH ROW EXECUTE FUNCTION action_request_success_guard();
