"""Structured JSON logger for the closed-loop orchestrator.

Dual-write: every record goes to stdout AND to audit_log.jsonl (append mode).
The audit log path defaults to ./audit_log.jsonl but can be overridden via
the AUDIT_LOG_PATH environment variable (needed for Docker / Promtail mount).

Every record includes default fields `service`, `action`, `result` so that
every log event satisfies the HANDOUT requirement (ts, event_type, service,
action, result).
"""

import json
import os
from datetime import datetime, timezone

_AUDIT_LOG_PATH = os.environ.get("AUDIT_LOG_PATH", "audit_log.jsonl")


class JsonLogger:
    """Emit structured JSON log records to stdout + audit_log.jsonl."""

    def __init__(self, name: str):
        self._name = name

    def _emit(self, level: str, event_type: str, **kwargs):
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "event_type": event_type,
            # HANDOUT required defaults — caller can override via kwargs
            "service": kwargs.pop("service", "N/A"),
            "action": kwargs.pop("action", "N/A"),
            "result": kwargs.pop("result", "N/A"),
            **kwargs,
        }
        line = json.dumps(record)
        print(line, flush=True)
        # Append to audit log file for Promtail / Grafana dashboard
        try:
            with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # non-critical — do not crash orchestrator over log I/O

    def info(self, event_type: str, **kwargs):
        self._emit("INFO", event_type, **kwargs)

    def warning(self, event_type: str, **kwargs):
        self._emit("WARNING", event_type, **kwargs)

    def error(self, event_type: str, **kwargs):
        self._emit("ERROR", event_type, **kwargs)
