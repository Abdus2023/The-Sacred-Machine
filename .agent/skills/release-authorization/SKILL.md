# Release Authorization

## Purpose

Convert evaluated propositions into a release decision without changing their evidence status.

## Evaluation

For each mandatory gate:

TRUE + DIRECT/APPROVED_DERIVED + authorized authority + required result satisfied → AUTHORIZED

FALSE + DIRECT/APPROVED_DERIVED → REJECTED

UNDETERMINED → BLOCKED

INFERRED or UNAVAILABLE derivation → BLOCKED

A mandatory REJECTED or BLOCKED gate prevents release.

## Historical independence

Current release authorization must not modify:
- historical execution occurrence
- historical execution result
- historical timestamps
- historical source identity

Resolving historical causality is informative unless the current release policy explicitly makes it a current mandatory gate.

## Release integrity

Maintain a continuous identity/provenance chain:

source commit
  ↓
verified execution
  ↓
build
  ↓
artifact digest
  ↓
release object
  ↓
release attestation

A missing link is BLOCKED, not assumed.

## Output

RELEASE_AUTHORIZED = TRUE | FALSE
blocking_gates = [...]
evidence_summary = [...]
artifact_identity = ...
provenance = ...
