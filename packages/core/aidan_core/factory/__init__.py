"""Factory — the durable execution runtime (Gate 4).

Factory is a durable execution runtime, NOT an AI programmer. It executes
already-governed, bounded work behind a typed, replaceable worker boundary. The
canonical ActionRequest remains the sole authority; workers are replaceable
specialists that return result data only and hold no canonical DB authority.

Slice 1 establishes the immutable, authorization-bound execution specification,
the typed worker boundary, and durable claim-only dispatch. Verification, Proof
Receipts, canonical SUCCESS, retries, timeouts and recovery are later slices.
"""
