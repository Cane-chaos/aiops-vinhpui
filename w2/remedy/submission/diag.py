import json
from features import extract_features
from retrieval import retrieve_and_vote
import yaml

history = json.load(open('incidents_history.json'))

for eid in ['E03', 'E05']:
    inc = json.load(open(f'eval/{eid}.json'))
    vec = extract_features(inc)
    cands = retrieve_and_vote(vec, history)
    trigger = inc["trigger_alert"]
    log_tmpl = vec["log_template_set"]
    dom_svc = vec["dominant_log_service"]
    anom_count = len(vec["anomalous_edges"])
    is_ood = cands["is_ood"]
    best_sim = cands["best_similarity"]
    votes = cands["action_votes"]
    nbrs = [(n["id"], n["similarity"], n["outcome"]) for n in cands["neighbours"][:3]]
    log_tmpl_counts = vec.get("log_template_counts", {})
    print(f'=== {eid} ===')
    print(f'  trigger: {trigger}')
    print(f'  log_templates: {log_tmpl}')
    print(f'  log_template_counts: {log_tmpl_counts}')
    print(f'  dominant_log_svc: {dom_svc}')
    print(f'  anomalous_edges count: {anom_count}')
    if anom_count > 0:
        for ae in vec["anomalous_edges"][:3]:
            print(f'    edge: {ae}')
    print(f'  is_ood: {is_ood}')
    print(f'  best_sim: {best_sim}')
    print(f'  votes: {votes}')
    print(f'  top neighbours: {nbrs}')
    print()
