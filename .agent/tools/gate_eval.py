#!/usr/bin/env python3
"""Evaluate current release gates from proposition JSON objects."""

from __future__ import annotations
import json
import sys
from protocol_check import expected_gate

def evaluate(propositions: list[dict]) -> dict:
    evaluated = []
    release = True
    for p in propositions:
        decision = expected_gate(
            p["result"],
            p["derivation"],
            bool(p.get("approved_derivation_rule")),
        )
        mandatory = bool(p.get("mandatory", True))
        evaluated.append({
            "proposition_id": p.get("proposition_id"),
            "result": p["result"],
            "derivation": p["derivation"],
            "gate": decision,
            "mandatory": mandatory,
        })
        if mandatory and decision != "AUTHORIZED":
            release = False
    return {"release_authorized": release, "gates": evaluated}

def main() -> int:
    if len(sys.argv) != 2:
        print("usage: gate_eval.py propositions.json", file=sys.stderr)
        return 2
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
    result = evaluate(data)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["release_authorized"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
