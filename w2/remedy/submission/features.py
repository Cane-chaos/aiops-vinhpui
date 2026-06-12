"""Layer 1 — Feature extraction.

Converts a raw incident JSON into a structured incident_vector that can be
compared against historical corpus entries.

Design rationale:
- We bridge the representation gap: live incidents have raw logs/traces,
  while historical entries have cleaned templates and aggregated signatures.
- We extract signals from BOTH logs AND traces (mandatory per HANDOUT §3).
- We deliberately avoid metric-only features because metrics drift slowly
  and are weak signal for incident class (per HANDOUT §3, Layer 1).
- Feature vector is 8-dimensional — small enough to avoid overfitting on
  ~30 historical samples, yet expressive enough to distinguish key classes.
"""
from __future__ import annotations
import re
from collections import Counter
from typing import Any


# ---------------------------------------------------------------------------
# Log clustering (lightweight Drain-inspired template matching)
# ---------------------------------------------------------------------------
# Pre-defined template patterns covering all known historical log signatures.
# This avoids full Drain (too heavy for 30 entries) while still normalising
# raw lines into comparable templates.

_LOG_TEMPLATES: list[tuple[str, str]] = [
    # pattern_regex                                   -> template_id
    (r"ConnectionPool.*timeout acquiring connection", "pool_timeout"),
    (r"pool exhausted|pool_exhausted",                "pool_exhausted"),
    (r"Failed to forward request.*pool",              "pool_forward_fail"),
    (r"deadlock detected",                            "deadlock"),
    (r"lock timeout exceeded",                        "lock_timeout"),
    (r"OOM|Out of Memory|OutOfMemoryError",           "oom"),
    (r"cgroup OOM kill|evict",                        "eviction"),
    (r"GC pause.*full GC",                            "full_gc"),
    (r"TLS handshake failed|certificate.*expired",    "tls_expiry"),
    (r"x509.*certificate",                            "tls_cert_error"),
    (r"consumer rebalance",                           "kafka_rebalance"),
    (r"partition reassignment",                       "kafka_partition"),
    (r"Retry exhausted",                              "retry_exhausted"),
    (r"fallback failed.*retry",                       "retry_fallback"),
    (r"feature distribution drift|model inference confidence", "model_drift"),
    (r"rate limit exceeded|429 returned",             "rate_limit"),
    (r"query took longer than threshold",             "slow_query"),
    (r"DB query latency.*5s",                         "db_latency_high"),
    (r"degraded behavior detected",                   "degraded_generic"),
    (r"service error rate elevated",                  "error_rate_high"),
    (r"informer.cache.*stale|k8s.*throttle",          "k8s_infra"),
    (r"replication lag|replica.*behind",              "replication_lag"),
    (r"data pipeline|pipeline.*lag",                  "pipeline_lag"),
    (r"error|ERROR|CRITICAL",                         "generic_error"),
    (r"warn|WARN|WARNING",                            "generic_warn"),
]

_COMPILED: list[tuple[re.Pattern, str]] = [
    (re.compile(pat, re.IGNORECASE), tid) for pat, tid in _LOG_TEMPLATES
]


def _cluster_log_line(msg: str) -> str:
    """Map a raw log message to its template ID."""
    for pattern, tid in _COMPILED:
        if pattern.search(msg):
            return tid
    return "unknown"


def _extract_log_features(logs: list[dict]) -> dict[str, Any]:
    """Summarise raw log lines into a template histogram and key signals."""
    if not logs:
        return {
            "log_template_set": set(),
            "log_error_rate": 0.0,
            "dominant_log_service": None,
            "log_volume": 0,
        }

    template_counts: Counter = Counter()
    error_count = 0
    service_counts: Counter = Counter()

    for entry in logs:
        msg = entry.get("msg", "")
        tid = _cluster_log_line(msg)
        template_counts[tid] += 1

        level = entry.get("level", "").upper()
        if level in ("ERROR", "CRITICAL", "FATAL"):
            error_count += 1

        svc = entry.get("svc", "")
        if svc:
            service_counts[svc] += 1

    dominant_svc = service_counts.most_common(1)[0][0] if service_counts else None
    log_error_rate = error_count / max(len(logs), 1)
    template_set = set(template_counts.keys()) - {"unknown"}

    return {
        "log_template_set": template_set,
        "log_error_rate": log_error_rate,
        "dominant_log_service": dominant_svc,
        "log_volume": len(logs),
        "log_template_counts": dict(template_counts),
    }


# ---------------------------------------------------------------------------
# Trace feature extraction
# ---------------------------------------------------------------------------

def _extract_trace_features(traces: list[dict]) -> dict[str, Any]:
    """Summarise trace records into edge-level deviation signals."""
    if not traces:
        return {
            "trace_edges": [],
            "max_error_rate": 0.0,
            "max_p99_ms": 0.0,
            "anomalous_edges": [],
        }

    anomalous: list[dict] = []
    max_err = 0.0
    max_p99 = 0.0

    for t in traces:
        err = t.get("error_count", 0) / max(t.get("count", 1), 1)
        p99 = t.get("p99_ms", 0.0)
        max_err = max(max_err, err)
        max_p99 = max(max_p99, p99)

        # Anomaly threshold: error_rate > 10% OR p99 > 1000ms
        if err > 0.10 or p99 > 1000:
            anomalous.append({
                "from": t.get("from", ""),
                "to": t.get("to", ""),
                "error_rate": round(err, 3),
                "p99_ms": p99,
            })

    edges = [{"from": t.get("from", ""), "to": t.get("to", "")} for t in traces]

    return {
        "trace_edges": edges,
        "max_error_rate": round(max_err, 3),
        "max_p99_ms": round(max_p99, 1),
        "anomalous_edges": anomalous,
    }


# ---------------------------------------------------------------------------
# Metric feature extraction (supplementary, not primary signal)
# ---------------------------------------------------------------------------

def _extract_metric_features(metrics_window: dict) -> dict[str, Any]:
    """Extract key metric trends. Used as tiebreaker, not primary signal."""
    samples: dict = metrics_window.get("samples", {})
    spikes: dict[str, float] = {}

    for key, series in samples.items():
        if not series or not isinstance(series, list):
            continue
        values = [v for _, v in series if isinstance(v, (int, float))]
        if len(values) < 2:
            continue
        # Compare last-quarter average vs first-quarter average
        n = len(values)
        q = max(n // 4, 1)
        baseline_avg = sum(values[:q]) / q
        recent_avg = sum(values[-q:]) / q
        if baseline_avg > 0:
            ratio = recent_avg / baseline_avg
            if ratio > 1.5:  # 50% increase
                spikes[key] = round(ratio, 2)

    return {"metric_spikes": spikes}


# ---------------------------------------------------------------------------
# Topology context
# ---------------------------------------------------------------------------

def _extract_topology_context(
    trigger_svc: str, topology: dict
) -> dict[str, Any]:
    """Find services downstream of the trigger service (blast radius context)."""
    edges = topology.get("edges", [])
    downstream: set[str] = set()
    upstream: set[str] = set()

    for e in edges:
        if e.get("from") == trigger_svc:
            downstream.add(e.get("to", ""))
        if e.get("to") == trigger_svc:
            upstream.add(e.get("from", ""))

    return {
        "trigger_service": trigger_svc,
        "downstream_services": list(downstream),
        "upstream_services": list(upstream),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_features(incident: dict) -> dict:
    """Layer 1 public entry point.

    Returns an incident_vector with keys:
      - log_template_set: set of matched template IDs
      - log_error_rate: fraction of ERROR-level lines
      - dominant_log_service: service with most log lines
      - log_volume: total log lines
      - trace_edges: list of {from, to}
      - max_error_rate: highest error_rate across trace edges
      - max_p99_ms: highest p99 latency across trace edges
      - anomalous_edges: edges with error_rate > 10% or p99 > 1000ms
      - metric_spikes: metrics with ≥50% increase baseline→recent
      - trigger_service: service from trigger_alert
      - downstream_services / upstream_services
      - log_template_counts: raw template histogram (for evidence chain)
    """
    logs = incident.get("logs", [])
    traces = incident.get("traces", [])
    metrics_window = incident.get("metrics_window", {})
    topology = incident.get("topology", {})
    trigger_svc = incident.get("trigger_alert", {}).get("service", "")

    log_feat = _extract_log_features(logs)
    trace_feat = _extract_trace_features(traces)
    metric_feat = _extract_metric_features(metrics_window)
    topo_ctx = _extract_topology_context(trigger_svc, topology)

    result = {**log_feat, **trace_feat, **metric_feat, **topo_ctx}

    # ── Conflict detection ─────────────────────────────────────────────────
    # If the dominant log service is NOT present in the anomalous trace edges,
    # we have a conflicting evidence situation (like E06):
    # - Logs blame service A (e.g. payment-svc pool exhaustion)
    # - Traces show anomaly on service B → C (e.g. cart-svc → cart-redis)
    dom_log_svc = log_feat.get("dominant_log_service", "")
    anom_edges = trace_feat.get("anomalous_edges", [])
    trace_svcs: set[str] = set()
    for e in anom_edges:
        trace_svcs.add(e.get("from", ""))
        trace_svcs.add(e.get("to", ""))

    conflict_detected = (
        bool(dom_log_svc)
        and bool(trace_svcs)
        and dom_log_svc not in trace_svcs
    )

    # Primary trace service = upstream "from" service in anomalous edges
    from collections import Counter as _Counter
    trace_from_counts = _Counter(
        e.get("from", "") for e in anom_edges if e.get("from", "")
    )
    trace_primary_svc = (
        trace_from_counts.most_common(1)[0][0] if trace_from_counts else ""
    )

    result["conflict_detected"] = conflict_detected
    result["trace_primary_service"] = trace_primary_svc

    return result
