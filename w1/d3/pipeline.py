"""
pipeline.py — Mock Streaming Pipeline
W1-D3: Data Layer Architecture + Observability Pipeline

Simulates a streaming pipeline:
  - Producer: reads CSV row-by-row, emits events into queue.Queue
  - Consumer: reads from queue, computes rolling features
  - Output: writes features.parquet (fallback: features.json)
"""

import queue
import json
import csv
import urllib.request
from pathlib import Path
from collections import deque
from datetime import datetime

# ── Paths (relative, no hardcoded absolute paths) ──────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
RESULTS_DIR = BASE_DIR / "results"

DATA_FILE = DATA_DIR / "machine_temperature_system_failure.csv"
DATASET_URL = (
    "https://raw.githubusercontent.com/numenta/NAB/master/"
    "data/realKnownCause/machine_temperature_system_failure.csv"
)

# ── Rolling window sizes (data interval = 5 min) ────────────────────────────
WINDOW_1H = 12    # 12 × 5 min = 1 hour
WINDOW_4H = 48    # 48 × 5 min = 4 hours
LAG_1     = 1
LAG_12    = 12    # 1 hour ago


# ═══════════════════════════════════════════════════════════════════════════
# Utilities
# ═══════════════════════════════════════════════════════════════════════════

def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def download_dataset() -> None:
    """Download NAB dataset if not present."""
    if DATA_FILE.exists():
        print(f"[INFO] Dataset already exists: {DATA_FILE}")
        return
    print(f"[INFO] Downloading dataset from:\n  {DATASET_URL}")
    urllib.request.urlretrieve(DATASET_URL, DATA_FILE)
    print(f"[INFO] Saved to: {DATA_FILE}")


# ═══════════════════════════════════════════════════════════════════════════
# PRODUCER — reads CSV and pushes events into a queue
# ═══════════════════════════════════════════════════════════════════════════

def producer(event_queue: queue.Queue) -> int:
    """
    Read the CSV file row-by-row and emit each row as a dict event
    into event_queue. Returns total rows emitted.
    """
    row_count = 0
    with DATA_FILE.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            event = {
                "timestamp": row["timestamp"].strip(),
                "value": float(row["value"].strip()),
            }
            event_queue.put(event)
            row_count += 1

    # Sentinel to signal end of stream
    event_queue.put(None)
    print(f"[PRODUCER] Emitted {row_count} events into queue.")
    return row_count


# ═══════════════════════════════════════════════════════════════════════════
# CONSUMER — reads events, computes streaming features
# ═══════════════════════════════════════════════════════════════════════════

def _safe_mean(dq: deque) -> float | None:
    return sum(dq) / len(dq) if dq else None


def _safe_std(dq: deque) -> float | None:
    if len(dq) < 2:
        return None
    n = len(dq)
    mean = sum(dq) / n
    variance = sum((x - mean) ** 2 for x in dq) / (n - 1)
    return variance ** 0.5


def consumer(event_queue: queue.Queue) -> list[dict]:
    """
    Consume events from the queue. For each event compute rolling features
    and return the list of feature dicts.
    """
    history: deque = deque(maxlen=max(WINDOW_4H, LAG_12) + 1)
    features = []

    while True:
        event = event_queue.get()
        if event is None:          # sentinel
            break

        ts_str: str  = event["timestamp"]
        value: float = event["value"]

        # Parse timestamp
        try:
            ts = datetime.fromisoformat(ts_str)
        except ValueError:
            # Try common NAB format
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")

        # Append current value to history BEFORE computing features
        history.append(value)

        # Build a snapshot of the full history so we can index from the end
        hist_list = list(history)
        n = len(hist_list)

        # Rolling windows — slice the last k elements
        window_1h = deque(hist_list[-WINDOW_1H:], maxlen=WINDOW_1H)
        window_4h = deque(hist_list[-WINDOW_4H:], maxlen=WINDOW_4H)

        # Lag values
        lag_1_val  = hist_list[-2] if n >= 2  else None
        lag_12_val = hist_list[-13] if n >= 13 else None

        # Rate of change
        roc = (value - lag_1_val) if lag_1_val is not None else None

        feat = {
            "timestamp":      ts_str,
            "value":          value,
            "rolling_mean_1h": _safe_mean(window_1h),
            "rolling_std_1h":  _safe_std(window_1h),
            "rolling_mean_4h": _safe_mean(window_4h),
            "rate_of_change":  roc,
            "lag_1":           lag_1_val,
            "lag_12":          lag_12_val,
            "hour":            ts.hour,
            "day_of_week":     ts.weekday(),
        }
        features.append(feat)

    print(f"[CONSUMER] Processed {len(features)} feature rows.")
    return features


# ═══════════════════════════════════════════════════════════════════════════
# OUTPUT — save features
# ═══════════════════════════════════════════════════════════════════════════

def save_features(features: list[dict]) -> Path:
    """
    Try to save as Parquet; fall back to JSON if pyarrow/pandas unavailable.
    Returns the path of the saved file.
    """
    # Attempt Parquet via pandas + pyarrow
    try:
        import pandas as pd          # noqa: F401  (pandas available?)
        import pyarrow               # noqa: F401  (pyarrow available?)

        df = pd.DataFrame(features)
        out_path = RESULTS_DIR / "features.parquet"
        df.to_parquet(out_path, index=False, engine="pyarrow")
        print(f"[OUTPUT] Saved Parquet: {out_path}")
        return out_path

    except ImportError:
        pass  # Fall through to JSON

    # Fallback: JSON
    out_path = RESULTS_DIR / "features.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(features, f, indent=2, default=str)
    print(f"[OUTPUT] Parquet unavailable — saved JSON: {out_path}")
    return out_path


def print_sample(features: list[dict], n: int = 5) -> None:
    print(f"\n[SAMPLE] First {n} feature rows:")
    print("-" * 90)
    header = list(features[0].keys()) if features else []
    col_w = max(len(h) for h in header) + 2 if header else 14
    print("  ".join(h.ljust(col_w) for h in header))
    print("-" * 90)
    for row in features[:n]:
        vals = []
        for h in header:
            v = row[h]
            if isinstance(v, float):
                vals.append(f"{v:.4f}".ljust(col_w))
            else:
                vals.append(str(v).ljust(col_w))
        print("  ".join(vals))
    print("-" * 90)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    print("=" * 60)
    print("  Mock Streaming Pipeline — W1-D3")
    print("=" * 60)

    ensure_dirs()
    download_dataset()

    # Run pipeline (single-threaded simulation)
    q: queue.Queue = queue.Queue()

    input_rows = producer(q)
    features   = consumer(q)
    out_path   = save_features(features)

    print("\n[SUMMARY]")
    print(f"  Input rows      : {input_rows}")
    print(f"  Output feat rows: {len(features)}")
    print(f"  Output file     : {out_path}")

    if features:
        print_sample(features)

    print("\n[DONE] Pipeline completed successfully.")


if __name__ == "__main__":
    main()
