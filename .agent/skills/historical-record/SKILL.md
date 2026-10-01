# Historical Record

## Purpose

Maintain immutable records of observed historical events while allowing incomplete propositions to be resolved by later evidence.

## Immutable observations

Preserve:
- event identifier
- source
- subject
- observed state
- source revision
- capture timestamp
- evidence digest

## Re-evaluable propositions

A proposition such as historical causality may remain:
UNDETERMINED / UNAVAILABLE / BLOCKED

New authorized evidence may change:
UNDETERMINED → TRUE
UNDETERMINED → FALSE

without changing the historical observation.

## Subject separation

Every proposition identifies its subject.

Examples:
historical_run:34872677449
reproduction:repr-b583a2af-001
current_release:<new-event>

Never copy a proposition from one subject to another without an explicit approved derivation rule.

## Anti-laundering invariant

AUTHORIZED(reproduction) ≠ AUTHORIZED(historical_causality)

## Correction policy

Do not delete an earlier authorized observation because later evidence provides a better explanation. Append the new evidence and re-evaluate the affected proposition.
