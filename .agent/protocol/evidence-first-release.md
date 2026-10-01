# Evidence-First Release Protocol v1.0

## Evidence derivation

DIRECT = observed directly from an authorized source.
DERIVED = logically derived using an approved rule.
INFERRED = pattern or abductive reasoning; never authorizing.
UNAVAILABLE = sought but inaccessible; never authorizing.

## Proposition result

TRUE | FALSE | UNDETERMINED

No fourth truth value is permitted.

## Gate decision

TRUE + DIRECT/APPROVED_DERIVED → AUTHORIZED
FALSE + DIRECT/APPROVED_DERIVED → REJECTED
UNDETERMINED → BLOCKED
INFERRED → BLOCKED
UNAVAILABLE → BLOCKED

## Evidence objects

An evidence object records an observation. It does not automatically authorize every proposition derived from it.

Conceptual fields:
evidence_id
source
authority
observation
derivation
captured_at
content_hash

A proposition explicitly references its evidence and, when derived, its approved derivation rule.

## Historical/reproduction boundary

Historical execution and controlled reproduction are separate subjects.

historical_run:<id>
reproduction:<id>

A reproduction may establish a reproduced mechanism. It cannot promote historical causality merely because the failure signature matches.

## Historical immutability

Immutable:
- authorized historical observation
- recorded execution event
- recorded execution result

Re-evaluable:
- propositions whose evidence was incomplete
- historical causality
- interpretations of an event

New evidence creates or updates a proposition. It does not rewrite the event record.

## Release authorization

A release is authorized only when every mandatory current-release proposition has:
- result TRUE
- DIRECT or approved DERIVED derivation
- authorized authority
- required result satisfied

A failed mandatory proposition rejects release. An unresolved mandatory proposition blocks release.

## Non-implications

EXISTENCE ↛ REQUIREMENT_MET
EXECUTION ↛ EXECUTION_SUCCESS
FAILURE ↛ HISTORICAL_CAUSE
REPRODUCTION ↛ HISTORICAL_CAUSE
IDENTITY ↛ PROVENANCE
PROVENANCE ↛ RELEASE_INTEGRITY

## Release identity invariant

The thing that was verified must be exactly the thing that was released.
