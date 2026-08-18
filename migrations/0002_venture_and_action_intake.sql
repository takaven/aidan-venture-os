-- Gate 1 / Slice 2 — canonical venture & action intake primitives.
--
-- Builds on immutable 0001 (which defines the lifecycle_state, run_status and
-- investment_decision ENUM types and the append-only audit_event table).
-- Adds: venture, append-only venture_mandate_version, action_request intake
-- (PostgreSQL-enforced idempotency), and append-only investment_decision.
--
-- Scope excludes Policy, Approval, Budget, KillSwitch, Execution, ProofReceipt.
-- Forward-only: once applied, this file must never be edited.

-- Generic append-only guard (reused by immutable tables below).
CREATE FUNCTION append_only_guard() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not permitted', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

-- --------------------------------------------------------------------------
-- Venture (mutable canonical state; lifecycle changes go through a guarded path)
-- --------------------------------------------------------------------------
CREATE TABLE venture (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            text NOT NULL UNIQUE,
    lifecycle_state lifecycle_state NOT NULL DEFAULT 'DISCOVERED',
    mandate_version integer,             -- current designated mandate version (nullable)
    autonomy_level  smallint NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- --------------------------------------------------------------------------
-- Venture Mandate versions (append-only durable reference primitive)
-- --------------------------------------------------------------------------
CREATE TABLE venture_mandate_version (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id   uuid NOT NULL REFERENCES venture(id),
    version      integer NOT NULL,
    content_hash text NOT NULL,
    source_ref   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venture_id, version)
);

CREATE TRIGGER mandate_version_no_update
    BEFORE UPDATE ON venture_mandate_version
    FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER mandate_version_no_delete
    BEFORE DELETE ON venture_mandate_version
    FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER mandate_version_no_truncate
    BEFORE TRUNCATE ON venture_mandate_version
    FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard();

-- --------------------------------------------------------------------------
-- ActionRequest intake (PostgreSQL-enforced idempotency)
-- --------------------------------------------------------------------------
CREATE TABLE action_request (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id      uuid NOT NULL REFERENCES venture(id),
    action_type     text NOT NULL,
    actor           text NOT NULL,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    payload_hash    text NOT NULL,
    idempotency_key text NOT NULL,
    status          run_status NOT NULL DEFAULT 'PENDING',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venture_id, idempotency_key)
);

-- --------------------------------------------------------------------------
-- Investment decision (append-only; a decision, NOT a lifecycle state)
-- --------------------------------------------------------------------------
CREATE TABLE investment_decision_record (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id          uuid NOT NULL REFERENCES venture(id),
    decision            investment_decision NOT NULL,
    rationale_ref       text,
    resulting_action_id uuid REFERENCES action_request(id),
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER investment_decision_no_update
    BEFORE UPDATE ON investment_decision_record
    FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER investment_decision_no_delete
    BEFORE DELETE ON investment_decision_record
    FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER investment_decision_no_truncate
    BEFORE TRUNCATE ON investment_decision_record
    FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard();
