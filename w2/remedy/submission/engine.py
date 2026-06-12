"""Evidence-Driven Remediation Engine — main entry point.

Usage:
    python engine.py decide --incident eval/E01.json \\
                            --history incidents_history.json \\
                            --actions actions.yaml

Output:
    - Prints JSON decision to stdout
    - Appends one JSON line to audit.jsonl
"""
from __future__ import annotations
import argparse
import json
import yaml
from pathlib import Path

from features import extract_features
from retrieval import retrieve_and_vote
from decision import select_action


def decide(incident_path: Path, history_path: Path, actions_path: Path) -> dict:
    """Full pipeline: extract → retrieve → select → return audit record."""
    incident = json.loads(incident_path.read_text(encoding="utf-8"))
    history = json.loads(history_path.read_text(encoding="utf-8"))
    actions_catalog = yaml.safe_load(actions_path.read_text(encoding="utf-8"))

    # Layer 1
    vec = extract_features(incident)

    # Layer 2
    candidates = retrieve_and_vote(vec, history)

    # Layer 3
    decision = select_action(candidates, actions_catalog, query_vec=vec)

    # Build audit record
    incident_id = Path(incident_path.stem).name  # e.g. "E01"

    audit_record = {
        "incident_id": incident_id,
        "selected_action": decision["selected_action"],
        "params": decision["params"],
        "confidence": decision["confidence"],
        "evidence": {
            "reasoning": decision["reasoning"],
            "is_ood": candidates["is_ood"],
            "best_similarity": candidates["best_similarity"],
            "top_neighbours": candidates["neighbours"][:3],
            "ev_table": decision["ev_table"][:4],
            "log_templates_matched": list(vec.get("log_template_set", [])),
            "anomalous_edges": vec.get("anomalous_edges", []),
            "dominant_log_service": vec.get("dominant_log_service"),
        },
    }
    return audit_record


def main() -> int:
    p = argparse.ArgumentParser(description="Evidence-Driven Remediation Engine")
    sub = p.add_subparsers(dest="cmd")

    d = sub.add_parser("decide", help="Decide remediation for one incident")
    d.add_argument("--incident", required=True, help="Path to incident JSON")
    d.add_argument("--history",  default="incidents_history.json",
                   help="Path to historical corpus JSON")
    d.add_argument("--actions",  default="actions.yaml",
                   help="Path to actions catalog YAML")
    d.add_argument("--audit",    default="audit.jsonl",
                   help="Path to audit log (appended)")

    args = p.parse_args()

    if args.cmd == "decide":
        out = decide(
            Path(args.incident),
            Path(args.history),
            Path(args.actions),
        )
        print(json.dumps(out, indent=2))
        audit_path = Path(args.audit)
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(out) + "\n")
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
