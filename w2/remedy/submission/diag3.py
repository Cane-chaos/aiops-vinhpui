import json
from features import extract_features
from retrieval import retrieve_and_vote
import yaml

history = json.load(open('incidents_history.json'))
inc = json.load(open('eval/E06.json'))
vec = extract_features(inc)

trigger = inc["trigger_alert"]
print(f'trigger: {trigger}')
print(f'log_templates: {vec["log_template_set"]}')
print(f'dominant_log_svc: {vec["dominant_log_service"]}')

# What services appear in anomalous traces?
anom_svcs = {}
for e in vec.get("anomalous_edges", []):
    pair = (e["from"], e["to"])
    anom_svcs[pair] = anom_svcs.get(pair, 0) + 1

print(f'unique anomalous edge pairs: {sorted(set((e["from"], e["to"]) for e in vec["anomalous_edges"]))}')

# check top logs
logs = inc.get("logs", [])
from collections import Counter
svc_counts = Counter(lg["svc"] for lg in logs)
print(f'log svc distribution: {svc_counts.most_common(5)}')
err_by_svc = Counter(lg["svc"] for lg in logs if lg.get("level") in ("ERROR", "CRITICAL"))
print(f'error log svc: {err_by_svc.most_common(5)}')
