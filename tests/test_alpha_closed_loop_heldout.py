"""Gate 8 / Slice 4 — HELD-OUT closed-loop evals.

Authored AFTER the production freeze at a0676b0. Materially distinct ventures, durations,
content, audiences, message ids, and intervention timing exercise the frozen closed-loop
machinery independently: completeness/assistance/reality classification, allocator lineage,
real-vs-simulated provenance (with the trusted production path's HTTP boundary stubbed — never a
transport subclass), negative/no-response paths, intervention scoping, cross-venture isolation,
history immutability, and trust-forgery resistance. No production changes, no real action.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

import psycopg
import pytest

from aidan_core import commitment, nextaction
from aidan_core.alpha import autonomy, loop
from aidan_core.errors import MarketAuthorityError
from aidan_core.market import observation as obs_mod
from aidan_core.market import origin as origin_mod
from aidan_core.market import postmark as pm
from aidan_core.market import runtime as market_runtime
from aidan_core.market import window as window_mod
from aidan_core.market.observation import record_market_observation

from factory_fakes import registry_with
from market_fakes import ChannelWorker, MarketSetup, freeze_outreach, market_action, operating_setup
from postmark_fakes import FakeRecipientResolver, default_source, install_real_postmark, basic_auth


class _Ctx:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _rec_time(conn, rec_id):
    with conn.cursor() as cur:
        cur.execute("SELECT created_at FROM next_action_recommendation WHERE id = %s", (rec_id,))
        return cur.fetchone()[0]


def _proof_time(conn, action_id):
    with conn.cursor() as cur:
        cur.execute("SELECT created_at FROM proof_receipt WHERE action_request_id = %s "
                    "AND verification_type = 'MARKET_ACTION' AND result = 'VERIFIED'", (action_id,))
        return cur.fetchone()[0]


def _ho_setup(conn, slug, *, days=7):
    s = operating_setup(conn, slug)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO validation_hypothesis (venture_id, hypothesis_key, opportunity_id, statement, "
                    "statement_hash) VALUES (%s,%s,%s,%s,'h') RETURNING id",
                    (s.venture_id, f"hoh-{slug}", s.opportunity_id, f"reachable via {slug}"))
        vh = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO validation_test (venture_id, test_key, validation_hypothesis_id, test_type, method, "
            "success_criterion, evidence_required, max_spend, max_duration_days, success_metric, definition_hash) "
            "VALUES (%s,%s,%s,'OUTREACH','cold email','>=3 qualified replies','reply logs','150',%s,'replies','h') RETURNING id",
            (s.venture_id, f"hot-{slug}", vh, days))
        vt = cur.fetchone()[0]
    return MarketSetup(s.venture_id, s.opportunity_id, vt)


def _ho_loop(conn, slug, *, real=False, outcome="REPLIED", days=7, monkeypatch=None,
             content="Would a same-week onboarding pilot help your team?", audience="aud://ho-segment"):
    """A materially-distinct held-out closed loop (simulated local channel, or the genuine
    production Postmark path with HTTP stubbed when ``real=True``)."""
    setup = _ho_setup(conn, slug, days=days)
    seed_a = market_action(conn, setup.venture_id, key=f"{slug}-seed")
    seed_spec = freeze_outreach(conn, setup, seed_a, content=content, audience_ref=audience)
    market_runtime.execute_market_action(conn, seed_a, registry=registry_with(ChannelWorker()), worker_kind="outreach-a")
    assert market_runtime.verify_market_action(conn, seed_a, actual_cost=0).verified
    record_market_observation(conn, seed_spec.market_action_spec_id, external_event_id=f"{slug}-seed-evt",
                              observation_type="DELIVERED", channel_kind="fake-local")
    r1 = nextaction.recommend(conn, setup.venture_id, setup.opportunity_id, recommendation_key=f"{slug}-k1")
    assert r1.action_type == "MARKET"
    loop_action = commitment.commit_recommendation(conn, r1.recommendation_id).resulting_action_id

    if real:
        transport, store = install_real_postmark(monkeypatch)
        resolver, source = FakeRecipientResolver(), default_source()
        loop_spec = freeze_outreach(conn, setup, loop_action, channel_kind=pm.POSTMARK_CHANNEL,
                                    content=content, audience_ref=audience)
        pm.execute_postmark_action(conn, loop_action, registry=registry_with(
            pm.PostmarkEmailWorker(transport, resolver, source)), source=source)
        assert pm.verify_postmark_action(conn, loop_action, transport=transport, resolver=resolver, source=source).verified
        mid = next(k for k, v in store.items() if k != "_n"
                   and str(v["Metadata"].get("market_action_spec")) == str(loop_spec.market_action_spec_id))
        if outcome == "REPLIED":
            pm.ingest_postmark_reply(conn, {"RecordType": "Inbound",
                                            "MailboxHash": pm.reply_mailbox_hash(setup.venture_id, loop_spec.market_action_spec_id),
                                            "From": "buyer@lead.invalid", "TextBody": "keen", "MessageID": f"{slug}-in"},
                                     source=source, auth_header=basic_auth(), transport=transport)
        elif outcome == "BOUNCED":
            pm.ingest_postmark_event(conn, {"RecordType": "Bounce", "MessageID": mid, "Type": "HardBounce"},
                                     source=source, auth_header=basic_auth(), transport=transport)
        elif outcome == "NO_RESPONSE":
            window_mod.record_no_response_completion(conn, loop_spec.market_action_spec_id,
                                                     as_of=_proof_time(conn, loop_action) + timedelta(days=days))
        elif outcome == "GENERIC_REPLIED":   # real action, but a GENERIC (untrusted) observation
            record_market_observation(conn, loop_spec.market_action_spec_id, external_event_id=f"{slug}-gen",
                                      observation_type="REPLIED", channel_kind=pm.POSTMARK_CHANNEL)
    else:
        loop_spec = freeze_outreach(conn, setup, loop_action, content=content, audience_ref=audience)
        market_runtime.execute_market_action(conn, loop_action, registry=registry_with(ChannelWorker()), worker_kind="outreach-a")
        assert market_runtime.verify_market_action(conn, loop_action, actual_cost=0).verified
        if outcome == "NO_RESPONSE":
            window_mod.record_no_response_completion(conn, loop_spec.market_action_spec_id,
                                                     as_of=_proof_time(conn, loop_action) + timedelta(days=days))
        else:
            record_market_observation(conn, loop_spec.market_action_spec_id, external_event_id=f"{slug}-o1",
                                      observation_type=outcome, channel_kind="fake-local",
                                      occurred_at=_proof_time(conn, loop_action) + timedelta(days=1))

    r2 = nextaction.recommend(conn, setup.venture_id, setup.opportunity_id, recommendation_key=f"{slug}-k2")
    return _Ctx(setup=setup, r1=r1, r2=r2, loop_action=loop_action, loop_spec=loop_spec,
                r1_at=_rec_time(conn, r1.recommendation_id), r2_at=_rec_time(conn, r2.recommendation_id))


def _classify(conn, c, next_rec=None):
    return loop.classify_loop(conn, start_recommendation_id=c.r1.recommendation_id,
                              next_recommendation_id=(next_rec or c.r2).recommendation_id)


def _intervene(conn, vid, *, at, kind="REASONING_CORRECTION"):
    return autonomy.record_intervention(conn, vid, intervention_kind=kind, intervention_stage="run", occurred_at=at)


# ==========================================================================
# H1-H3 — clean simulated positive / negative / no-response
# ==========================================================================
def test_H1_clean_simulated_positive(migrated):
    c = _ho_loop(migrated, "lumen-clinic", outcome="REPLIED", days=6,
                 content="Pilot our intake bot for your dermatology group?", audience="aud://derm-eu")
    assert _classify(migrated, c) == {"completeness": "COMPLETE", "assistance_class": autonomy.CLEAN,
                                      "reality_class": "SIMULATED", "eligible_clean_real_alpha": False}


def test_H2_clean_simulated_negative(migrated):
    c = _ho_loop(migrated, "atlas-legal", outcome="BOUNCED", days=4,
                 content="Contract review automation for your firm?", audience="aud://legal-us")
    r = _classify(migrated, c)
    assert r["completeness"] == "COMPLETE" and r["reality_class"] == "SIMULATED" and r["eligible_clean_real_alpha"] is False
    with migrated.cursor() as cur:
        cur.execute("SELECT observation_type FROM market_observation WHERE market_action_spec_id = %s "
                    "AND observation_type = 'BOUNCED'", (c.loop_spec.market_action_spec_id,))
        assert cur.fetchone() is not None   # negative evidence retained
    assert c.r2.action_type != "KILL"       # no arbitrary KILL


def test_H3_clean_simulated_no_response(migrated):
    c = _ho_loop(migrated, "verde-farms", outcome="NO_RESPONSE", days=3,
                 content="Yield analytics trial for your co-op?", audience="aud://agri-latam")
    r = _classify(migrated, c)
    assert r["completeness"] == "COMPLETE" and r["reality_class"] == "SIMULATED"
    assert "NO_RESPONSE" not in obs_mod.OBSERVATION_TYPES     # derived fact, never an observation
    assert len(nextaction.provenance(migrated, c.r2.recommendation_id)["considered_completions"]) >= 1


# ==========================================================================
# H4-H6 — trusted REAL positive / negative / no-response (production path, HTTP stubbed)
# ==========================================================================
def test_H4_real_positive_eligible(migrated, monkeypatch):
    c = _ho_loop(migrated, "nimbus-saas", real=True, outcome="REPLIED", monkeypatch=monkeypatch,
                 content="Two-week concierge migration for your ops team?", audience="aud://ops-na")
    r = _classify(migrated, c)
    assert r == {"completeness": "COMPLETE", "assistance_class": autonomy.CLEAN,
                 "reality_class": "REAL", "eligible_clean_real_alpha": True}


def test_H5_real_negative_eligible(migrated, monkeypatch):
    c = _ho_loop(migrated, "orchid-health", real=True, outcome="BOUNCED", monkeypatch=monkeypatch,
                 content="Pre-auth follow-up automation for your clinic?", audience="aud://health-uk")
    r = _classify(migrated, c)
    assert r["reality_class"] == "REAL" and r["eligible_clean_real_alpha"] is True   # negativity != not-real


def test_H6_real_no_response(migrated, monkeypatch):
    c = _ho_loop(migrated, "quartz-logistics", real=True, outcome="NO_RESPONSE", days=5, monkeypatch=monkeypatch,
                 content="Route optimization pilot for your fleet?", audience="aud://logi-eu")
    r = _classify(migrated, c)
    assert r["reality_class"] == "REAL" and r["completeness"] == "COMPLETE" and r["eligible_clean_real_alpha"] is True


# ==========================================================================
# H7 — human-assisted real loop
# ==========================================================================
def test_H7_human_assisted_real_loop(migrated, monkeypatch):
    c = _ho_loop(migrated, "cedar-fintech", real=True, outcome="REPLIED", monkeypatch=monkeypatch)
    _intervene(migrated, c.setup.venture_id, at=c.r1_at, kind="PROVIDER_REPAIR")   # inside [r1, r2)
    r = _classify(migrated, c)
    assert r["reality_class"] == "REAL" and r["assistance_class"] == autonomy.HUMAN_ASSISTED
    assert r["eligible_clean_real_alpha"] is False   # real evidence preserved; not clean


# ==========================================================================
# H8 / H9 — historical / post-loop interventions do not contaminate
# ==========================================================================
def test_H8_historical_intervention_ignored(migrated):
    c = _ho_loop(migrated, "sable-retail", outcome="REPLIED")
    _intervene(migrated, c.setup.venture_id, at=c.r1_at - timedelta(days=2))   # strictly before start
    assert _classify(migrated, c)["assistance_class"] == autonomy.CLEAN


def test_H9_post_loop_intervention_no_retroactive_contamination(migrated):
    c = _ho_loop(migrated, "onyx-travel", outcome="REPLIED")
    assert _classify(migrated, c)["assistance_class"] == autonomy.CLEAN
    _intervene(migrated, c.setup.venture_id, at=c.r2_at + timedelta(days=3))   # after validated end
    assert _classify(migrated, c)["assistance_class"] == autonomy.CLEAN        # unchanged


# ==========================================================================
# H10 — foreign / invalid next recommendation rejected
# ==========================================================================
def test_H10_foreign_next_recommendation_rejected(migrated):
    a = _ho_loop(migrated, "harbor-a", outcome="REPLIED")
    b = _ho_loop(migrated, "harbor-b", outcome="REPLIED")
    with pytest.raises(MarketAuthorityError):   # another venture's recommendation
        loop.classify_loop(migrated, start_recommendation_id=a.r1.recommendation_id,
                           next_recommendation_id=b.r2.recommendation_id)
    with pytest.raises(MarketAuthorityError):   # earlier recommendation cannot be the "next"
        loop.classify_loop(migrated, start_recommendation_id=a.r2.recommendation_id,
                           next_recommendation_id=a.r1.recommendation_id)


# ==========================================================================
# H11 — generic observation cannot upgrade a REAL action to a REAL loop
# ==========================================================================
def test_H11_generic_observation_on_real_action_is_simulated(migrated, monkeypatch):
    c = _ho_loop(migrated, "flint-energy", real=True, outcome="GENERIC_REPLIED", monkeypatch=monkeypatch)
    assert origin_mod.action_reality(migrated, c.loop_action) == "REAL"       # real outbound proof
    assert _classify(migrated, c)["reality_class"] == "SIMULATED"             # generic outcome != REAL


# ==========================================================================
# H12 — unrelated REAL observation elsewhere cannot upgrade this loop
# ==========================================================================
def test_H12_unrelated_real_observation_cannot_upgrade(migrated, monkeypatch):
    target = _ho_loop(migrated, "willow-media", outcome="REPLIED")            # SIMULATED local loop
    _ho_loop(migrated, "willow-real", real=True, outcome="REPLIED", monkeypatch=monkeypatch)  # other venture, REAL
    assert _classify(migrated, target)["reality_class"] == "SIMULATED"


# ==========================================================================
# H13 — mixed real + simulated outcomes on one action
# ==========================================================================
def test_H13_mixed_real_and_simulated_outcomes(migrated, monkeypatch):
    c = _ho_loop(migrated, "delta-edu", real=True, outcome="REPLIED", monkeypatch=monkeypatch)
    # add a generic (untrusted) observation to the SAME action, then recommend citing both
    generic = record_market_observation(migrated, c.loop_spec.market_action_spec_id, external_event_id="delta-gen",
                                        observation_type="OPENED", channel_kind=pm.POSTMARK_CHANNEL)
    assert origin_mod.observation_is_real(migrated, generic.market_observation_id) is False   # not relabeled
    r3 = nextaction.recommend(migrated, c.setup.venture_id, c.setup.opportunity_id, recommendation_key="delta-edu-k3")
    # the recommendation cites both the trusted REAL reply and the generic observation
    assert _classify(migrated, c, next_rec=r3)["reality_class"] == "REAL"   # exact cited REAL outcome confers REAL


# ==========================================================================
# H14 — late reply after NO_RESPONSE does not rewrite history
# ==========================================================================
def test_H14_late_reply_after_no_response(migrated, monkeypatch):
    c = _ho_loop(migrated, "grove-hr", real=True, outcome="NO_RESPONSE", days=4, monkeypatch=monkeypatch)
    r1_prov = nextaction.provenance(migrated, c.r2.recommendation_id)["considered_completions"]
    # a late reply arrives after the deadline — recorded as evidence, driving a NEW recommendation
    # without rewriting the retained NO_RESPONSE completion or the earlier recommendation
    late = record_market_observation(migrated, c.loop_spec.market_action_spec_id, external_event_id="grove-late",
                                     observation_type="REPLIED", channel_kind=pm.POSTMARK_CHANNEL,
                                     occurred_at=_proof_time(migrated, c.loop_action) + timedelta(days=30))
    r3 = nextaction.recommend(migrated, c.setup.venture_id, c.setup.opportunity_id, recommendation_key="grove-hr-k3")
    assert nextaction.provenance(migrated, c.r2.recommendation_id)["considered_completions"] == r1_prov  # R1 unchanged
    assert late.created is True and r3.recommendation_id != c.r2.recommendation_id


# ==========================================================================
# H15 — terminal KILL closed loop
# ==========================================================================
def _kill_loop(conn, slug):
    """A loop whose precommitted kill criterion (a FAIL result) makes the next allocation KILL."""
    s = operating_setup(conn, slug)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO assumption (venture_id, assumption_key, proposition, proposition_hash, importance, "
                    "confidence, consequence_if_false, cheapest_test) "
                    "VALUES (%s,%s,'buyers will pay','h','CRITICAL','LOW','no market','interview') RETURNING id",
                    (s.venture_id, f"as-{slug}"))
        aid = cur.fetchone()[0]
        cur.execute("INSERT INTO opportunity_assumption (opportunity_id, assumption_id, venture_id) VALUES (%s,%s,%s)",
                    (s.opportunity_id, aid, s.venture_id))
        cur.execute("INSERT INTO validation_hypothesis (venture_id, hypothesis_key, opportunity_id, assumption_id, "
                    "statement, statement_hash) VALUES (%s,%s,%s,%s,'wtp','h') RETURNING id",
                    (s.venture_id, f"kh-{slug}", s.opportunity_id, aid))
        vh = cur.fetchone()[0]
        cur.execute("INSERT INTO validation_test (venture_id, test_key, validation_hypothesis_id, test_type, method, "
                    "success_criterion, evidence_required, definition_hash) "
                    "VALUES (%s,%s,%s,'PRICING','preorder','>=10 preorders','orders','h') RETURNING id",
                    (s.venture_id, f"kt-{slug}", vh))
        vt = cur.fetchone()[0]
        cur.execute("INSERT INTO validation_result (venture_id, validation_test_id, result_key, observed_hash, outcome) "
                    "VALUES (%s,%s,%s,'h','FAIL')", (s.venture_id, vt, f"kr-{slug}"))
    r1 = nextaction.recommend(conn, s.venture_id, s.opportunity_id, recommendation_key=f"{slug}-k1")
    return s, r1


def test_H15_terminal_kill_loop(migrated):
    s, r1 = _kill_loop(migrated, "granite-mfg")
    assert r1.action_type == "KILL" and r1.reason_code == "KILL_CRITERION_TRIGGERED"
    commitment.commit_recommendation(migrated, r1.recommendation_id)   # KILL decision (terminal, no action)
    r = loop.classify_loop(migrated, start_recommendation_id=r1.recommendation_id, next_recommendation_id=None)
    assert r["completeness"] == "COMPLETE" and r["reality_class"] == "SIMULATED"
    with migrated.cursor() as cur:   # KILL produces no market ActionRequest
        cur.execute("SELECT count(*) FROM action_request WHERE venture_id = %s AND action_type = 'send_outreach'",
                    (s.venture_id,))
        assert cur.fetchone()[0] == 0


# ==========================================================================
# Trust-forgery adversarial cases
# ==========================================================================
def test_H16_trust_forgery_resistance(migrated, monkeypatch):
    c = _ho_loop(migrated, "basalt-ai", real=True, outcome="REPLIED", monkeypatch=monkeypatch)
    mid = None
    with migrated.cursor() as cur:
        cur.execute("SELECT er.external_result_id FROM execution_result er JOIN proof_receipt pr "
                    "ON pr.execution_result_id = er.id WHERE pr.action_request_id = %s AND pr.result = 'VERIFIED'",
                    (c.loop_action,))
        mid = cur.fetchone()[0]

    class FakeReal(pm.PostmarkHttpTransport):
        pass

    class Shaped:
        origin_kind = "REAL_PROVIDER"
    # none of these can produce a trusted attestation
    assert pm._trusted_provider_state(FakeReal("x"), mid) is None            # subclass
    assert pm._trusted_provider_state(Shaped(), mid) is None                 # arbitrary object
    with pytest.raises(RuntimeError):
        pm._PostmarkVerifiedProviderState(object(), message_id="x", server_id="y")  # direct construction
    # no caller reality flag exists on either origin writer
    import inspect
    assert "is_real" not in inspect.signature(origin_mod.record_observation_origin).parameters
    assert "origin_kind" not in inspect.signature(origin_mod.record_evidence_origin).parameters


# ==========================================================================
# Cross-venture isolation + immutability
# ==========================================================================
def test_H17_cross_venture_observation_and_completion_isolation(migrated):
    a = _ho_loop(migrated, "iso-a", outcome="REPLIED")
    b = _ho_loop(migrated, "iso-b", outcome="REPLIED")
    # venture-B's canonical source cannot attach an observation to venture-A's action
    with pytest.raises(MarketAuthorityError):
        record_market_observation(migrated, a.loop_spec.market_action_spec_id, external_event_id="x",
                                  observation_type="DELIVERED", channel_kind="fake-local",
                                  source_instance_ref=f"fake-local:{b.setup.venture_id}")
    # an intervention cannot reference another venture's action (composite FK)
    other_action = market_action(migrated, b.setup.venture_id, key="iso-x")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        autonomy.record_intervention(migrated, a.setup.venture_id, intervention_kind="CODE_REPAIR",
                                     intervention_stage="run", occurred_at=a.r1_at,
                                     related_action_request_id=other_action)


def test_H18_classification_and_reconstruction_are_immutable(migrated, monkeypatch):
    c = _ho_loop(migrated, "marble-bio", real=True, outcome="REPLIED", monkeypatch=monkeypatch)

    def _snap():
        with migrated.cursor() as cur:
            out = {}
            for t in ("proof_receipt", "external_evidence_origin", "market_observation",
                      "market_observation_origin", "market_window_completion",
                      "recommendation_market_observation"):
                cur.execute(f"SELECT md5(string_agg(id::text, ',' ORDER BY id)) FROM {t}")
                out[t] = cur.fetchone()[0]
            return out

    before = _snap()
    _classify(migrated, c)
    origin_mod.action_reality(migrated, c.loop_action)
    nextaction.provenance(migrated, c.r2.recommendation_id)
    assert _snap() == before   # truth projection mutated nothing
