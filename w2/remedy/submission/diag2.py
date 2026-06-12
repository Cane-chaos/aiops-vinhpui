import json
from features import extract_features
from retrieval import retrieve_and_vote
import yaml

history = json.load(open('incidents_history.json'))

for eid in ['E05', 'E06', 'E08']:
    inc = json.load(open(f'eval/{eid}.json'))
    vec = extract_features(inc)
    cands = retrieve_and_vote(vec, history)
    trigger = inc["trigger_alert"]
    log_tmpl = vec["log_template_set"]
    is_ood = cands["is_ood"]
    best_sim = cands["best_similarity"]
    votes = cands["action_votes"]
    nbrs = [(n["id"], n["similarity"], n["outcome"], n["root_cause_class"]) for n in cands["neighbours"][:3]]
    anom_svcs = set()
    for e in vec.get("anomalous_edges", []):
        anom_svcs.add(e["from"])
        anom_svcs.add(e["to"])
    print(f'=== {eid} ===')
    print(f'  trigger: {trigger}')
    print(f'  log_templates: {log_tmpl}')
    print(f'  dominant_log_svc: {vec["dominant_log_service"]}')
    print(f'  anomalous_svcs: {anom_svcs}')
    print(f'  anomalous_edges count: {len(vec["anomalous_edges"])}')
    print(f'  is_ood: {is_ood}')
    print(f'  best_sim: {best_sim}')
    print(f'  votes: {votes}')
    print(f'  action_params: {cands["action_params"]}')
    print(f'  top neighbours: {nbrs}')
    # sample some logs
    logs = inc.get("logs", [])[:5]
    print(f'  sample logs:')
    for lg in logs:
        print(f'    {lg.get("svc")} [{lg.get("level")}]: {lg.get("msg", "")[:80]}')
    print()
