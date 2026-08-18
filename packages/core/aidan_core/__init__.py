"""AI-DAN Venture OS canonical truth & governance kernel.

Gate 1, Slice 1: PostgreSQL-backed persistence foundation only.
No ActionRequest, Policy Engine, approvals, budgets, proof receipts or
kill switch are implemented in this slice; those belong to later slices.

Submodules are imported lazily by callers to keep import of this package
free of a hard ``psycopg`` requirement at module-load time.
"""

__version__ = "0.1.0"
