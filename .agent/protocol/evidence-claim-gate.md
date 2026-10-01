# Evidence → Proposition → Gate Contract

## Evidence object

An evidence object is an observation from an authorized source.

It supports propositions only through an explicit evidence reference and valid derivation.

## Proposition

A proposition has exactly one result:

TRUE
FALSE
UNDETERMINED

It also declares:
- subject
- evidence references
- derivation
- authority
- optional approved derivation rule
- required result
- mandatory status

## Gate

A gate evaluates whether the proposition may affect release authorization.

Evidence authorization:
DIRECT + TRUE → evidence may authorize a TRUE proposition.
DIRECT + FALSE → evidence may authorize a FALSE proposition.
DIRECT + UNDETERMINED → BLOCKED.
DERIVED requires an approved rule.
INFERRED → BLOCKED for every result.
UNAVAILABLE → BLOCKED for every result.

Requirement semantics are separate:
- proposition result says what is true;
- required_result says what the release gate requires.

Example:
result=FALSE and required_result=FALSE can produce AUTHORIZED.
result=FALSE and required_result=TRUE produces REJECTED.

## Evidence laundering barriers

Forbidden:
- inferred cause promoted to fact;
- unavailable log treated as success/failure detail;
- reproduction copied into historical execution;
- current implementation projected backward into an old commit;
- successful build treated as release provenance without identity linkage;
- artifact existence treated as artifact integrity without digest/identity evidence.

## Subject rule

Every evidence object and proposition must identify what event, source revision, reproduction, artifact, or release it describes.
