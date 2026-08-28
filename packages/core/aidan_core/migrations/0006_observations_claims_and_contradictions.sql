-- Gate 2 / Slice 2 — Observations, Claims, and Claim<->Observation relations.
--
-- Continues the canonical evidence envelope pattern from Slice 1: every typed
-- record is a subtype of an evidence_record of the matching kind. Claim support
-- state is DERIVED from append-only SUPPORTS/CONTRADICTS relations, never stored
-- as caller-controlled truth. Contradictory evidence coexists and is never
-- overwritten.
--
-- Builds on immutable 0001-0005. Forward-only: never edit after use.
-- Reuses evidence_record_identity_uk (id, venture_id, kind) from 0005 as the
-- envelope composite-FK target.

-- Composite-FK target so an Observation can bind to an exact Source Receipt with
-- venture agreement (evidence_record_id is already the PK).
ALTER TABLE source_receipt
    ADD CONSTRAINT source_receipt_id_venture_uk UNIQUE (evidence_record_id, venture_id);

-- --------------------------------------------------------------------------
-- Observation (append-only OBSERVATION subtype; requires an exact Source Receipt)
-- --------------------------------------------------------------------------
CREATE TABLE observation (
    evidence_record_id uuid PRIMARY KEY,
    venture_id         uuid NOT NULL,
    observation_kind   text NOT NULL DEFAULT 'OBSERVATION' CHECK (observation_kind = 'OBSERVATION'),
    source_evidence_id uuid NOT NULL,          -- the SOURCE evidence_record it derives from
    source_locator     text,                    -- source-relative citation, where available
    statement          text NOT NULL,           -- bounded observed statement/value (not interpretation)
    excerpt            text,                     -- bounded retained excerpt, where permissible
    observation_key    text NOT NULL,           -- idempotency key for the observation operation
    created_at         timestamptz NOT NULL DEFAULT now(),

    UNIQUE (venture_id, observation_key),
    UNIQUE (evidence_record_id, venture_id),     -- composite-FK target for claim_evidence

    -- Envelope: parent exists, venture agrees, parent kind is OBSERVATION.
    FOREIGN KEY (evidence_record_id, venture_id, observation_kind)
        REFERENCES evidence_record (id, venture_id, kind),
    -- Provenance: an Observation cannot exist without an exact Source Receipt of
    -- the same venture.
    FOREIGN KEY (source_evidence_id, venture_id)
        REFERENCES source_receipt (evidence_record_id, venture_id)
);

CREATE INDEX observation_source_idx ON observation (source_evidence_id);

CREATE TRIGGER observation_no_update
    BEFORE UPDATE ON observation FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER observation_no_delete
    BEFORE DELETE ON observation FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER observation_no_truncate
    BEFORE TRUNCATE ON observation FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard();

-- --------------------------------------------------------------------------
-- Claim (append-only CLAIM subtype; a proposition, NOT truth)
-- --------------------------------------------------------------------------
CREATE TABLE claim (
    evidence_record_id uuid PRIMARY KEY,
    venture_id         uuid NOT NULL,
    claim_kind         text NOT NULL DEFAULT 'CLAIM' CHECK (claim_kind = 'CLAIM'),
    statement          text NOT NULL,
    claim_key          text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),

    UNIQUE (venture_id, claim_key),
    UNIQUE (evidence_record_id, venture_id),     -- composite-FK target for claim_evidence

    FOREIGN KEY (evidence_record_id, venture_id, claim_kind)
        REFERENCES evidence_record (id, venture_id, kind)
);

CREATE TRIGGER claim_no_update
    BEFORE UPDATE ON claim FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER claim_no_delete
    BEFORE DELETE ON claim FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER claim_no_truncate
    BEFORE TRUNCATE ON claim FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard();

-- --------------------------------------------------------------------------
-- Claim <-> Observation relations (append-only; contradictions coexist)
-- --------------------------------------------------------------------------
CREATE TABLE claim_evidence (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id       uuid NOT NULL,
    observation_id uuid NOT NULL,
    venture_id     uuid NOT NULL,
    stance         text NOT NULL CHECK (stance IN ('SUPPORTS', 'CONTRADICTS')),
    reason         text,
    created_at     timestamptz NOT NULL DEFAULT now(),

    -- One stance per (claim, observation) pair: exact retry is idempotent and an
    -- opposite-stance re-assertion is a deterministic conflict (never a replace).
    UNIQUE (claim_id, observation_id),

    -- Claim side resolves to a CLAIM envelope of the same venture.
    FOREIGN KEY (claim_id, venture_id) REFERENCES claim (evidence_record_id, venture_id),
    -- Observation side resolves to an OBSERVATION envelope of the same venture.
    FOREIGN KEY (observation_id, venture_id) REFERENCES observation (evidence_record_id, venture_id)
);

CREATE INDEX claim_evidence_claim_idx ON claim_evidence (claim_id);

CREATE TRIGGER claim_evidence_no_update
    BEFORE UPDATE ON claim_evidence FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER claim_evidence_no_delete
    BEFORE DELETE ON claim_evidence FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER claim_evidence_no_truncate
    BEFORE TRUNCATE ON claim_evidence FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard();
