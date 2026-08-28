-- Gate 6 / Slice 1 — immutable release authority + canonical deployment binding.
--
-- Establishes the authority boundary between a Gate-5 quality-passed candidate and
-- any deployment execution. A Gate-5 overall PASS is NOT deployment authority: before
-- a deploy worker may run, the exact quality-qualified candidate and the exact
-- deployment intent must be FROZEN in an immutable release_candidate (1:1 with the
-- deploy ActionRequest), bound to a venture-owned deployment_target, and the generic
-- Gate-4 execution_spec must bind that release (enforced in factory.spec for ANY caller).
--
-- release_hash binds the COMPLETE immutable release intent (candidate identity + target
-- identity/environment + normalized release contract), so a changed target/config can
-- never masquerade as the same release.
--
-- Scope: deployment_target + release_candidate + the DEPLOY_CANDIDATE capability only.
-- NO deployment_record, NO deployment/proof/lifecycle state, NO external provider.
-- Builds on immutable 0001-0017. Forward-only: never edit after use.

-- Extend the execution_spec capability vocabulary (0012's inline CHECK) with the
-- single capability this slice needs. Forward-only: drop + re-add the auto-named
-- constraint; migration 0012 is not edited.
ALTER TABLE execution_spec DROP CONSTRAINT IF EXISTS execution_spec_capability_scope_check;
ALTER TABLE execution_spec ADD CONSTRAINT execution_spec_capability_scope_check
    CHECK (capability_scope <@ ARRAY[
        'READ_REPOSITORY', 'WRITE_ISOLATED_WORKSPACE', 'RUN_TESTS',
        'PRODUCE_PATCH', 'READ_DECLARED_INPUTS', 'DEPLOY_CANDIDATE']::text[]);

-- ==========================================================================
-- Immutable, venture-owned deployment target identity (no credentials)
-- ==========================================================================
-- Identity only: an opaque target_ref and a provider-neutral provider_kind string.
-- It stores NO token/key/secret. Provider names are adapter identity, not architecture.
CREATE TABLE deployment_target (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id    uuid NOT NULL REFERENCES venture(id),
    environment   text NOT NULL,
    provider_kind text NOT NULL,
    target_ref    text NOT NULL,
    provenance    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venture_id, environment),   -- one canonical target per (venture, environment)
    UNIQUE (target_ref),                -- a target backs one canonical identity
    UNIQUE (id, venture_id)             -- composite-FK target
);
CREATE TRIGGER deployment_target_no_update
    BEFORE UPDATE ON deployment_target FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER deployment_target_no_delete
    BEFORE DELETE ON deployment_target FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- ==========================================================================
-- Immutable release candidate (1:1 with the deploy action; quality-qualified)
-- ==========================================================================
CREATE TABLE release_candidate (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id           uuid NOT NULL,
    action_request_id    uuid NOT NULL,
    build_manifest_id    uuid NOT NULL,
    build_spec_id        uuid NOT NULL,
    deployment_target_id uuid NOT NULL,
    candidate_tree_hash  text NOT NULL,                        -- exact candidate identity (from build_manifest)
    release_contract     jsonb NOT NULL DEFAULT '{}'::jsonb,   -- normalized deployment-relevant intent
    release_hash         text NOT NULL,                        -- identity of the COMPLETE release intent
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (action_request_id),                                -- one release per deploy ActionRequest
    UNIQUE (id, venture_id),                                   -- composite-FK target
    FOREIGN KEY (action_request_id, venture_id)
        REFERENCES action_request (id, venture_id),
    FOREIGN KEY (build_manifest_id, venture_id)
        REFERENCES build_manifest (id, venture_id),
    FOREIGN KEY (build_spec_id, venture_id)
        REFERENCES build_spec (id, venture_id),
    FOREIGN KEY (deployment_target_id, venture_id)
        REFERENCES deployment_target (id, venture_id)
);
CREATE TRIGGER release_candidate_no_update
    BEFORE UPDATE ON release_candidate FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER release_candidate_no_delete
    BEFORE DELETE ON release_candidate FOR EACH ROW EXECUTE FUNCTION append_only_guard();
