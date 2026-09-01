"""Frozen Stage-C smoke spec + repaired health probe + immutable workflow (DB-free, no Fly).

Covers matrix rows A-D (health probe reduces external HTML to the exact canonical marker; verifier
exact-match semantics unchanged), E-H (concrete amd64 digest frozen; digest/port/marker changes
change the smoke-spec hash), I (workflow has no image/port/path/marker inputs), R (accepted-SHA
mismatch blocks before any Fly), S (spec tamper blocks before any Fly).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aidan_core.deploy import fly_stagec_spec as spec
from aidan_core.deploy.checks import _obs_health
from aidan_core.deploy.fly_live_smoke import main, observed_health_from_body, validate_app_name
from aidan_core.deploy.observe import DeploymentObservation

NGINX_HTML = ("<!DOCTYPE html>\n<html>\n<head><title>Welcome to nginx!</title></head>\n"
              "<body><h1>Welcome to nginx!</h1></body>\n</html>\n")
MARKER = "Welcome to nginx!"


# ---- A/B/C: probe reduces external HTML to the exact canonical marker -----------------
def test_A_probe_extracts_exact_marker_from_html():
    assert observed_health_from_body(NGINX_HTML, MARKER) == MARKER   # exactly the marker, not the HTML


def test_B_probe_absent_marker_is_none():
    assert observed_health_from_body("<html>no welcome here</html>", MARKER) is None


def test_C_probe_case_mismatch_is_none():
    assert observed_health_from_body("welcome to NGINX!", MARKER) is None


def test_probe_generic_when_no_marker():
    assert observed_health_from_body("anything", None) == "ok"
    assert observed_health_from_body("", None) is None


# ---- D: the deterministic verifier's EXACT-match health semantics are UNCHANGED --------
def test_D_verifier_health_exact_match_unchanged():
    contract = {"marker_content": MARKER}
    ok = DeploymentObservation("id", (), MARKER)            # canonical marker (from repaired probe)
    bad = DeploymentObservation("id", (), NGINX_HTML)       # raw HTML would NOT exact-match
    assert _obs_health(ok, contract).result == "PASS"
    assert _obs_health(bad, contract).result == "FAIL"      # verifier still exact-match, unweakened


# ---- E: the CONCRETE linux/amd64 manifest digest is frozen (not the multi-arch index) --
def test_E_concrete_amd64_digest_frozen():
    assert spec.STAGEC_SPEC["expected_artifact_digest"] == spec.NGINX_AMD64_MANIFEST_DIGEST
    assert spec.NGINX_AMD64_MANIFEST_DIGEST.startswith("sha256:") and len(spec.NGINX_AMD64_MANIFEST_DIGEST) == 71
    assert spec.assert_frozen() == spec.FROZEN_SMOKE_SPEC_HASH   # frozen hash matches the frozen spec


# ---- F/G/H: changing digest / port / marker changes the smoke-spec hash ---------------
def _mutated(**over):
    import copy
    s = copy.deepcopy(spec.STAGEC_SPEC)
    for k, v in over.items():
        s[k] = v
    return spec.compute_smoke_spec_hash(s)


def test_F_changing_digest_changes_hash():
    assert _mutated(expected_artifact_digest="sha256:" + "cd" * 32) != spec.FROZEN_SMOKE_SPEC_HASH


def test_G_changing_port_changes_hash():
    import copy
    s = copy.deepcopy(spec.STAGEC_SPEC)
    s["runtime_contract"]["internal_port"] = 8080
    assert spec.compute_smoke_spec_hash(s) != spec.FROZEN_SMOKE_SPEC_HASH


def test_H_changing_marker_changes_hash():
    import copy
    s = copy.deepcopy(spec.STAGEC_SPEC)
    s["health_contract"]["marker_content"] = "different"
    assert spec.compute_smoke_spec_hash(s) != spec.FROZEN_SMOKE_SPEC_HASH


# ---- S: a tampered frozen spec fails closed (before any Fly) --------------------------
def test_S_tampered_spec_asserts(monkeypatch):
    import copy
    tampered = copy.deepcopy(spec.STAGEC_SPEC)
    tampered["expected_artifact_digest"] = "sha256:" + "00" * 32
    monkeypatch.setattr(spec, "STAGEC_SPEC", tampered)
    with pytest.raises(spec.SmokeSpecMismatch):
        spec.assert_frozen()


# ---- I: the manual workflow exposes NO image/port/path/marker inputs ------------------
def test_I_workflow_has_no_mutable_smoke_inputs():
    wf = Path(__file__).resolve().parents[1] / ".github/workflows/gate8-fly-live-deploy-smoke.yml"
    text = wf.read_text(encoding="utf-8")
    inputs_block = text.split("jobs:", 1)[0]
    for forbidden in ("image_ref:", "internal_port:", "health_path:", "health_marker:"):
        assert forbidden not in inputs_block, f"{forbidden} must not be a dispatch input"
    for required in ("confirm:", "fly_app:", "accepted_sha:"):
        assert required in inputs_block
    assert "workflow_dispatch:" in text and "on:\n  push" not in text


# ---- R: accepted-SHA mismatch blocks BEFORE any DB/Fly work ---------------------------
def test_R_accepted_sha_mismatch_blocks(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")   # never connected: SHA check precedes it
    monkeypatch.setenv("CONFIRM", "RUN_FROZEN_FLY_DEPLOY_SMOKE")
    monkeypatch.setenv("FLY_SMOKE_ACCEPTED_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    assert main() == 3
    assert "SHA_MISMATCH" in capsys.readouterr().out


def test_app_name_validation():
    assert validate_app_name("aidan-gate8-smoke-1") == "aidan-gate8-smoke-1"
    for bad in ("", "-bad", "UPPER", "has space", "a" * 64):
        with pytest.raises(ValueError):
            validate_app_name(bad)
