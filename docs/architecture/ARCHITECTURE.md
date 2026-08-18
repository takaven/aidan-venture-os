# Frozen Architecture

AI-DAN Venture OS is a governed venture allocator/operator. The fundamental unit is the highest-value next action, not BUILD.

```text
Board / Venture Mandate
        ↓
AIDAN
        ↓
Policy / Capital / Evidence
        ↓
Factory Execution Runtime
        ↓
Replaceable Specialist Workers
        ↓
Verification / Proof Receipts
        ↓
Market + Operate Runtime
        ↓
Outcomes / Portfolio Learning
```

## Four planes

- **Governance:** Venture Mandate, policy, approvals, autonomy, capital controls and permitted state transitions.
- **Intelligence:** AIDAN opportunity discovery, research synthesis, Kill Case, assumptions, experiments, investment decisions and next-best-action allocation. AIDAN does not write application code.
- **Execution:** durable Factory runtime, replaceable specialist workers, product/market work, QA, deployment, rollback and operations.
- **Truth:** Evidence Ledger, Capital Ledger, Audit Ledger, canonical venture state, experiments, outcomes, costs and machine-verifiable Proof Receipts.

## Locked execution doctrine

Consequential actions follow:

`ActionRequest → Policy → Approval if required → Execution → Proof Receipt → permitted canonical transition`.

Generated prose and worker self-report do not establish consequential success. Deterministic verification outranks agent self-assessment. PostgreSQL is the intended canonical durable state from Gate 1 onward; no critical state may exist only in memory.

Each venture is an isolated repository, credential, data, deployment, permissions and budget boundary. Historical repositories remain donors and cannot become canonical by bulk merge.
