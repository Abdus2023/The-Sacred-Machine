# Evidence Audit

## Purpose

Audit a repository or artifact using the Evidence-First Release Protocol without converting inference into fact.

## Activation

Use for release-integrity audits, repository/workflow conformance, CI failure analysis, evidence-chain review, and release-readiness questions.

## Procedure

1. Freeze source identity.
2. Inspect the authorized source tree.
3. Inspect and parse the declared contract.
4. Separate requirements from requirement satisfaction.
5. Inspect execution metadata.
6. Inspect execution evidence.
7. Classify inaccessible evidence as UNAVAILABLE.
8. Construct evidence objects.
9. Evaluate propositions.
10. Evaluate mandatory release gates.
11. Preserve historical and reproduction subjects separately.
12. Emit a release decision.

## Required record

audit_id
source
contract
execution
evidence
propositions
gates
release_decision
unknowns

## Forbidden shortcuts

- Never infer execution from source inspection.
- Never infer historical causality from a reproduced failure.
- Never treat absence of evidence as FALSE unless the proposition is specifically about observable absence and the authorized source directly establishes it.
- Never use local execution as evidence that CI executed.
- Never use a successful build to prove release provenance unless the released object is linked to that exact build.

## Output status sets

Evidence: DIRECT | DERIVED | INFERRED | UNAVAILABLE
Result: TRUE | FALSE | UNDETERMINED
Gate: AUTHORIZED | REJECTED | BLOCKED

## Completion criterion

Every mandatory proposition has an explicit result, derivation, authority, evidence reference, and gate decision.
