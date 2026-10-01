# Skill Creator

## Purpose

Create reusable agent skills from completed workflows while preserving explicit contracts, evidence boundaries, and failure modes.

## Creation procedure

1. Identify the reusable task.
2. Extract inputs and outputs.
3. Extract activation conditions.
4. Extract the canonical execution sequence.
5. Extract authority requirements.
6. Extract evidence requirements.
7. Extract proposition and gate semantics.
8. Extract failure modes.
9. Define forbidden shortcuts.
10. Write SKILL.md.
11. Add deterministic tools where behavior can be made explicit.
12. Add fixtures/tests for each tool.
13. Run validation.
14. Record artifact identity.

## Required skill structure

Purpose
Activation
Inputs
Outputs
Authority
Procedure
Evidence
Failure Modes
Forbidden Shortcuts
Completion Criteria

## Quality gate

A skill is not complete merely because its file exists.

Required:
- explicit activation conditions
- explicit inputs/outputs
- authority boundary
- deterministic or bounded procedure
- evidence model
- failure handling
- completion criterion
- machine-readable validation where practical

## Composition rule

Skills compose through artifacts and explicit contracts, not implicit shared state.

skill A output
  ↓
declared handoff artifact
  ↓
skill B input

## No silent promotion

INFERRED → TRUE is forbidden.
UNAVAILABLE → TRUE is forbidden.
REPRODUCTION → HISTORICAL FACT is forbidden.
