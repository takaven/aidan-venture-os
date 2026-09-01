"""Provider-neutral deploy ARTIFACT IDENTITY (Gate 6 real-deploy readiness).

Gate-6 freezes ``candidate_tree_hash`` (exact source-tree identity). A real external deploy runs an
OCI image, whose identity is an immutable content digest. This module validates and normalizes the
``expected_artifact_identity`` (an OCI digest) that is frozen into the immutable ``release_contract``
(so it is hashed into ``release_hash`` and a changed digest changes release identity), bound into the
immutable execution spec, and later compared by the verifier against the provider read-back.

SCOPE — honest claim boundary. This module does NOT derive ``candidate_tree_hash -> OCI digest``. It
only validates/normalizes a caller-supplied digest. Therefore the Stage-C smoke proves only:

    "AIDAN deployed the EXACT OCI artifact its frozen release explicitly authorized."

and explicitly NOT:

    "AIDAN proved this OCI artifact was BUILT from candidate_tree_hash."

The ``candidate_tree_hash -> deploy artifact`` derivation (a deterministic, pinned host build tool)
is deliberately out of scope here and remains UNPROVEN by this smoke — a later composed production
proof must close that bridge. ``SOURCE_TO_ARTIFACT_DERIVATION_PROVEN`` records this, so no evidence
line can overclaim a derivation. The worker can never substitute a different digest — it receives
only the frozen one and the verifier independently confirms the RUNNING digest equals it.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# HONEST MARKER: this module does not (and this smoke does not) prove source-tree -> OCI-image
# derivation. Kept False until a deterministic, pinned build tool closes that bridge in a later slice.
SOURCE_TO_ARTIFACT_DERIVATION_PROVEN = False

ARTIFACT_KIND_OCI_DIGEST = "oci-image-digest"
_ALGOS = ("sha256", "sha512")
# An OCI content digest: "<algorithm>:<hex>" (sha256 -> 64 hex chars, sha512 -> 128).
_DIGEST_RE = re.compile(r"^(sha256:[0-9a-f]{64}|sha512:[0-9a-f]{128})$")


class ArtifactIdentityError(ValueError):
    """A deploy artifact identity is malformed or missing — reject before any deploy authority."""


def is_valid_digest(digest: str) -> bool:
    return bool(digest) and bool(_DIGEST_RE.match(str(digest)))


def normalize_digest(value: str) -> str:
    """Validate and return a canonical ``<algo>:<hex>`` content digest, or raise. Accepts either a
    bare digest or a fully-qualified ``repo@sha256:...`` reference (the digest part is extracted)."""
    if not value or not str(value).strip():
        raise ArtifactIdentityError("empty artifact digest")
    s = str(value).strip()
    if "@" in s:
        s = s.rsplit("@", 1)[1]
    s = s.lower()
    if not is_valid_digest(s):
        raise ArtifactIdentityError(f"malformed OCI digest (expected sha256:<64hex>): {s[:16]}...")
    return s


def build_expected_artifact_identity(digest: str, *, kind: str = ARTIFACT_KIND_OCI_DIGEST) -> dict:
    """Construct the frozen, immutable artifact-identity record for the release_contract. The digest
    MUST be trusted-tool-derived from the frozen candidate; this only validates/normalizes it."""
    if kind != ARTIFACT_KIND_OCI_DIGEST:
        raise ArtifactIdentityError(f"unsupported artifact identity kind: {kind}")
    return {"kind": kind, "digest": normalize_digest(digest)}


def expected_digest(artifact_identity: Optional[dict[str, Any]]) -> Optional[str]:
    """Extract the frozen digest from an ``expected_artifact_identity`` record, or None if absent."""
    if not artifact_identity:
        return None
    d = dict(artifact_identity).get("digest")
    return normalize_digest(d) if d else None


def identity_matches(expected: Optional[dict[str, Any]], observed_digest: Optional[str]) -> bool:
    """True iff a well-formed observed digest exactly equals the frozen expected digest. Fail-closed:
    a missing expected identity or a malformed/absent observed digest is NEVER a match."""
    exp = expected_digest(expected)
    if exp is None or not observed_digest:
        return False
    try:
        obs = normalize_digest(observed_digest)
    except ArtifactIdentityError:
        return False
    return obs == exp
