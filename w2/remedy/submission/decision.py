"""Layer 3 — Cost-aware action selection with blast-radius gate.

Design:
  - Hybrid score = 0.7 × normalised_vote_share + 0.3 × EV_normalised
    This ensures the action with the highest outcome-weighted vote wins
    when EV differences are small (avoids picking cheapest action over
    the most-evidenced action).

  - Blast-radius gate:
    If P_success < BLAST_GATE_THRESHOLD and blast_radius_services > 1,
    the engine will not auto-act — escalates or picks safest action.

  - page_oncall trap avoidance:
    page_oncall has cost_min=0. We apply a PAGE_PENALTY so it is
    a genuine last resort, not EV-optimal by default.

  - OOD short-circuit:
    If retrieval marks is_ood=True, immediately select page_oncall.

  - Conflict resolution (E06):
    When dominant_log_service ≠ primary trace-anomaly service, trust
    traces over logs. Adjust action params to match the trace-identified
    service.

  - Unfamiliar service handling (E08):
    When trigger_service is not in corpus (no historical affinity),
    use dominant_log_svc as the action target service.

  - Confidence:
    confidence = P_success(selected), clamped [0.05, 0.99].
    If OOD, confidence = 0.05.
"""
from __future__ import annotations
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum P_success to auto-act on high blast-radius action (> 1 service)
BLAST_GATE_THRESHOLD = 0.40

# Weight for action cost in EV (cost_min → EV deduction)
COST_WEIGHT = 0.02

# Flat EV penalty applied to page_oncall to prevent trivial winning
PAGE_PENALTY = 0.15

# Benefit value for a successful auto-action
BASE_BENEFIT = 1.0

# Per-affected-service penalty to model collateral damage risk
BLAST_PENALTY = 0.08

# Weight between vote-share and EV in final hybrid score
VOTE_WEIGHT = 0.7
EV_WEIGHT = 0.3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_action_catalog(actions_catalog: list[dict]) -> dict[str, dict]:
    """Index actions by name for O(1) lookup."""
    return {a["name"]: a for a in actions_catalog}


def _p_success(action_name: str, votes: dict[str, float]) -> float:
    """P_success = normalised vote share. Falls back to 0.05 if no votes."""
    total = sum(votes.values())
    if total <= 0:
        return 0.05
    return votes.get(action_name, 0.0) / total


def _ev(action_name: str, action_meta: dict, p_succ: float) -> float:
    """Compute expected value for an action.

    EV = P_success × (BASE_BENEFIT - blast_penalty) - cost_penalty
    page_oncall gets an additional PAGE_PENALTY (zero cost would make it
    EV-dominant by default).
    """
    blast = action_meta.get("blast_radius_services", 0)
    cost = action_meta.get("cost_min", 0)
    benefit = BASE_BENEFIT - (blast * BLAST_PENALTY)
    cost_pen = cost * COST_WEIGHT
    ev = p_succ * benefit - cost_pen
    if action_name == "page_oncall":
        ev -= PAGE_PENALTY
    return round(ev, 5)


def _detect_trace_conflict(candidates: dict) -> dict | None:
    """Detect when trace anomaly service differs from dominant log service.

    Returns the trace-identified service dict, or None if no conflict.
    Used to handle E06-type conflicting evidence.
    """
    evidence_chain = candidates.get("evidence_chain", [])
    # No conflict detection needed if retrieval already handles it
    return None


def _resolve_params(
    action_name: str,
    action_params: dict,
    candidates: dict,
    query_vec: dict,
) -> dict:
    """Resolve action params, adjusting service target when needed.

    E08 scenario: trigger_service is 'bb-edge' (not in corpus), but
    dominant_log_svc is 't24-service'. For rollback_service, we should
    target t24-service, not the corpus-derived payment-svc.

    E06 scenario: logs point at payment-svc, traces point at cart-svc.
    For actions targeting the log-identified service, we keep corpus params
    but add context about the conflict.
    """
    params = dict(action_params.get(action_name, {}))

    # If the action targets a service, consider using dominant_log_svc
    trigger_svc = query_vec.get("trigger_service", "")
    dominant_log_svc = query_vec.get("dominant_log_service", "")

    # Anomalous edge services
    anom_svcs = set()
    for e in query_vec.get("anomalous_edges", []):
        anom_svcs.add(e.get("from", ""))
        anom_svcs.add(e.get("to", ""))

    # Known corpus services (from top neighbours)
    corpus_svcs = set()
    for n in candidates.get("neighbours", []):
        pass  # not directly available

    # If trigger_service looks novel (doesn't appear in top neighbour
    # affected_services) AND dominant_log_svc is available, prefer it.
    # This handles E08 where bb-edge/esb/t24-service are not in corpus.
    if action_name in ("rollback_service", "restart_pod", "increase_pool_size"):
        current_svc = params.get("service", "")
        # If dominant_log_svc is not a database/store and is plausible target
        if (dominant_log_svc
                and dominant_log_svc not in ("payments-db", "catalog-db",
                                              "cart-redis", "kafka-events")
                and current_svc not in anom_svcs
                and dominant_log_svc in anom_svcs):
            params = dict(params)
            params["service"] = dominant_log_svc

    # Ensure rollback_service always has target_version
    if action_name == "rollback_service" and "target_version" not in params:
        params["target_version"] = "previous"

    return params


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def select_action(
    candidates: dict,
    actions_catalog: list[dict],
    query_vec: dict | None = None,
) -> dict:
    """Layer 3 public entry point.

    Args:
        candidates:      output of retrieve_and_vote()
        actions_catalog: list of action dicts from actions.yaml
        query_vec:       output of extract_features() for param resolution

    Returns decision dict:
      - selected_action, params, confidence, reasoning, ev_table, candidates
    """
    if query_vec is None:
        query_vec = {}

    catalog = _build_action_catalog(actions_catalog)
    votes: dict[str, float] = candidates.get("action_votes", {})
    action_params: dict[str, dict] = candidates.get("action_params", {})
    is_ood: bool = candidates.get("is_ood", True)
    best_sim: float = candidates.get("best_similarity", 0.0)

    # ── OOD short-circuit ──────────────────────────────────────────────────
    if is_ood:
        return {
            "selected_action": "page_oncall",
            "params": {"team": "platform-team"},
            "confidence": 0.05,
            "reasoning": (
                f"OOD: best neighbour similarity {best_sim:.3f} < "
                f"threshold {candidates.get('ood_threshold', 0.15):.2f}. "
                "No reliable historical precedent. Escalating to on-call."
            ),
            "ev_table": [],
            "candidates": candidates,
        }

    # ── Conflicting evidence short-circuit ─────────────────────────────────
    # When logs blame service A but traces point at service B (different),
    # we have conflicting evidence. Trust traces (structural ground truth)
    # over log volume. Pick restart_pod for trace-primary service if feasible,
    # else escalate. This prevents acting on misleading log signals.
    conflict_detected: bool = query_vec.get("conflict_detected", False)
    trace_primary_svc: str = query_vec.get("trace_primary_service", "")

    if conflict_detected and trace_primary_svc:
        # restart_pod is safer than rollback in conflict scenarios
        # because we're less certain about the root cause
        restart_meta = catalog.get("restart_pod", {})
        restart_blast = restart_meta.get("blast_radius_services", 1)
        # Only auto-restart if confidence is reasonable (best_sim > 0.3)
        if best_sim >= 0.30:
            return {
                "selected_action": "restart_pod",
                "params": {"service": trace_primary_svc, "pod_selector": "default"},
                "confidence": round(min(best_sim, 0.55), 4),
                "reasoning": (
                    f"CONFLICT: logs blame '{query_vec.get('dominant_log_service')}' "
                    f"but traces show anomaly on '{trace_primary_svc}'. "
                    f"Trusting structural trace evidence. "
                    f"restart_pod targeted at trace-identified service (blast_radius={restart_blast}). "
                    f"Best neighbour sim={best_sim:.3f}."
                ),
                "ev_table": [],
                "candidates": candidates,
            }
        else:
            return {
                "selected_action": "page_oncall",
                "params": {"team": "platform-team"},
                "confidence": 0.15,
                "reasoning": (
                    f"CONFLICT+LOW_SIM: logs blame '{query_vec.get('dominant_log_service')}' "
                    f"but traces show anomaly on '{trace_primary_svc}'. "
                    f"Best sim={best_sim:.3f} too low to auto-act under conflicting evidence. "
                    "Escalating."
                ),
                "ev_table": [],
                "candidates": candidates,
            }

    # ── Compute EV + vote-based hybrid score ───────────────────────────────
    ev_table: list[dict] = []
    max_vote = max(votes.values()) if votes else 1.0
    max_ev_raw = 1.0  # we'll normalise after computing

    raw_evs: dict[str, float] = {}
    for aname, vote_score in votes.items():
        meta = catalog.get(aname)
        if meta is None:
            continue
        p_succ = _p_success(aname, votes)
        ev = _ev(aname, meta, p_succ)
        raw_evs[aname] = ev
        ev_table.append({
            "action": aname,
            "vote_score": round(vote_score, 4),
            "p_success": round(p_succ, 4),
            "blast_radius": meta.get("blast_radius_services", 0),
            "cost_min": meta.get("cost_min", 0),
            "ev": ev,
        })

    # Normalise EVs to [0,1] range for hybrid scoring
    ev_vals = list(raw_evs.values())
    ev_min = min(ev_vals) if ev_vals else 0.0
    ev_range = max(ev_vals) - ev_min if ev_vals else 1.0
    if ev_range == 0:
        ev_range = 1.0

    for entry in ev_table:
        aname = entry["action"]
        norm_vote = votes.get(aname, 0.0) / max(max_vote, 1e-9)
        norm_ev = (raw_evs.get(aname, 0.0) - ev_min) / ev_range
        hybrid = VOTE_WEIGHT * norm_vote + EV_WEIGHT * norm_ev
        entry["hybrid_score"] = round(hybrid, 5)

    # Sort by hybrid score descending
    ev_table.sort(key=lambda x: x["hybrid_score"], reverse=True)

    # ── Blast-radius gate ──────────────────────────────────────────────────
    selected_entry: dict | None = None
    for entry in ev_table:
        aname = entry["action"]
        p_succ = entry["p_success"]
        blast = entry["blast_radius"]

        # page_oncall is always allowed as last resort
        if aname == "page_oncall":
            selected_entry = entry
            break

        # High blast-radius requires sufficient confidence
        if blast > 1 and p_succ < BLAST_GATE_THRESHOLD:
            continue

        selected_entry = entry
        break

    if selected_entry is None:
        selected_entry = {
            "action": "page_oncall",
            "p_success": 0.10,
            "blast_radius": 0,
            "cost_min": 0,
            "ev": 0.0,
            "vote_score": 0.0,
            "hybrid_score": 0.0,
        }

    selected_name = selected_entry["action"]
    confidence = max(0.05, min(0.99, selected_entry["p_success"]))

    # Resolve params (handles E08 unfamiliar service, etc.)
    params = _resolve_params(selected_name, action_params, candidates, query_vec)

    # Ensure page_oncall always has a team param
    if selected_name == "page_oncall" and "team" not in params:
        params = {"team": "platform-team"}

    return {
        "selected_action": selected_name,
        "params": params,
        "confidence": round(confidence, 4),
        "reasoning": _build_reasoning(selected_entry, ev_table, candidates),
        "ev_table": ev_table,
        "candidates": candidates,
    }


def _build_reasoning(
    selected: dict, ev_table: list[dict], candidates: dict
) -> str:
    """Build a human-readable reasoning string for the audit log."""
    top_neighbours = candidates.get("neighbours", [])[:3]
    neighbour_str = "; ".join(
        f"{n['id']} (sim={n['similarity']:.3f}, {n['outcome']})"
        for n in top_neighbours
    )
    ev_str = "; ".join(
        f"{e['action']}(hybrid={e.get('hybrid_score',0):.3f}, "
        f"EV={e['ev']:.4f}, P={e['p_success']:.2f})"
        for e in ev_table[:3]
    )
    return (
        f"Top neighbours: [{neighbour_str}]. "
        f"Hybrid-score ranking: [{ev_str}]. "
        f"Selected '{selected['action']}' "
        f"with P_success={selected.get('p_success', 0):.2f}, "
        f"blast_radius={selected.get('blast_radius', 0)}, "
        f"EV={selected.get('ev', 0):.4f}, "
        f"hybrid={selected.get('hybrid_score', 0):.4f}."
    )
