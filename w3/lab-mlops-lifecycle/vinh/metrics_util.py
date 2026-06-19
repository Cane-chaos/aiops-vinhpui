"""metrics_util.py — Đẩy MLOps lifecycle metrics lên Prometheus Pushgateway.

Requires: prometheus_client
URL: env PUSHGATEWAY_URL (default http://localhost:9091)

Tất cả push calls là best-effort: nếu gateway unreachable thì print warning,
không bao giờ raise exception để không làm crash pipeline chính.
"""

import os

from prometheus_client import CollectorRegistry, Counter, Gauge, push_to_gateway

PUSHGATEWAY_URL = os.environ.get("PUSHGATEWAY_URL", "http://localhost:9091")


def _new_registry() -> CollectorRegistry:
    """Tạo fresh registry mỗi lần push để tránh duplicate metric errors."""
    return CollectorRegistry()


def push_drift_score(score: float, threshold: float) -> None:
    """Push drift score + threshold + flag lên pushgateway (job=drift_detector)."""
    reg = _new_registry()
    g_score = Gauge("mlops_drift_score", "Fraction of features drifted (0–1)", registry=reg)
    g_thresh = Gauge("mlops_drift_threshold", "Configured drift threshold", registry=reg)
    g_flag = Gauge("mlops_drift_is_drift", "1 if drift detected, 0 otherwise", registry=reg)

    g_score.set(score)
    g_thresh.set(threshold)
    g_flag.set(1.0 if score > threshold else 0.0)

    try:
        push_to_gateway(PUSHGATEWAY_URL, job="drift_detector", registry=reg)
    except Exception as exc:
        print(f"[metrics_util] WARNING: pushgateway unreachable — {exc}")


def push_model_eval(version: str, precision: float, recall: float, f1: float) -> None:
    """Push per-version precision/recall/f1 lên pushgateway (job=retrain)."""
    reg = _new_registry()
    g_p = Gauge("mlops_model_precision", "Model precision on eval set", ["version"], registry=reg)
    g_r = Gauge("mlops_model_recall", "Model recall on eval set", ["version"], registry=reg)
    g_f = Gauge("mlops_model_f1", "Model F1 on eval set", ["version"], registry=reg)

    g_p.labels(version=version).set(precision)
    g_r.labels(version=version).set(recall)
    g_f.labels(version=version).set(f1)

    try:
        push_to_gateway(PUSHGATEWAY_URL, job="retrain", registry=reg)
    except Exception as exc:
        print(f"[metrics_util] WARNING: pushgateway unreachable — {exc}")


def push_event(event_type: str, version: str) -> None:
    """Increment lifecycle event counter lên pushgateway (job=retrain).

    event_type: 'retrain_triggered' | 'auto_rollback_v2_to_v1'
    """
    reg = _new_registry()
    c = Counter(
        f"mlops_{event_type}_total",
        f"Total count of {event_type} events",
        ["version"],
        registry=reg,
    )
    c.labels(version=version).inc()

    try:
        push_to_gateway(PUSHGATEWAY_URL, job="retrain", registry=reg)
    except Exception as exc:
        print(f"[metrics_util] WARNING: pushgateway unreachable — {exc}")


def push_active_version(version: str, alias: str) -> None:
    """Push version number hiện tại của alias lên pushgateway (job=retrain)."""
    reg = _new_registry()
    g_num = Gauge(
        "mlops_active_version_number",
        "Integer version number for the given alias",
        ["alias"],
        registry=reg,
    )
    g_info = Gauge(
        "mlops_active_version_info",
        "Label-only gauge: alias→version mapping (value always 1)",
        ["alias", "version"],
        registry=reg,
    )

    try:
        g_num.labels(alias=alias).set(int(version))
    except ValueError:
        g_num.labels(alias=alias).set(0)

    g_info.labels(alias=alias, version=version).set(1)

    try:
        push_to_gateway(PUSHGATEWAY_URL, job="retrain", registry=reg)
    except Exception as exc:
        print(f"[metrics_util] WARNING: pushgateway unreachable — {exc}")
