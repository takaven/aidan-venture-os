-- Gate 4 / Slice 3 — bounded retry / recovery: durable attempt failure classification.
--
-- Retry is a NEW execution_attempt against the SAME immutable execution_spec; the
-- retry model reuses the existing attempt/lease/recovery primitives and the
-- immutable ``execution_spec.max_attempts``. The only durable state this slice
-- adds is a deterministic per-attempt failure classification, so a fresh process
-- can reason about what failed and whether another authorized attempt is allowed.
--
-- ActionRequest terminal FAILED vs. retry-eligible is a run_status transition
-- (RUNNING -> PENDING for a retryable attempt failure, RUNNING -> FAILED at
-- exhaustion / non-retryable), enforced in the kernel — NOT a new state machine.
-- No scheduler/queue/job table, no second run layer, no lease redesign.
--
-- Builds on immutable 0001-0013. Forward-only: never edit after use.

ALTER TABLE execution_attempt
    ADD COLUMN failure_class text
        CHECK (failure_class IS NULL OR failure_class IN
               ('WORKER_ERROR', 'TIMEOUT', 'VERIFICATION_FAILED', 'POLICY_REVOKED',
                'KILLED', 'RETRY_EXHAUSTED', 'RECOVERY_REQUIRED', 'INTERNAL_ERROR')),
    ADD COLUMN failure_detail jsonb,
    ADD COLUMN finished_at timestamptz;
