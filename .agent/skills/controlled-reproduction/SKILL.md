# Controlled Reproduction

## Purpose

Reproduce a reported behavior under a declared source and execution profile while preserving the distinction between reproduction and history.

## Procedure

P0 FREEZE SOURCE
P1 VERIFY SOURCE IDENTITY
P2 VERIFY CONTRACT
P3 DECLARE HYPOTHESES [NON-AUTHORIZING]
P4 FREEZE EXECUTION PROFILE
P5 EXECUTE
P6 CAPTURE COMPLETE
P7 HASH EVIDENCE
P8 CREATE REPRODUCTION RECORD
P9 EVALUATE REPRODUCTION
P10 DERIVE APPROVED MECHANISM
P11 PRESERVE HISTORICAL CAUSALITY
P12 REASSESS CURRENT GATES

## Execution profile

Record:
- commit SHA
- tree identity
- workflow path and definition hash
- OS
- architecture
- shell
- runtime versions
- dependency state
- PATH
- working directory
- runner identity
- timestamp

## Per-command evidence

Record:
- exact command
- exit code
- complete stdout
- complete stderr
- duration
- termination reason
- timestamp

## Causality discipline

A reproduction may authorize:
"Under declared conditions, condition X produces failure Y."

It may not authorize:
"Historical execution H failed because of X."

The second proposition requires historical evidence.

## Identity requirement

Same commit is necessary but not sufficient for same execution conditions. Environment and invocation must also be recorded.

## Failure modes

- environment drift
- dependency drift
- command mismatch
- workflow wrapper differences
- missing historical logs
- nondeterministic behavior
- symptom reproduction without causal exclusivity

Mechanism, sufficiency, necessity, and exclusivity are separate propositions where applicable.
