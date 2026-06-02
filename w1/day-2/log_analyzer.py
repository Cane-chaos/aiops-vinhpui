"""
W1-D2 Mini Log Analyzer
Usage: python log_analyzer.py <logfile>
Example: python log_analyzer.py data/HDFS_2k.log
"""

import sys
import os
import re
from datetime import datetime, timezone
import pandas as pd
import numpy as np

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig


def create_miner(sim_th=0.5, depth=4):
    """Create and return a configured Drain3 TemplateMiner."""
    config = TemplateMinerConfig()
    config.drain_sim_th = sim_th
    config.drain_depth = depth
    config.drain_max_children = 100
    config.parametrize_numeric_tokens = True
    miner = TemplateMiner(config=config)
    return miner


def parse_timestamp(line):
    """
    Parse HDFS timestamp from log line.
    Format: YYMMDD HHMMSS ...
    Returns datetime or None if parse fails.
    """
    try:
        parts = line.split()
        if len(parts) >= 2:
            date_str = parts[0]
            time_str = parts[1]
            if len(date_str) == 6 and len(time_str) == 6:
                dt = datetime.strptime(f"20{date_str} {time_str}", "%Y%m%d %H%M%S")
                return dt
    except Exception:
        pass
    return None


def parse_log_file(logfile):
    """
    Parse a log file using Drain3.
    Returns a DataFrame with columns: line_id, raw_log, timestamp, template_id, template, change_type
    """
    if not os.path.exists(logfile):
        print(f"[ERROR] File not found: {logfile}")
        sys.exit(1)

    with open(logfile, "r", encoding="utf-8", errors="replace") as f:
        lines = [ln.rstrip("\n\r") for ln in f.readlines()]

    miner = create_miner(sim_th=0.5, depth=4)

    records = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        result = miner.add_log_message(line)
        ts = parse_timestamp(line)
        records.append({
            "line_id": i + 1,
            "raw_log": line,
            "timestamp": ts,
            "template_id": result["cluster_id"],
            "template": result["template_mined"],
            "change_type": result["change_type"],
        })

    df = pd.DataFrame(records)
    return df


def detect_recent_spikes(parsed_df, recent_hours=1, threshold=3.0):
    """
    Detect templates that spiked in the last `recent_hours` relative to historical mean.
    Returns list of (template_id, template, recent_count, hist_mean, z_score) or empty.
    """
    df = parsed_df.copy()
    df = df.dropna(subset=["timestamp"])

    if df.empty or len(df["timestamp"].dropna()) == 0:
        return None  # Not enough timestamp data

    max_ts = df["timestamp"].max()
    cutoff = max_ts - pd.Timedelta(hours=recent_hours)
    recent_df = df[df["timestamp"] >= cutoff]
    hist_df = df[df["timestamp"] < cutoff]

    if recent_df.empty or hist_df.empty:
        return None

    recent_counts = recent_df.groupby("template_id").size().rename("recent_count")
    hist_counts = hist_df.groupby("template_id").size().rename("hist_count")

    combined = pd.concat([recent_counts, hist_counts], axis=1).fillna(0)
    combined["hist_mean"] = combined["hist_count"] / max(
        (max_ts - df["timestamp"].min()).total_seconds() / 3600 - recent_hours, 1
    )
    combined["hist_std"] = 0.0  # single bin, approximate
    combined["z_score"] = (combined["recent_count"] - combined["hist_mean"]) / (combined["hist_mean"].replace(0, np.nan) + 1e-6)

    spikes = combined[combined["z_score"] > threshold].reset_index()

    # Attach template text
    template_map = parsed_df.drop_duplicates("template_id").set_index("template_id")["template"]
    spikes["template"] = spikes["template_id"].map(template_map)
    return spikes


def detect_new_templates_recent(parsed_df, recent_hours=1):
    """
    Detect templates that first appeared within the last `recent_hours`.
    Returns list of template_id or empty list.
    """
    df = parsed_df.copy()
    df = df.dropna(subset=["timestamp"])

    if df.empty:
        return []

    max_ts = df["timestamp"].max()
    cutoff = max_ts - pd.Timedelta(hours=recent_hours)

    first_seen = df.groupby("template_id")["timestamp"].min().reset_index()
    first_seen.columns = ["template_id", "first_seen"]
    new_templates = first_seen[first_seen["first_seen"] >= cutoff]

    template_map = df.drop_duplicates("template_id").set_index("template_id")["template"]
    new_templates = new_templates.copy()
    new_templates["template"] = new_templates["template_id"].map(template_map)
    return new_templates


def main():
    if len(sys.argv) < 2:
        print("Usage: python log_analyzer.py <logfile>")
        sys.exit(1)

    logfile = sys.argv[1]
    print(f"\n{'='*60}")
    print(f"  W1-D2 Mini Log Analyzer")
    print(f"  File: {logfile}")
    print(f"{'='*60}\n")

    # Parse log file
    parsed_df = parse_log_file(logfile)

    total_lines = len(parsed_df)
    unique_templates = parsed_df["template_id"].nunique()

    # 1. Total lines
    print(f"[1] Total log lines: {total_lines}")

    # 2. Unique templates
    print(f"[2] Unique templates: {unique_templates}")

    # 3. Top-5 templates
    template_counts = (
        parsed_df.groupby(["template_id", "template"])
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
        .head(5)
    )
    template_counts["percentage"] = (template_counts["count"] / total_lines * 100).round(2)

    print(f"\n[3] Top-5 Templates:")
    print(f"  {'Rank':<5} {'ID':<6} {'Count':<8} {'%':<8} Template")
    print(f"  {'-'*5} {'-'*6} {'-'*8} {'-'*8} {'-'*40}")
    for rank, (_, row) in enumerate(template_counts.iterrows(), 1):
        tmpl_short = row["template"][:60] + ("..." if len(row["template"]) > 60 else "")
        print(f"  {rank:<5} {row['template_id']:<6} {row['count']:<8} {row['percentage']:<8} {tmpl_short}")

    # 4. Recent spike detection
    print(f"\n[4] Template spike in last 1 hour vs historical average:")
    ts_available = parsed_df["timestamp"].notna().sum()
    if ts_available == 0:
        print("  Not enough timestamp data for recent spike detection.")
    else:
        spikes = detect_recent_spikes(parsed_df, recent_hours=1, threshold=3.0)
        if spikes is None:
            print("  Not enough timestamp data for recent spike detection.")
        elif len(spikes) == 0:
            print("  No spike detected (z-score > 3.0) in last 1 hour.")
        else:
            print(f"  {'ID':<6} {'Recent':<10} {'Hist Mean':<12} {'Z-Score':<10} Template")
            print(f"  {'-'*6} {'-'*10} {'-'*12} {'-'*10} {'-'*40}")
            for _, row in spikes.iterrows():
                tmpl_short = str(row.get("template", ""))[:50]
                print(f"  {row['template_id']:<6} {row['recent_count']:<10.0f} {row['hist_mean']:<12.2f} {row['z_score']:<10.2f} {tmpl_short}")

    # 5. New template detection
    print(f"\n[5] New templates not seen before last 1 hour:")
    ts_available = parsed_df["timestamp"].notna().sum()
    if ts_available == 0:
        print("  Not enough timestamp data for new template detection.")
    else:
        new_templates = detect_new_templates_recent(parsed_df, recent_hours=1)
        if new_templates is None or len(new_templates) == 0:
            print("  No new templates detected in last 1 hour.")
        else:
            print(f"  {'ID':<6} {'First Seen':<22} Template")
            print(f"  {'-'*6} {'-'*22} {'-'*40}")
            for _, row in new_templates.iterrows():
                tmpl_short = str(row.get("template", ""))[:50]
                print(f"  {row['template_id']:<6} {str(row['first_seen']):<22} {tmpl_short}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
