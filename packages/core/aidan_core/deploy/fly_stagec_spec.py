"""FROZEN Stage-C Fly deploy smoke specification (Gate 6 real-deploy readiness).

The first real Fly deployment is a single, immutable, preregistered boundary smoke. Every
deploy-relevant value — the exact public OCI image and its concrete linux/amd64 manifest digest, the
runtime/network contract, the health contract, the spend/attempt/cleanup bounds, and the post-smoke
lifecycle expectation — is frozen HERE, not accepted as a mutable dispatch-time input. Only the
owner-created external target (the Fly app name), the confirmation token, and the accepted-main SHA
are supplied at dispatch.

Immutability is enforced fail-closed: ``FROZEN_SMOKE_SPEC_HASH`` is a canonical hash over the frozen
spec, recomputed at runtime BEFORE any Fly mutation. Tampering with the image, digest, port, path,
marker, ceiling, or cleanup semantics changes the recomputed hash and aborts before a machine is
created.

OCI authority: the digest is the CONCRETE linux/amd64 image MANIFEST digest (verified read-only
against Docker Hub: fetching by this digest returns an OCI image manifest with config + layers).
Pinning the platform manifest digest — not the multi-arch index digest — removes index->platform
resolution ambiguity, so Fly's ``image_ref.digest`` read-back denotes exactly this value.

Honest scope: SOURCE_TO_ARTIFACT_DERIVATION (candidate_tree_hash -> OCI image) is NOT proven by this
smoke; the frozen artifact is a stock public image, deployed to prove the external deployment
boundary only.
"""
from __future__ import annotations

from decimal import Decimal

from ..actions import canonical_payload_hash

PROVIDER_KIND = "fly-machines"
# nginx:stable, concrete linux/amd64 image manifest digest (immutable). Multi-arch INDEX digest was
# sha256:09cc2702709e6388d979d8030e3ab4eb1ceb699b2dced26d7543e872a822e823 — deliberately NOT used.
NGINX_AMD64_MANIFEST_DIGEST = "sha256:2e46548799bad886ef8975d4cedbaf797902ed1ac9fa9b2cb56bae890cff7336"
IMAGE_REF = f"registry-1.docker.io/library/nginx@{NGINX_AMD64_MANIFEST_DIGEST}"

CEILING = Decimal("0.05")
REQUIRED_STATE = "started"
MAX_ATTEMPTS = 1
HEALTH_PATH = "/"
HEALTH_MARKER = "Welcome to nginx!"     # embedded in nginx's default "/" HTML; matched by substring
INTERNAL_PORT = 80
PORTS = [{"port": 80, "handlers": ["http"]}, {"port": 443, "handlers": ["tls", "http"]}]

# The immutable spec, canonicalised for hashing + consumed by the fixture/entrypoint.
STAGEC_SPEC = {
    "provider": PROVIDER_KIND,
    "image_ref": IMAGE_REF,
    "expected_artifact_digest": NGINX_AMD64_MANIFEST_DIGEST,
    "runtime_contract": {"internal_port": INTERNAL_PORT, "protocol": "tcp", "ports": PORTS},
    "health_contract": {"path": HEALTH_PATH, "marker_content": HEALTH_MARKER},
    "required_state": REQUIRED_STATE,
    "max_attempts": MAX_ATTEMPTS,
    "spend_ceiling_usd": str(CEILING),
    "cleanup": {"deletes_max": 1, "force": True, "confirm": "read_only_get_404"},
    "lifecycle_after_pass": "BUILDING",
    "source_to_artifact_derivation_proven": False,
}


class SmokeSpecMismatch(Exception):
    """The frozen Stage-C smoke spec was tampered with — abort BEFORE any Fly mutation."""


def compute_smoke_spec_hash(spec: dict) -> str:
    """Deterministic canonical hash over the frozen smoke spec (identity of the preregistration)."""
    return canonical_payload_hash(spec)


# Frozen expected identity of STAGEC_SPEC. Recomputed and compared at runtime (fail-closed).
FROZEN_SMOKE_SPEC_HASH = "20f8d9514a9c69e4126f57119af0b13bc8af14b1c9f2fa7ab78a6b1d531fc47b"


def assert_frozen() -> str:
    """Recompute the spec hash and fail closed on any drift. Returns the hash when it matches."""
    actual = compute_smoke_spec_hash(STAGEC_SPEC)
    if actual != FROZEN_SMOKE_SPEC_HASH:
        raise SmokeSpecMismatch(f"stage-C smoke spec hash {actual} != frozen {FROZEN_SMOKE_SPEC_HASH}")
    return actual
