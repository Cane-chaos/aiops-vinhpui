"""
Alert Correlator — W2D1 Assignment
===================================
Pipeline: Dedup → Time-Window → Topology → Combined Correlation
"""

import json
import os
from datetime import datetime, timezone
from collections import defaultdict

# ---------------------------------------------------------------------------
# 0. Helpers
# ---------------------------------------------------------------------------

def load_alerts(path: str) -> list[dict]:
    """Load alerts from JSONL file."""
    alerts = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                alerts.append(json.loads(line))
    # parse timestamps
    for a in alerts:
        a['_ts'] = datetime.fromisoformat(a['ts'].replace('Z', '+00:00'))
    alerts.sort(key=lambda a: a['_ts'])
    return alerts


def load_graph(path: str):
    """Load service dependency graph from services.json and return a networkx DiGraph."""
    import networkx as nx
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    G = nx.DiGraph()
    for svc in data.get('services', []):
        G.add_node(svc['name'], **{k: v for k, v in svc.items() if k != 'name'})
    for store in data.get('stores', []):
        G.add_node(store['name'], **{k: v for k, v in store.items() if k != 'name'})
    for edge in data.get('edges', []):
        G.add_edge(edge['from'], edge['to'], type=edge.get('type', 'unknown'))
    return G


# ---------------------------------------------------------------------------
# Layer 1 — Fingerprint Dedup
# ---------------------------------------------------------------------------

def fingerprint(alert: dict) -> str:
    """service|metric|severity — stable, does not include ts or value."""
    return f"{alert['service']}|{alert['metric']}|{alert['severity']}"


def dedup(alerts: list[dict]) -> list[dict]:
    """Keep the first occurrence of each fingerprint."""
    seen = set()
    result = []
    for a in alerts:
        fp = fingerprint(a)
        if fp not in seen:
            seen.add(fp)
            a['fingerprint'] = fp
            result.append(a)
        else:
            a['fingerprint'] = fp
    return result


# ---------------------------------------------------------------------------
# Layer 2 — Time-Window Session Grouping
# ---------------------------------------------------------------------------

def session_groups(alerts: list[dict], gap_sec: int = 120) -> list[list[dict]]:
    """Group alerts into sessions where consecutive alerts are ≤ gap_sec apart."""
    if not alerts:
        return []
    alerts_sorted = sorted(alerts, key=lambda a: a['_ts'])
    sessions = [[alerts_sorted[0]]]
    for a in alerts_sorted[1:]:
        if (a['_ts'] - sessions[-1][-1]['_ts']).total_seconds() <= gap_sec:
            sessions[-1].append(a)
        else:
            sessions.append([a])
    return sessions


# ---------------------------------------------------------------------------
# Layer 3 — Topology Grouping (Union-Find on graph distance)
# ---------------------------------------------------------------------------

def topology_group(alerts: list[dict], graph, max_hop: int = 2) -> list[list[dict]]:
    """Group alerts whose services are ≤ max_hop apart on undirected graph."""
    import networkx as nx
    undirected = graph.to_undirected()
    by_service = defaultdict(list)
    for a in alerts:
        by_service[a['service']].append(a)

    services = list(by_service.keys())
    parent = {s: s for s in services}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, s1 in enumerate(services):
        for s2 in services[i + 1:]:
            try:
                if nx.shortest_path_length(undirected, s1, s2) <= max_hop:
                    parent[find(s1)] = find(s2)
            except nx.NetworkXNoPath:
                pass

    groups = defaultdict(list)
    for s in services:
        groups[find(s)].extend(by_service[s])
    return list(groups.values())


# ---------------------------------------------------------------------------
# Layer 2+3 — Combined Correlation
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {'info': 0, 'warn': 1, 'crit': 2}


def correlate(alerts: list[dict], graph, gap_sec: int = 120, max_hop: int = 2) -> list[dict]:
    """
    Full correlation pipeline:
      1. Session-group by time window
      2. Within each session, topology-group by graph distance
      3. Emit clusters with metadata
    """
    sessions = session_groups(alerts, gap_sec=gap_sec)
    clusters = []
    for s_idx, session_alerts in enumerate(sessions):
        for g_idx, group in enumerate(topology_group(session_alerts, graph, max_hop)):
            clusters.append({
                'cluster_id': f'c-{s_idx:03d}-{g_idx:03d}',
                'alert_count': len(group),
                'services': sorted({a['service'] for a in group}),
                'time_range': [
                    min(a['ts'] for a in group),
                    max(a['ts'] for a in group),
                ],
                'max_severity': max(
                    (a['severity'] for a in group),
                    key=lambda s: SEVERITY_ORDER.get(s, -1)
                ),
                'fingerprints': sorted({fingerprint(a) for a in group}),
                'alert_ids': [a['id'] for a in group],
            })
    return clusters


# ---------------------------------------------------------------------------
# Main — run the full pipeline and write results
# ---------------------------------------------------------------------------

def main():
    base = os.path.dirname(os.path.abspath(__file__))
    dataset = os.path.join(base, 'dataset')

    # 1. Load data
    print("=" * 60)
    print("Step 1: Loading data")
    print("=" * 60)
    alerts = load_alerts(os.path.join(dataset, 'alerts_sample.jsonl'))
    graph = load_graph(os.path.join(dataset, 'services.json'))
    print(f"  Loaded {len(alerts)} alerts")
    print(f"  Graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges")

    # 2. Dedup
    print("\n" + "=" * 60)
    print("Step 2: Dedup — fingerprint-based")
    print("=" * 60)
    deduped = dedup(alerts)
    print(f"  Before dedup: {len(alerts)} alerts")
    print(f"  After  dedup: {len(deduped)} unique fingerprints")
    print(f"  Removed: {len(alerts) - len(deduped)} duplicate(s)")
    dup_fps = set()
    for a in alerts:
        fp = fingerprint(a)
        if fp in dup_fps:
            continue
        count = sum(1 for x in alerts if fingerprint(x) == fp)
        if count > 1:
            dup_fps.add(fp)
            print(f"    ↳ Duplicate fingerprint: {fp} (×{count})")

    # 3. Time-window sessions
    print("\n" + "=" * 60)
    print("Step 3: Time-Window Sessions (gap_sec=120)")
    print("=" * 60)
    sessions = session_groups(alerts, gap_sec=120)
    for i, sess in enumerate(sessions):
        ts_range = f"{sess[0]['ts']} → {sess[-1]['ts']}"
        svcs = sorted({a['service'] for a in sess})
        print(f"  Session {i}: {len(sess)} alerts | {ts_range}")
        print(f"    Services: {svcs}")

    # 4. Topology groups (within full set for demo)
    print("\n" + "=" * 60)
    print("Step 4: Topology Grouping (max_hop=2)")
    print("=" * 60)
    topo_groups = topology_group(alerts, graph, max_hop=2)
    for i, grp in enumerate(topo_groups):
        svcs = sorted({a['service'] for a in grp})
        print(f"  Topo group {i}: {len(grp)} alerts | services={svcs}")

    # 5. Combined correlation
    print("\n" + "=" * 60)
    print("Step 5: Combined Correlation (gap_sec=120, max_hop=2)")
    print("=" * 60)
    clusters = correlate(alerts, graph, gap_sec=120, max_hop=2)
    for c in clusters:
        print(f"  {c['cluster_id']}: {c['alert_count']} alerts | "
              f"services={c['services']} | severity={c['max_severity']}")
        print(f"    time: {c['time_range'][0]} → {c['time_range'][1]}")
        print(f"    alert_ids: {c['alert_ids']}")

    # 6. Build summary and write JSON
    print("\n" + "=" * 60)
    print("Step 6: Writing results/cluster_summary.json")
    print("=" * 60)
    total_alerts = len(alerts)
    total_clusters = len(clusters)
    reduction = 1 - total_clusters / total_alerts

    summary = {
        "input_alerts": total_alerts,
        "output_clusters": total_clusters,
        "reduction_ratio": round(reduction, 4),
        "clusters": clusters,
    }

    results_dir = os.path.join(base, 'results')
    os.makedirs(results_dir, exist_ok=True)
    out_path = os.path.join(results_dir, 'cluster_summary.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"  input_alerts   = {total_alerts}")
    print(f"  output_clusters = {total_clusters}")
    print(f"  reduction_ratio = {reduction:.4f}")
    print(f"  Written to: {out_path}")
    print("\n✅ Done!")

    return summary


if __name__ == '__main__':
    main()
