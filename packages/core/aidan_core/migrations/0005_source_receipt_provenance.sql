-- Gate 2 / Slice 1 — Source Receipt provenance.
--
-- Extends the Gate 1 canonical evidence envelope (evidence_record) with a
-- SOURCE-specific subtype. evidence_record remains the canonical evidence
-- identity and the authoritative holder of content_hash; source_receipt holds
-- only SOURCE-specific provenance. No competing evidence store is introduced.
--
-- Builds on immutable 0001-0004. Forward-only: never edit after use.

-- Composite-uniqueness target so the source_receipt envelope FK can enforce, in
-- one declaration: parent existence, venture agreement, and parent kind=SOURCE.
-- (id is already the PK; this wider unique is only an FK target.)
ALTER TABLE evidence_record
    ADD CONSTRAINT evidence_record_identity_uk UNIQUE (id, venture_id, kind);

-- --------------------------------------------------------------------------
-- Source Receipt (append-only SOURCE subtype of evidence_record, 1:1)
-- --------------------------------------------------------------------------
CREATE TABLE source_receipt (
    evidence_record_id     uuid PRIMARY KEY,          -- 1:1 with the SOURCE evidence_record
    venture_id             uuid NOT NULL,
    source_kind            text NOT NULL DEFAULT 'SOURCE' CHECK (source_kind = 'SOURCE'),
    locator                text NOT NULL,             -- authoritative locator for SOURCE evidence
    source_type            text NOT NULL
                             CHECK (source_type IN ('WEB_PAGE', 'DOCUMENT', 'DATASET', 'API_RESPONSE', 'OTHER')),
    retrieved_at           timestamptz NOT NULL,      -- retrieval provenance (required)
    retrieved_by           text NOT NULL,             -- acquisition adapter identity (required)
    acquisition_key        text NOT NULL,             -- idempotency key for the acquisition operation
    published_at           timestamptz,               -- source publication time, where known
    publication_time_known boolean NOT NULL DEFAULT false,
    excerpt                text,                       -- bounded selected excerpt, where permissible
    snapshot_ref           text,                       -- external snapshot/artifact reference, where permissible
    reliability_code       text
                             CHECK (reliability_code IS NULL OR reliability_code IN
                               ('PRIMARY', 'SECONDARY', 'AUTHORITATIVE', 'ANECDOTAL',
                                'DIRECT_MEASUREMENT', 'COMMENTARY', 'UNKNOWN')),
    metadata               jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at             timestamptz NOT NULL DEFAULT now(),

    -- Acquisition idempotency: a given acquisition operation collapses to one
    -- receipt. This is NOT locator+content uniqueness — later retrievals under a
    -- new acquisition key are legitimate new provenance events.
    UNIQUE (venture_id, acquisition_key),

    -- Envelope: parent exists, venture agrees, and parent kind is SOURCE.
    FOREIGN KEY (evidence_record_id, venture_id, source_kind)
        REFERENCES evidence_record (id, venture_id, kind)
);

CREATE TRIGGER source_receipt_no_update
    BEFORE UPDATE ON source_receipt FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER source_receipt_no_delete
    BEFORE DELETE ON source_receipt FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER source_receipt_no_truncate
    BEFORE TRUNCATE ON source_receipt FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard();
