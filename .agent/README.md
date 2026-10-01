# Evidence-First Agent System

Reusable agent skills and deterministic tools extracted from the Evidence-First Release Protocol.

Core flow:

SOURCE → CONTRACT → OBSERVE → PROPOSITION → GATE → REPRODUCE → ATTEST → PROVENANCE → RELEASE

Core separations:
- Evidence object ≠ proposition ≠ gate decision.
- Proposition results are exactly TRUE, FALSE, UNDETERMINED.
- Evidence derivation is DIRECT, DERIVED, INFERRED, or UNAVAILABLE.
- Gate decisions are AUTHORIZED, REJECTED, or BLOCKED.
- INFERRED and UNAVAILABLE can never authorize.
- Reproduction is a new subject and never rewrites historical execution.
- Historical observations are immutable; incomplete propositions may be re-evaluated when new evidence arrives.
- The thing verified must be exactly the thing released.

Skills:
- evidence-audit
- controlled-reproduction
- release-authorization
- historical-record
- skill-creator

Tools:
- protocol_check.py
- gate_eval.py
- hash_evidence.py
- repro_capture.py

Operating rule: NO EVIDENCE → NO VERIFIED CLAIM.
