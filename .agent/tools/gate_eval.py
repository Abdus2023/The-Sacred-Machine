"""Evaluate mandatory release gates from proposition JSON objects."""

from __future__ import annotations
import json
import sys
from protocol_check import expected_gate

def evaluate(propositions: list[dict]) -> dict:
    evaluated = []
    release = True

    for p in propositions:
        result = p["result"]
        derivation = p["derivation"]
        approved = bool(p.get("approved_derivation_rule"))
        evidence_gate = expected_gate(result, derivation, approved)
        required_result = p.get("required_result", "TRUE")
        required_result_ok = result == required_result

        if evidence_gate == "BLOCKED":
            decision = "BLOCKED"
        elif not required_result_ok:
            decision = "REJECTED"
        else:
            decision = "AUTHORIZED"

        mandatory = bool(p.get("mandatory", True))
        evaluated.append({
            "proposition_id": p.get("proposition_id"),
            "result": result,
            "required_result": required_result,
            "derivation": derivation,
            "evidence_gate": evidence_gate,
            "required_result_satisfied": required_result_ok,
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
