-- Gate 8 / Slice 4 — durable REAL vs SIMULATED evidence origin.
--
-- Whether a consequential MARKET_ACTION proof was verified against a LIVE provider transport or
-- a deterministic fixture is not otherwise reconstructable after the process exits (an in-memory
-- object type is insufficient). external_evidence_origin binds the exact VERIFIED proof (and its
-- attempt) to a trusted origin. It is written by TRUSTED code from the transport's own declared
-- origin_kind — never from a caller flag or worker output — so a fixture cannot self-promote to
-- REAL_PROVIDER, and a REAL closed loop can require a REAL_PROVIDER anchor. It says nothing about
-- commercial success; REAL_PROVIDER means only "provider-backed evidence path".
--
-- Scope: this one entity. NO closed_loop_run, NO workflow/run engine, NO credential store, NO
-- provider-account state, NO market/autonomy score. Builds on immutable 0001-0023. Forward-only.
CREATE TABLE external_evidence_origin (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id           uuid NOT NULL REFERENCES venture (id),
    proof_receipt_id     uuid NOT NULL REFERENCES proof_receipt (id),
    execution_attempt_id uuid REFERENCES execution_attempt (id),
    origin_kind          text NOT NULL CHECK (origin_kind IN ('REAL_PROVIDER', 'SIMULATED')),
    provider_kind        text NOT NULL,           -- e.g. 'postmark' / 'local' (provenance only)
    source_instance_ref  text NOT NULL,
    origin_hash          text NOT NULL,           -- kernel-derived over exact provenance
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (proof_receipt_id)                      -- exactly one origin per consequential proof
);
CREATE TRIGGER external_evidence_origin_no_update
    BEFORE UPDATE ON external_evidence_origin FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER external_evidence_origin_no_delete
    BEFORE DELETE ON external_evidence_origin FOR EACH ROW EXECUTE FUNCTION append_only_guard();
