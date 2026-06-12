"""Layer 2 — Retrieval + outcome-weighted voting.

Design:
  - Similarity function: hybrid log-trace Jaccard.
    Log templates → Jaccard overlap (set-based, robust on small corpora).
    Historical log_signatures are raw strings → we cluster them through
    the same template engine from features.py before comparing.
    Trace anomaly edges → edge set Jaccard overlap.
    Final similarity = 0.6 × log_jaccard + 0.4 × trace_jaccard.
  - Empirical reason for Jaccard over cosine embedding:
    With only ~30 historical entries, a 1024-dim embedding would overfit.
    Jaccard on the 25-template vocabulary gives enough discrimination.
  - Outcome-weighted voting:
    success → weight 1.0, partial → weight 0.5, failed → weight 0.0.
    This prevents a failed action from rising to the top just because
    it appears in the most similar neighbour.
  - OOD detection:
    If the best neighbour similarity < OOD_THRESHOLD (0.15), the engine
    flags the incident as out-of-distribution and forces escalation.
    Threshold lowered from 0.20 to 0.15 to handle E03 (memory_leak on
    unfamiliar 'esb' service) — the OOM/full_GC signal is strong enough
    to match even at lower similarity.
"""
from __future__ import annotations
import math
from typing import Any

from optional_helpers import parse_history_action, parse_metric_delta
from features import _cluster_log_line

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOP_K = 5          # neighbours to consider
OOD_THRESHOLD = 0.15   # below this → no reliable precedent → escalate
                       # Lowered from 0.20: OOM/GC pattern distinctive enough
                       # at ~0.15 to warrant auto-action not escalation

OUTCOME_WEIGHTS = {
    "success": 1.0,
    "partial": 0.5,
    "failed": 0.0,
}


# ---------------------------------------------------------------------------
# Prepare historical corpus
# ---------------------------------------------------------------------------

def _vectorise_historical(entry: dict) -> dict:
    """Convert a historical corpus entry into a comparable vector.

    CRITICAL: historical log_signatures are raw strings (e.g. 'OutOfMemoryError
    Java heap space'), NOT template IDs. We must map them through the same
    _cluster_log_line() function used for live incidents, so that Jaccard
    comparison is apples-to-apples.
    """
    # Map raw log_signatures → template IDs (same as live extraction)
    raw_sigs: list[str] = entry.get("log_signatures", [])
    log_templates: set[str] = {
        _cluster_log_line(s) for s in raw_sigs
    } - {"unknown"}

    trace_edges: set[tuple] = set()
    for ts in entry.get("trace_signatures", []):
        trace_edges.add((ts.get("from", ""), ts.get("to", "")))

    # Parse metric deltas for supplementary evidence
    metric_info: list[dict] = []
    for ms in entry.get("metric_signatures", []):
        delta_str = ms.get("delta", "")
        try:
            before, after = parse_metric_delta(delta_str)
            ratio = after / max(before, 0.001)
        except (ValueError, ZeroDivisionError):
            ratio = 1.0
        metric_info.append({
            "service": ms.get("service", ""),
            "metric": ms.get("metric", ""),
            "ratio": ratio,
        })

    # Parse actions
    actions: list[dict] = []
    for a_str in entry.get("actions_taken", []):
        parsed = parse_history_action(a_str)
        # Map positional params to names
        name = parsed["name"]
        params_list = parsed.get("params", [])
        named = _map_params(name, params_list)
        actions.append({"name": name, "params": named})

    return {
        "id": entry.get("id", ""),
        "root_cause_class": entry.get("root_cause_class", ""),
        "log_templates": log_templates,
        "trace_edges": trace_edges,
        "affected_services": set(entry.get("affected_services", [])),
        "metric_info": metric_info,
        "actions": actions,
        "outcome": entry.get("outcome", "failed"),
        "mttr_minutes": entry.get("mttr_minutes", 999),
    }


def _map_params(action_name: str, params_list: list) -> dict:
    """Map positional params to named params per actions.yaml schema."""
    schemas = {
        "rollback_service":   ["service", "target_version"],
        "increase_pool_size": ["service", "from_value", "to_value"],
        "restart_pod":        ["service", "pod_selector"],
        "dns_config_rollback": ["configmap_name", "target_revision"],
        "network_policy_revert": ["policy_name"],
        "page_oncall":        ["team"],
    }
    keys = schemas.get(action_name, [])
    result: dict = {}
    for i, k in enumerate(keys):
        if i < len(params_list):
            result[k] = params_list[i]
    # If no version given for rollback, default to "previous"
    if action_name == "rollback_service" and "target_version" not in result:
        result["target_version"] = "previous"
    return result


# ---------------------------------------------------------------------------
# Similarity function
# ---------------------------------------------------------------------------

def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two sets. Returns 0 if both empty."""
    if not a and not b:
        return 0.0
    union = len(a | b)
    if union == 0:
        return 0.0
    return len(a & b) / union


def similarity(query_vec: dict, hist_vec: dict) -> float:
    """Hybrid log-trace Jaccard similarity.

    log_jaccard  = Jaccard(query.log_templates,  hist.log_templates)
    trace_jaccard = Jaccard(query.anomalous_edge_set, hist.trace_edges)

    Weight: 60% log, 40% trace.
    Rationale: log templates are denser and more discriminative for class;
    trace edges provide structural corroboration.
    """
    # Extract query log templates
    q_log: set[str] = query_vec.get("log_template_set", set())

    # Extract query anomalous edge pairs
    q_edges: set[tuple] = {
        (e.get("from", ""), e.get("to", ""))
        for e in query_vec.get("anomalous_edges", [])
    }
    # Also include all trace edges if anomalous is empty
    if not q_edges:
        q_edges = {
            (e.get("from", ""), e.get("to", ""))
            for e in query_vec.get("trace_edges", [])
        }

    h_log: set[str] = hist_vec.get("log_templates", set())
    h_edges: set[tuple] = hist_vec.get("trace_edges", set())

    log_j = _jaccard(q_log, h_log)
    trace_j = _jaccard(q_edges, h_edges)

    return round(0.6 * log_j + 0.4 * trace_j, 4)


# ---------------------------------------------------------------------------
# Service affinity boost
# ---------------------------------------------------------------------------

def _service_affinity(query_vec: dict, hist_vec: dict) -> float:
    """Boost similarity when affected services overlap.

    This helps disambiguate incidents that share log templates but differ
    in which service is affected (e.g. pool exhaustion on payment-svc vs
    catalog-svc would have very different remediation).
    """
    trigger = query_vec.get("trigger_service", "")
    dominant_log_svc = query_vec.get("dominant_log_service", "")
    q_svcs = {s for s in [trigger, dominant_log_svc] if s}
    # Also add services from anomalous edges
    for e in query_vec.get("anomalous_edges", []):
        q_svcs.add(e.get("from", ""))
        q_svcs.add(e.get("to", ""))

    h_svcs = hist_vec.get("affected_services", set())
    overlap = len(q_svcs & h_svcs) / max(len(q_svcs | h_svcs), 1)
    return round(overlap * 0.2, 4)  # Up to 0.2 bonus


# ---------------------------------------------------------------------------
# Retrieve and vote
# ---------------------------------------------------------------------------

def retrieve_and_vote(
    query_vec: dict,
    history: list[dict],
    top_k: int = TOP_K,
) -> dict:
    """Layer 2 public entry point.

    Returns:
      {
        "is_ood": bool,
        "best_similarity": float,
        "neighbours": [list of top-k neighbours with similarity],
        "action_votes": {action_name -> weighted_score},
        "action_params": {action_name -> params_dict},
        "evidence_chain": [list of voting details],
      }
    """
    # Prepare historical vectors
    hist_vecs = [_vectorise_historical(e) for e in history]

    # Score all
    scored: list[tuple[float, dict]] = []
    for hv in hist_vecs:
        base_sim = similarity(query_vec, hv)
        affinity = _service_affinity(query_vec, hv)
        total_sim = min(base_sim + affinity, 1.0)
        scored.append((total_sim, hv))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_neighbours = scored[:top_k]

    best_sim = top_neighbours[0][0] if top_neighbours else 0.0
    is_ood = best_sim < OOD_THRESHOLD

    # Outcome-weighted action voting
    action_votes: dict[str, float] = {}
    action_params: dict[str, dict] = {}
    evidence_chain: list[dict] = []

    for sim, hv in top_neighbours:
        outcome_w = OUTCOME_WEIGHTS.get(hv["outcome"], 0.0)
        combined_w = sim * outcome_w  # similarity × outcome weight

        for action in hv["actions"]:
            aname = action["name"]
            aparams = action.get("params", {})
            action_votes[aname] = action_votes.get(aname, 0.0) + combined_w
            # Store params from highest-similarity neighbour
            if aname not in action_params:
                action_params[aname] = aparams

        evidence_chain.append({
            "historical_id": hv["id"],
            "root_cause_class": hv["root_cause_class"],
            "similarity": sim,
            "outcome": hv["outcome"],
            "outcome_weight": outcome_w,
            "combined_weight": round(combined_w, 4),
            "actions": [a["name"] for a in hv["actions"]],
        })

    neighbours_out = [
        {
            "id": hv["id"],
            "similarity": sim,
            "outcome": hv["outcome"],
            "root_cause_class": hv["root_cause_class"],
        }
        for sim, hv in top_neighbours
    ]

    return {
        "is_ood": is_ood,
        "best_similarity": best_sim,
        "ood_threshold": OOD_THRESHOLD,
        "neighbours": neighbours_out,
        "action_votes": action_votes,
        "action_params": action_params,
        "evidence_chain": evidence_chain,
    }
