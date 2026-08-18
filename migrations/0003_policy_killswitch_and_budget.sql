-- Gate 1 / Slice 3 — deterministic governance: policy, kill switch, budget.
--
-- Builds on immutable 0001 and 0002. Adds: policy_decision (append-only),
-- kill_switch (stateful, audited), budget_account (DB-enforced invariants),
-- capital_entry (append-only ledger), and the small action_request additions
-- needed to make policy inputs canonical.
--
-- Scope excludes approvals, execution, proof receipts, evidence, reconciliation.
-- Forward-only: once applied, this file must never be edited.

-- --------------------------------------------------------------------------
-- action_request additions (canonical policy/budget inputs)
-- --------------------------------------------------------------------------
ALTER TABLE action_request
    ADD COLUMN required_autonomy  smallint      NOT NULL DEFAULT 0,
    ADD COLUMN requested_amount   numeric(20,4) NOT NULL DEFAULT 0
        CHECK (requested_amount >= 0),
    ADD COLUMN requested_currency text          NOT NULL DEFAULT 'USD';

-- Needed as the target of a composite FK (entry venture must match action venture).
ALTER TABLE action_request
    ADD CONSTRAINT action_request_id_venture_uk UNIQUE (id, venture_id);

-- --------------------------------------------------------------------------
-- Policy decision (append-only; the only writer is deterministic evaluation)
-- --------------------------------------------------------------------------
CREATE TYPE policy_decision_kind AS ENUM ('ALLOW', 'DENY', 'REQUIRE_APPROVAL');

CREATE TABLE policy_decision (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    action_request_id uuid NOT NULL REFERENCES action_request(id),
    decision          policy_decision_kind NOT NULL,
    rule_id           text NOT NULL,
    rule_version      text NOT NULL,
    inputs_hash       text NOT NULL,
    inputs            jsonb NOT NULL DEFAULT '{}'::jsonb,
    reason            text NOT NULL,
    evaluated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER policy_decision_no_update
    BEFORE UPDATE ON policy_decision
    FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER policy_decision_no_delete
    BEFORE DELETE ON policy_decision
    FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER policy_decision_no_truncate
    BEFORE TRUNCATE ON policy_decision
    FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard();

-- --------------------------------------------------------------------------
-- Kill switch (GLOBAL singleton + at most one per venture)
-- --------------------------------------------------------------------------
CREATE TABLE kill_switch (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope       text NOT NULL CHECK (scope IN ('GLOBAL', 'VENTURE')),
    venture_id  uuid REFERENCES venture(id),
    active      boolean NOT NULL DEFAULT true,
    engaged_by  text NOT NULL,
    reason      text,
    engaged_at  timestamptz NOT NULL DEFAULT now(),
    released_by text,
    released_at timestamptz,
    CHECK (
        (scope = 'GLOBAL'  AND venture_id IS NULL) OR
        (scope = 'VENTURE' AND venture_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX kill_switch_global_uk  ON kill_switch (scope)      WHERE scope = 'GLOBAL';
CREATE UNIQUE INDEX kill_switch_venture_uk ON kill_switch (venture_id) WHERE scope = 'VENTURE';

-- --------------------------------------------------------------------------
-- Budget account (fixed-precision; DB-enforced invariants)
-- --------------------------------------------------------------------------
CREATE TABLE budget_account (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id      uuid NOT NULL REFERENCES venture(id),
    currency        text NOT NULL,
    granted_amount  numeric(20,4) NOT NULL DEFAULT 0 CHECK (granted_amount  >= 0),
    reserved_amount numeric(20,4) NOT NULL DEFAULT 0 CHECK (reserved_amount >= 0),
    committed_amount numeric(20,4) NOT NULL DEFAULT 0 CHECK (committed_amount >= 0),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venture_id, currency),
    UNIQUE (id, currency),      -- composite FK target: entry currency consistency
    UNIQUE (id, venture_id),    -- composite FK target: entry venture consistency
    CHECK (reserved_amount + committed_amount <= granted_amount)
);

-- --------------------------------------------------------------------------
-- Capital ledger (append-only). Per-action RESERVE/RELEASE/COMMIT are unique.
-- --------------------------------------------------------------------------
CREATE TABLE capital_entry (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venture_id        uuid NOT NULL,
    budget_account_id uuid NOT NULL,
    action_request_id uuid,
    entry_type        text NOT NULL CHECK (entry_type IN ('GRANT', 'RESERVE', 'RELEASE', 'COMMIT')),
    amount            numeric(20,4) NOT NULL CHECK (amount >= 0),
    currency          text NOT NULL,
    reference         text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    -- currency + venture consistency with the account (composite FKs):
    FOREIGN KEY (budget_account_id, currency)   REFERENCES budget_account(id, currency),
    FOREIGN KEY (budget_account_id, venture_id) REFERENCES budget_account(id, venture_id),
    -- venture consistency with the action (skipped when action_request_id IS NULL):
    FOREIGN KEY (action_request_id, venture_id) REFERENCES action_request(id, venture_id)
);

-- One RESERVE, one RELEASE, one COMMIT per action (idempotency at DB level).
CREATE UNIQUE INDEX capital_entry_reserve_uk ON capital_entry (action_request_id) WHERE entry_type = 'RESERVE';
CREATE UNIQUE INDEX capital_entry_release_uk ON capital_entry (action_request_id) WHERE entry_type = 'RELEASE';
CREATE UNIQUE INDEX capital_entry_commit_uk  ON capital_entry (action_request_id) WHERE entry_type = 'COMMIT';

CREATE TRIGGER capital_entry_no_update
    BEFORE UPDATE ON capital_entry
    FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER capital_entry_no_delete
    BEFORE DELETE ON capital_entry
    FOR EACH ROW EXECUTE FUNCTION append_only_guard();
CREATE TRIGGER capital_entry_no_truncate
    BEFORE TRUNCATE ON capital_entry
    FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard();
