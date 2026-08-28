-- Gate 1 / Slice 1 — PostgreSQL canonical foundation.
--
-- Scope: canonical vocabularies (as three distinct ENUM types so status
-- concepts cannot collapse) and the append-only audit/event primitive.
-- No Venture, ActionRequest, Policy, Approval, Budget, ProofReceipt or
-- KillSwitch tables are created in this slice.
--
-- Forward-only: once applied, this file must never be edited (the migration
-- runner enforces this via checksum locking).

-- --------------------------------------------------------------------------
-- Canonical vocabularies (three separate types == separation enforced by DB)
-- --------------------------------------------------------------------------
CREATE TYPE lifecycle_state AS ENUM (
    'DISCOVERED',
    'VALIDATING',
    'BUILDING',
    'OPERATING',
    'ARCHIVED'
);

CREATE TYPE run_status AS ENUM (
    'PENDING',
    'RUNNING',
    'SUCCEEDED',
    'FAILED'
);

CREATE TYPE investment_decision AS ENUM (
    'VALIDATE',
    'BUILD',
    'IMPROVE',
    'MARKET',
    'SCALE',
    'HOLD',
    'KILL',
    'DO_NOTHING'
);

-- --------------------------------------------------------------------------
-- Append-only audit/event primitive
-- --------------------------------------------------------------------------
CREATE TABLE audit_event (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type  text        NOT NULL,
    actor       text        NOT NULL,
    venture_id  uuid,            -- nullable; venture table arrives in a later slice
    action_id   uuid,            -- nullable; action table arrives in a later slice
    payload     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now()
);

-- Append-only enforcement: reject UPDATE, DELETE and TRUNCATE at the DB level.
CREATE FUNCTION audit_event_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_event_no_update
    BEFORE UPDATE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION audit_event_immutable();

CREATE TRIGGER audit_event_no_delete
    BEFORE DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION audit_event_immutable();

CREATE TRIGGER audit_event_no_truncate
    BEFORE TRUNCATE ON audit_event
    FOR EACH STATEMENT EXECUTE FUNCTION audit_event_immutable();
