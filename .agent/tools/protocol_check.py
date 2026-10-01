#!/usr/bin/env python3
"""Validate the Evidence-First proposition/derivation/gate algebra."""

from __future__ import annotations
import json
import sys

RESULTS = {"TRUE", "FALSE", "UNDETERMINED"}
DERIVATIONS = {"DIRECT", "DERIVED", "INFERRED", "UNAVAILABLE"}
GATES = {"AUTHORIZED", "REJECTED", "BLOCKED"}

def expected_gate(result: str, derivation: str, approved: bool = False) -> str:
    if derivation in {"INFERRED", "UNAVAILABLE"}:
        return "BLOCKED"
    if derivation == "DERIVED" and not approved:
        return "BLOCKED"
    if result == "TRUE":
        return "AUTHORIZED"
    if result == "FALSE":
        return "REJECTED"
    return "BLOCKED"

def validate(p: dict) -> list[str]:
    errors = []
    result = p.get("result")
    derivation = p.get("derivation")
    gate = p.get("gate")
    approved = bool(p.get("approved_derivation_rule"))
    if result not in RESULTS:
        errors.append(f"invalid result: {result!r}")
    if derivation not in DERIVATIONS:
        errors.append(f"invalid derivation: {derivation!r}")
    if gate not in GATES:
        errors.append(f"invalid gate: {gate!r}")
    if result in RESULTS and derivation in DERIVATIONS and gate in GATES:
        expected = expected_gate(result, derivation, approved)
        if gate != expected:
            errors.append(f"gate mismatch: expected {expected}, got {gate}")
    return errors

def main() -> int:
    if len(sys.argv) != 2:
        print("usage: protocol_check.py proposition.json", file=sys.stderr)
        return 2
    with open(sys.argv[1], encoding="utf-8") as f:
        proposition = json.load(f)
    errors = validate(proposition)
    if errors:
        print("INVALID")
        for e in errors:
            print(e)
        return 1
    print("VALID")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
