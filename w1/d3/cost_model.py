"""
cost_model.py — Observability Platform Cost Estimation
W1-D3: Data Layer Architecture + Observability Pipeline

Estimates monthly infrastructure cost for 3 tiers:
  Small  : 10  services, 50  GB log/day, 100K metric events/sec
  Medium : 100 services, 500 GB log/day, 1M   metric events/sec
  Large  : 1K  services, 5   TB log/day, 10M  metric events/sec

All cost figures are rough estimates (USD/month) based on public
AWS/GCP/Confluent/Datadog pricing as of 2025. Adjust as needed.
"""

from pathlib import Path
import csv
import json

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results"

# ═══════════════════════════════════════════════════════════════════════════
# ASSUMPTIONS (documented inline below)
# ═══════════════════════════════════════════════════════════════════════════

# Metric Storage (VictoriaMetrics / Prometheus)
#   ~$0.003 / million events ingested   (self-hosted on c6i EC2)
#   Retention 15 days on hot storage
METRIC_COST_PER_M_EVENTS = 0.003   # $/million events

# Log Storage — Hot (Loki / OpenSearch)
#   ~$1.50 / GB / month for hot tier (7-day retention, SSD)
LOG_HOT_COST_PER_GB = 1.50

# Log Storage — Cold (S3 + Parquet)
#   ~$0.023 / GB / month (S3 Standard) × 30-day retention
LOG_COLD_COST_PER_GB = 0.023

# Trace Storage (Jaeger + Elasticsearch or AWS X-Ray)
#   Assume traces ~ 10% of log volume in GB
#   ~$0.80 / GB / month (hot) for ES on EC2
TRACE_COST_RATIO    = 0.10         # traces = 10% of log volume
TRACE_COST_PER_GB   = 0.80

# Kafka (Confluent Cloud or self-hosted MSK)
#   Self-hosted MSK: ~$0.50 / MBps throughput / month (broker cost)
#   Throughput (MB/s) estimated from metric events + log volume
#   log: GB/day → MB/s;  metric: events/sec × 200 bytes avg
KAFKA_COST_PER_MBPS = 0.50         # $/MBps/month

# Stream Processing (Flink on EMR / EKS)
#   ~$0.15 / vCPU-hour; scale: 1 vCPU per 500K events/sec
FLINK_VCPU_PER_500K = 1
FLINK_COST_PER_VCPU_HOUR = 0.15   # $/vCPU/hour

# Network egress
#   ~$0.09 / GB; rough 20% of log volume cross-region
NETWORK_EGRESS_RATIO     = 0.20
NETWORK_COST_PER_GB      = 0.09

# Datadog SaaS multiplier over build cost
#   Tier-based: Small 4x, Medium 3x, Large 2.5x
DATADOG_MULTIPLIER = {"Small": 4.0, "Medium": 3.0, "Large": 2.5}


# ═══════════════════════════════════════════════════════════════════════════
# TIERS
# ═══════════════════════════════════════════════════════════════════════════

TIERS = {
    "Small": {
        "services":          10,
        "log_gb_per_day":    50,
        "metric_events_sec": 100_000,
    },
    "Medium": {
        "services":          100,
        "log_gb_per_day":    500,
        "metric_events_sec": 1_000_000,
    },
    "Large": {
        "services":          1000,
        "log_gb_per_day":    5_000,   # 5 TB
        "metric_events_sec": 10_000_000,
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# COST CALCULATOR
# ═══════════════════════════════════════════════════════════════════════════

def estimate_tier(tier_name: str, cfg: dict) -> dict:
    log_gb_day    = cfg["log_gb_per_day"]
    metric_eps    = cfg["metric_events_sec"]  # events per second
    services      = cfg["services"]

    log_gb_month  = log_gb_day * 30

    # ── Metric storage ──────────────────────────────────────────────────
    metric_events_month = metric_eps * 60 * 60 * 24 * 30  # per month
    metric_storage = (metric_events_month / 1_000_000) * METRIC_COST_PER_M_EVENTS

    # ── Log hot storage (7-day retention) ────────────────────────────────
    log_hot_gb    = log_gb_day * 7
    log_hot       = log_hot_gb * LOG_HOT_COST_PER_GB

    # ── Log cold storage (30-day retention, S3) ──────────────────────────
    log_cold      = log_gb_month * LOG_COLD_COST_PER_GB

    # ── Trace storage (10% of log volume, hot) ───────────────────────────
    trace_gb_month = log_gb_month * TRACE_COST_RATIO
    trace_storage  = trace_gb_month * TRACE_COST_PER_GB

    # ── Kafka throughput ─────────────────────────────────────────────────
    # Convert log GB/day → MB/s and metrics (200 bytes avg) → MB/s
    log_mbps      = (log_gb_day * 1024) / (24 * 3600)
    metric_mbps   = (metric_eps * 200) / (1024 * 1024)
    total_mbps    = log_mbps + metric_mbps
    kafka         = total_mbps * KAFKA_COST_PER_MBPS * 30  # monthly

    # ── Stream processing (Flink) ────────────────────────────────────────
    # 1 vCPU per 500K events/sec, running 24×7
    vcpus         = max(1, metric_eps // 500_000) + (services // 100)
    flink         = vcpus * FLINK_COST_PER_VCPU_HOUR * 24 * 30

    # ── Network egress ───────────────────────────────────────────────────
    network       = log_gb_month * NETWORK_EGRESS_RATIO * NETWORK_COST_PER_GB

    # ── Build total ──────────────────────────────────────────────────────
    build_total   = (
        metric_storage + log_hot + log_cold +
        trace_storage  + kafka   + flink    + network
    )

    # ── Datadog SaaS estimate ─────────────────────────────────────────────
    dd_mult       = DATADOG_MULTIPLIER[tier_name]
    datadog_saas  = build_total * dd_mult

    return {
        "tier":                 tier_name,
        "services":             services,
        "log_gb_per_day":       log_gb_day,
        "metric_events_sec":    metric_eps,
        "metric_storage":       round(metric_storage, 2),
        "log_hot_storage":      round(log_hot, 2),
        "log_cold_storage":     round(log_cold, 2),
        "trace_storage":        round(trace_storage, 2),
        "kafka":                round(kafka, 2),
        "stream_processing":    round(flink, 2),
        "network":              round(network, 2),
        "build_total":          round(build_total, 2),
        "datadog_saas_estimate": round(datadog_saas, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════
# OUTPUT FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════

COST_COLUMNS = [
    "metric_storage", "log_hot_storage", "log_cold_storage",
    "trace_storage",  "kafka",           "stream_processing",
    "network",        "build_total",     "datadog_saas_estimate",
]

def print_table(results: list[dict]) -> None:
    tier_w = 8
    col_w  = 22
    sep    = "+" + ("-" * (tier_w + 2)) + "+" + (("+" + "-" * (col_w + 2)) * len(COST_COLUMNS)) + "+"

    print(sep)
    header_cells = ["Tier".ljust(tier_w)] + [c.ljust(col_w) for c in COST_COLUMNS]
    print("| " + " | ".join(header_cells) + " |")
    print(sep)

    for row in results:
        cells = [row["tier"].ljust(tier_w)]
        for col in COST_COLUMNS:
            val = f"${row[col]:,.2f}"
            cells.append(val.ljust(col_w))
        print("| " + " | ".join(cells) + " |")

    print(sep)
    print()


def save_csv(results: list[dict]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "cost_estimate.csv"
    fieldnames = list(results[0].keys())
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"[OUTPUT] CSV saved: {out}")
    return out


def save_md(results: list[dict]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "cost_estimate.md"

    lines = [
        "# Monthly Observability Cost Estimate",
        "",
        "> All figures are USD/month estimates based on self-hosted AWS infrastructure.",
        "> Datadog SaaS estimate = build_total × tier multiplier (Small 4×, Medium 3×, Large 2.5×).",
        "",
    ]

    # Header
    header = ["Tier"] + [c.replace("_", " ").title() for c in COST_COLUMNS]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")

    # Rows
    for row in results:
        cells = [row["tier"]]
        for col in COST_COLUMNS:
            cells.append(f"${row[col]:,.2f}")
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Cost Assumptions",
        "",
        f"- **Metric storage**: ${METRIC_COST_PER_M_EVENTS}/million events/month (VictoriaMetrics on EC2)",
        f"- **Log hot storage**: ${LOG_HOT_COST_PER_GB}/GB/month — 7-day retention (Loki/OpenSearch SSD)",
        f"- **Log cold storage**: ${LOG_COLD_COST_PER_GB}/GB/month — 30-day retention (S3 Standard)",
        f"- **Trace storage**: {TRACE_COST_RATIO*100:.0f}% of log volume × ${TRACE_COST_PER_GB}/GB/month",
        f"- **Kafka**: ${KAFKA_COST_PER_MBPS}/MBps/month (MSK self-hosted)",
        f"- **Flink**: ${FLINK_COST_PER_VCPU_HOUR}/vCPU-hour, 1 vCPU per 500K events/sec",
        f"- **Network**: {NETWORK_EGRESS_RATIO*100:.0f}% of log volume × ${NETWORK_COST_PER_GB}/GB egress",
        "",
        "## Build vs Buy Summary",
        "",
        "| Tier   | Build/Month | Datadog SaaS/Month | Savings |",
        "| ------ | ----------- | ------------------ | ------- |",
    ]

    for row in results:
        build = row["build_total"]
        saas  = row["datadog_saas_estimate"]
        saving = saas - build
        lines.append(
            f"| {row['tier']:<6} | ${build:>10,.2f} | ${saas:>18,.2f} | ${saving:>7,.2f} |"
        )

    with out.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"[OUTPUT] Markdown saved: {out}")
    return out


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    print("=" * 60)
    print("  Observability Cost Model — W1-D3")
    print("=" * 60)
    print()

    results = [estimate_tier(name, cfg) for name, cfg in TIERS.items()]

    print("Monthly Cost Breakdown (USD)\n")
    print_table(results)

    save_csv(results)
    save_md(results)

    print("\n[DONE] Cost model completed successfully.")


if __name__ == "__main__":
    main()
