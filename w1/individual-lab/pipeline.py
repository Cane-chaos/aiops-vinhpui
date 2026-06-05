import json
from fastapi import FastAPI, Request
import uvicorn
from datetime import datetime, timezone

app = FastAPI()
ALERTS_FILE = "alerts.jsonl"

def fire_alert(timestamp, fault_type, severity, message):
    alert = {
        "timestamp": timestamp,
        "type": fault_type,
        "severity": severity,
        "message": message
    }
    with open(ALERTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(alert) + "\n")
    print(f"[ALERT] {fault_type.upper()} ({severity}): {message}")

@app.post("/ingest")
async def ingest(request: Request):
    payload = await request.json()
    metrics = payload.get("metrics", {})
    logs = payload.get("logs", [])
    timestamp = payload.get("timestamp", datetime.now(timezone.utc).isoformat())

    # 1. Check for Memory Leak
    mem_bytes = metrics.get("memory_usage_bytes", 0)
    if mem_bytes > 1_000_000_000:
        fire_alert(
            timestamp, 
            "memory_leak", 
            "critical", 
            f"Memory usage abnormally high: {mem_bytes} bytes"
        )
    
    # 2. Check for Traffic Spike
    rps = metrics.get("http_requests_per_sec", 0)
    if rps > 250:
        fire_alert(
            timestamp,
            "traffic_spike",
            "critical",
            f"Traffic spike detected, RPS: {rps}"
        )

    # 3. Check for Dependency Timeout
    timeout_rate = metrics.get("upstream_timeout_rate", 0)
    if timeout_rate > 2.0:
        fire_alert(
            timestamp,
            "dependency_timeout",
            "critical",
            f"High upstream timeout rate: {timeout_rate}%"
        )
        
    # 4. Check Logs for explicit errors
    for log_entry in logs:
        msg = log_entry.get("message", "")
        level = log_entry.get("level", "INFO")
        
        if "OutOfMemoryWarning" in msg or "GC pause exceeded threshold" in msg:
            fire_alert(timestamp, "memory_leak", "critical", msg)
        elif "Queue depth high" in msg or "Request rejected" in msg:
            fire_alert(timestamp, "traffic_spike", "critical", msg)
        elif "Upstream timeout rate=" in msg or "Circuit breaker OPEN" in msg:
            fire_alert(timestamp, "dependency_timeout", "critical", msg)

    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
