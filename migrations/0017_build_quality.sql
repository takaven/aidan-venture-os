-- Gate 5 / Slice 3 — venture product-quality gates (Product, Experience,
-- Commercial, AntiGeneric) as canonical, per-dimension, evidence-led verdicts.
--
-- Technical quality already lives in Slice 2 (build_technical_check) and is REUSED,
-- not duplicated. Slice 3 adds the four remaining dimensions and keeps them
-- DISTINCT: there is no scalar/weighted score and no compensation between
-- dimensions — overall quality is derived as (Technical AND Product AND Experience
-- AND Commercial AND AntiGeneric) all PASS, computed in the kernel, never stored as
-- an independent caller-supplied truth.
--
-- Two append-only tables separate EVIDENCE from DECISION:
--   * build_quality_evidence  — deterministic observations / structural facts /
--     bounded reviewer observations, per (manifest, dimension, criterion). A model
--     reviewer may only contribute REVIEW_OBSERVATION evidence; it can never write a
--     verdict.
--   * build_quality_assessment — the canonical per-dimension PASS/FAIL, derived by
--     the kernel from that evidence.
--
-- Scope: NO deployment, NO lifecycle change, NO customer/revenue/market facts, NO
-- quality score, NO live model/provider. Builds on immutable 0001-0016. Forward-only.

-- ==========================================================================
-- Append-only quality evidence (separate from the decision)
-- ==========================================================================
CREATE TABLE build_quality_evidence (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id        uuid NOT NULL,
    build_manifest_id uuid NOT NULL,
    dimension         text NOT NULL CHECK (dimension IN ('PRODUCT', 'EXPERIENCE', 'COMMERCIAL', 'ANTIGENERIC')),
    criterion         text NOT NULL,
    source_type       text NOT NULL CHECK (source_type IN
                        ('KERNEL_DERIVED', 'TEST_RESULT', 'STRUCTURAL_MANIFEST', 'REVIEW_OBSERVATION')),
    evidence_payload  jsonb NOT NULL DEFAULT '{}'::jsonb,
    evidence_hash     text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (build_manifest_id, dimension, criterion),
    FOREIGN KEY (build_manifest_id, venture_id) REFERENCES build_manifest (id, venture_id)
);
CREATE TRIGGER build_quality_evidence_no_update
    BEFORE UPDATE ON build_quality_evidence FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER build_quality_evidence_no_delete
    BEFORE DELETE ON build_quality_evidence FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- ==========================================================================
-- Canonical per-dimension verdict (kernel-derived; one per (manifest, dimension))
-- ==========================================================================
CREATE TABLE build_quality_assessment (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id          uuid NOT NULL,
    build_manifest_id   uuid NOT NULL,
    dimension           text NOT NULL CHECK (dimension IN ('PRODUCT', 'EXPERIENCE', 'COMMERCIAL', 'ANTIGENERIC')),
    verdict             text NOT NULL CHECK (verdict IN ('PASS', 'FAIL')),
    decision_basis_hash text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (build_manifest_id, dimension),                     -- one canonical verdict per dimension
    FOREIGN KEY (build_manifest_id, venture_id) REFERENCES build_manifest (id, venture_id)
);
CREATE TRIGGER build_quality_assessment_no_update
    BEFORE UPDATE ON build_quality_assessment FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER build_quality_assessment_no_delete
    BEFORE DELETE ON build_quality_assessment FOR EACH ROW EXECUTE FUNCTION append_only_guard();
