"""Real, provider-neutral research providers (Gate 8 real-integration, Slice 1).

The earliest missing REAL boundary of the Gate-8 closed loop is autonomous external
research. This module supplies two concrete, replaceable specialist providers that close it:

* :class:`HttpSearchAdapter` — a :class:`~aidan_core.research.adapters.ResearchAdapter` that
  ACQUIRES untrusted source DATA over HTTP from a configured search/retrieval endpoint.
* :class:`LlmResearchProposer` — a :class:`~aidan_core.research.proposals.ResearchProposer` that
  asks a configured LLM endpoint to PROPOSE typed research artifacts.

Both are *adapters*: the provider is chosen by environment configuration, never by architecture.
Neither ever opens a PostgreSQL connection, writes canonical state, sets Claim state, finalizes
opportunities, or holds any governance/capital authority. The deterministic kernel
(``research.orchestration.run_research``) verifies provenance — including the load-bearing
exact-substring excerpt anchoring — and performs every canonical write. A proposer may INTERPRET
acquired data, but its prose only becomes an Observation when its excerpt is an exact substring of
the acquired source the kernel hashed; a fabricated excerpt is rejected by the kernel and never
becomes evidence.

Fail-closed everywhere: when unconfigured, or on any provider/network/format failure, both raise
rather than invent a source, an excerpt, or an artifact (``run_research`` treats an adapter
exception as "no evidence for this question"). The only network boundary is :func:`_http_json`
(stdlib ``urllib`` only — no SDK, no framework); tests inject a deterministic transport instead, so
CI performs zero real provider calls. Secrets (API tokens) are read from the environment, sent only
in request headers, and never returned, logged, or placed in any AcquiredSource, proposal, or
exception message.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..errors import ConfigError, InvalidAcquisitionError
from .adapters import AcquiredSource
from .proposals import (
    AssumptionProposal,
    ClaimProposal,
    DimensionProposal,
    InterpretationProposal,
    KillCaseProposal,
    ObservationProposal,
    OpportunityProposal,
    ResearchProposal,
)

# --- Environment configuration (NAMES only; values never logged) --------------------
ENV_ACQUIRE_ENDPOINT = "RESEARCH_ACQUIRE_ENDPOINT"   # non-secret — acquisition HTTP endpoint URL
ENV_ACQUIRE_TOKEN = "RESEARCH_ACQUIRE_TOKEN"         # SECRET — acquisition provider bearer token
ENV_LLM_ENDPOINT = "RESEARCH_LLM_ENDPOINT"           # non-secret — proposer/LLM HTTP endpoint URL
ENV_LLM_TOKEN = "RESEARCH_LLM_TOKEN"                 # SECRET — proposer/LLM bearer token
ENV_LLM_MODEL = "RESEARCH_LLM_MODEL"                 # non-secret — model identifier

Transport = Callable[..., "tuple[int, Any]"]


def _http_json(method: str, url: str, *, headers: dict, body: Optional[dict] = None,
               timeout: float = 20.0):  # pragma: no cover - real network boundary (stubbed in tests)
    """The single real network boundary: one JSON request via stdlib urllib. No SDK."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers))
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - operator-configured endpoint
        status = getattr(resp, "status", 200)
        payload = json.loads(resp.read().decode("utf-8"))
    return status, payload


def _acquisition_key(query: str, rank: int, locator: str, content: str) -> str:
    digest = hashlib.sha256(f"{locator}\n{content}".encode("utf-8")).hexdigest()[:24]
    return f"acq:{hashlib.sha256(query.encode('utf-8')).hexdigest()[:8]}:{rank}:{digest}"


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class HttpSearchAdapter:
    """Acquire untrusted source DATA over HTTP. Returns validated ``AcquiredSource`` only.

    Expected provider response envelope (provider-neutral JSON):
        ``{"results": [{"url": str, "content": str, "published_at": <iso, optional>}, ...]}``
    """

    adapter_id = "http-search-v1"

    def __init__(self, *, env: Optional[dict] = None, transport: Optional[Transport] = None,
                 now: Optional[Callable[[], datetime]] = None, max_results: int = 5):
        self._env = os.environ if env is None else env
        self._http = transport or _http_json
        self._now = now
        self._max = max_results

    def _clock(self) -> datetime:
        return self._now() if self._now is not None else datetime.now(timezone.utc)

    def acquire(self, query: str) -> list[AcquiredSource]:
        if not isinstance(query, str) or not query.strip():
            raise InvalidAcquisitionError("query must be a non-empty string")
        endpoint = self._env.get(ENV_ACQUIRE_ENDPOINT)
        token = self._env.get(ENV_ACQUIRE_TOKEN)
        if not endpoint or not token:
            raise ConfigError(f"{ENV_ACQUIRE_ENDPOINT}/{ENV_ACQUIRE_TOKEN} not configured")  # fail closed
        try:
            status, data = self._http(
                "POST", endpoint,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                         "Accept": "application/json"},
                body={"query": query, "max_results": self._max})
        except (ConfigError, InvalidAcquisitionError):
            raise
        except Exception as exc:  # network/provider fault — never fabricate a source
            raise InvalidAcquisitionError(f"acquisition provider call failed ({type(exc).__name__})") from None
        if status != 200 or not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise InvalidAcquisitionError(f"acquisition provider returned an unusable response (status {status})")
        out: list[AcquiredSource] = []
        for rank, r in enumerate(data["results"][: self._max]):
            if not isinstance(r, dict):
                raise InvalidAcquisitionError("acquisition result must be an object")
            locator, content = r.get("url"), r.get("content")
            if not (isinstance(locator, str) and locator.strip() and isinstance(content, str) and content.strip()):
                raise InvalidAcquisitionError("acquisition result missing url/content")
            published = _parse_iso(r.get("published_at"))
            out.append(AcquiredSource(
                locator=locator, source_type="WEB_PAGE", content=content,
                retrieved_at=self._clock(), retrieved_by=self.adapter_id,
                acquisition_key=_acquisition_key(query, rank, locator, content),
                published_at=published, publication_time_known=published is not None,
                reliability_code="UNKNOWN",
                metadata={"provider": self.adapter_id, "query": query, "rank": rank}))
        return out


def _has(d: Any, *keys: str) -> bool:
    return isinstance(d, dict) and all(k in d for k in keys)


def _opt_str(v: Any) -> Optional[str]:
    return v if isinstance(v, str) and v.strip() else None


def _parse_dimension(d: Any) -> Optional[DimensionProposal]:
    if not _has(d, "dimension", "assessment", "rationale"):
        return None
    return DimensionProposal(dimension=str(d["dimension"]), assessment=str(d["assessment"]),
                             rationale=str(d["rationale"]), claim_keys=tuple(map(str, d.get("claim_keys", ()))))


def _parse_kill_case(k: Any) -> Optional[KillCaseProposal]:
    if not _has(k, "disposition"):
        return None
    dims = tuple(x for x in (_parse_dimension(d) for d in (k.get("dimensions") or [])) if x is not None)
    return KillCaseProposal(disposition=str(k["disposition"]), dimensions=dims)


def _parse_opportunity(o: Any) -> OpportunityProposal:
    kc = _parse_kill_case(o.get("kill_case")) if isinstance(o.get("kill_case"), dict) else None
    return OpportunityProposal(
        key=str(o["key"]), buyer_hypothesis=str(o["buyer_hypothesis"]),
        problem_hypothesis=str(o["problem_hypothesis"]), critical_unknown=str(o["critical_unknown"]),
        claim_keys=tuple(map(str, o.get("claim_keys", ()))),
        assumption_keys=tuple(map(str, o.get("assumption_keys", ()))),
        interpretation_keys=tuple(map(str, o.get("interpretation_keys", ()))),
        acquisition_hypothesis=_opt_str(o.get("acquisition_hypothesis")), kill_case=kc)


def _parse_proposal(data: Any) -> ResearchProposal:
    if not isinstance(data, dict):
        raise InvalidAcquisitionError("LLM proposal must be a JSON object")

    def items(key: str) -> list:
        v = data.get(key, [])
        return v if isinstance(v, list) else []

    try:
        observations = tuple(
            ObservationProposal(source_index=int(o["source_index"]), excerpt=str(o["excerpt"]),
                                statement=str(o["statement"]), key=str(o["key"]), locator=_opt_str(o.get("locator")))
            for o in items("observations") if _has(o, "source_index", "excerpt", "statement", "key"))
        claim_proposals = tuple(
            ClaimProposal(key=str(c["key"]), statement=str(c["statement"]),
                          supports=tuple(map(str, c.get("supports", ()))),
                          contradicts=tuple(map(str, c.get("contradicts", ()))))
            for c in items("claims") if _has(c, "key", "statement"))
        interpretations = tuple(
            InterpretationProposal(key=str(x["key"]), statement=str(x["statement"]),
                                   produced_by=str(x.get("produced_by", "llm-proposer")),
                                   claim_keys=tuple(map(str, x.get("claim_keys", ()))))
            for x in items("interpretations") if _has(x, "key", "statement"))
        assumptions = tuple(
            AssumptionProposal(key=str(a["key"]), proposition=str(a["proposition"]),
                               importance=str(a["importance"]), confidence=str(a["confidence"]),
                               consequence_if_false=str(a["consequence_if_false"]), cheapest_test=str(a["cheapest_test"]),
                               claim_keys=tuple(map(str, a.get("claim_keys", ()))),
                               interpretation_keys=tuple(map(str, a.get("interpretation_keys", ()))))
            for a in items("assumptions")
            if _has(a, "key", "proposition", "importance", "confidence", "consequence_if_false", "cheapest_test"))
        opportunities = tuple(
            _parse_opportunity(o) for o in items("opportunities")
            if _has(o, "key", "buyer_hypothesis", "problem_hypothesis", "critical_unknown"))
    except (TypeError, ValueError, KeyError) as exc:
        raise InvalidAcquisitionError(f"malformed LLM proposal ({type(exc).__name__})") from None

    return ResearchProposal(observations=observations, claims=claim_proposals,
                            interpretations=interpretations, assumptions=assumptions, opportunities=opportunities)


class LlmResearchProposer:
    """Ask a configured LLM to PROPOSE typed artifacts. Never touches the database.

    The LLM receives only the Mandate text and acquired source data (index/content/locator) and
    returns JSON proposals. Its prose is a proposal, not truth: the kernel verifies every excerpt
    against the acquired source before persistence, and performs all canonical writes.
    """

    def __init__(self, *, env: Optional[dict] = None, transport: Optional[Transport] = None,
                 max_questions: int = 6):
        self._env = os.environ if env is None else env
        self._http = transport or _http_json
        self._max_q = max_questions

    def _call(self, task: str, payload: dict) -> dict:
        endpoint = self._env.get(ENV_LLM_ENDPOINT)
        token = self._env.get(ENV_LLM_TOKEN)
        model = self._env.get(ENV_LLM_MODEL)
        if not endpoint or not token or not model:
            raise ConfigError(f"{ENV_LLM_ENDPOINT}/{ENV_LLM_TOKEN}/{ENV_LLM_MODEL} not configured")  # fail closed
        try:
            status, data = self._http(
                "POST", endpoint,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                         "Accept": "application/json"},
                body={"model": model, "task": task, "input": payload})
        except (ConfigError, InvalidAcquisitionError):
            raise
        except Exception as exc:
            raise InvalidAcquisitionError(f"LLM provider call failed ({type(exc).__name__})") from None
        if status != 200 or not isinstance(data, dict):
            raise InvalidAcquisitionError(f"LLM provider returned an unusable response (status {status})")
        return data

    def research_questions(self, mandate: str) -> list:
        data = self._call("research_questions", {"mandate": mandate})
        raw = data.get("questions")
        if not isinstance(raw, list):
            raise InvalidAcquisitionError("LLM did not return a questions list")
        questions = [q for q in raw if isinstance(q, str) and q.strip()][: self._max_q]
        if not questions:
            raise InvalidAcquisitionError("LLM returned no usable research questions")
        return questions

    def propose(self, mandate: str, sources: list) -> ResearchProposal:
        return _parse_proposal(self._call("propose", {"mandate": mandate, "sources": sources}))
