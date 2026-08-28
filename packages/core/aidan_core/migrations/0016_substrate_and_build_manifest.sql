-- Gate 5 / Slice 2 — minimal Venture Substrate identity + durable build manifest
-- + deterministic Technical build evidence.
--
-- substrate_release: the immutable identity of a reusable INFRASTRUCTURE substrate
-- release — a finite component set, the exact OS source commit (source_sha), and a
-- deterministic content hash over the actual component file hashes. It is provenance
-- identity, not a product template and not implementation stored in the DB.
--
-- build_manifest: the durable, attempt-bound record of the candidate product output
-- a builder produced (kernel-computed file hashes over the venture workspace). It is
-- provenance, NOT a quality verdict.
--
-- build_technical_check: deterministic per-check Technical evidence for a manifest
-- (PASS/FAIL per named check). The Technical verdict is DERIVED from the required
-- checks (no scalar score). This is narrowly-scoped build-Technical evidence — NOT
-- the Slice 3 multi-dimension quality store.
--
-- Scope: NO Product/Experience/Commercial/AntiGeneric quality, NO aggregate score,
-- NO deployment, NO lifecycle change, NO second proof/execution runtime.
-- Builds on immutable 0001-0015. Forward-only: never edit after use.

-- ==========================================================================
-- Immutable substrate release identity (infrastructure provenance, not a template)
-- ==========================================================================
CREATE TABLE substrate_release (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    release_key           text NOT NULL,
    source_repository_ref text NOT NULL,
    source_sha            text NOT NULL,
    components            jsonb NOT NULL DEFAULT '[]'::jsonb,   -- finite component vocabulary
    component_manifest    jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{component, path, sha256, size}]
    content_hash          text NOT NULL,                        -- deterministic release identity
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (release_key),
    UNIQUE (id)                                                 -- explicit FK target
);
CREATE TRIGGER substrate_release_no_update
    BEFORE UPDATE ON substrate_release FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER substrate_release_no_delete
    BEFORE DELETE ON substrate_release FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- ==========================================================================
-- Durable candidate build manifest (one per execution attempt; kernel-hashed)
-- ==========================================================================
CREATE TABLE build_manifest (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id            uuid NOT NULL,
    action_request_id     uuid NOT NULL,
    execution_attempt_id  uuid NOT NULL,
    build_spec_id         uuid NOT NULL,
    venture_repository_id uuid NOT NULL,
    substrate_release_id  uuid NOT NULL REFERENCES substrate_release (id),
    candidate_tree_hash   text NOT NULL,                        -- deterministic hash of the candidate tree
    file_manifest         jsonb NOT NULL DEFAULT '[]'::jsonb,   -- kernel-computed [{path, sha256, size, origin}]
    manifest_hash         text NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (execution_attempt_id),                              -- one manifest per attempt
    UNIQUE (id, venture_id),                                    -- composite-FK target
    FOREIGN KEY (action_request_id, venture_id)
        REFERENCES action_request (id, venture_id),
    FOREIGN KEY (execution_attempt_id, action_request_id)
        REFERENCES execution_attempt (id, action_request_id),
    FOREIGN KEY (build_spec_id, venture_id)
        REFERENCES build_spec (id, venture_id),
    FOREIGN KEY (venture_repository_id, venture_id)
        REFERENCES venture_repository (id, venture_id)
);
CREATE TRIGGER build_manifest_no_update
    BEFORE UPDATE ON build_manifest FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER build_manifest_no_delete
    BEFORE DELETE ON build_manifest FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- ==========================================================================
-- Deterministic Technical check evidence (per-check PASS/FAIL; verdict is derived)
-- ==========================================================================
CREATE TABLE build_technical_check (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    build_manifest_id uuid NOT NULL,
    venture_id        uuid NOT NULL,
    check_name        text NOT NULL,
    result            text NOT NULL CHECK (result IN ('PASS', 'FAIL')),
    detail            jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (build_manifest_id, check_name),                    -- one row per (manifest, check)
    FOREIGN KEY (build_manifest_id, venture_id)
        REFERENCES build_manifest (id, venture_id)
);
CREATE TRIGGER build_technical_check_no_update
    BEFORE UPDATE ON build_technical_check FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER build_technical_check_no_delete
    BEFORE DELETE ON build_technical_check FOR EACH ROW EXECUTE FUNCTION append_only_guard();
