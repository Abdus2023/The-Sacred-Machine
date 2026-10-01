# Audit Phase Contract

## Seven evidence layers

1. EXISTENCE — what exists at the frozen source revision.
2. REQUIREMENT — what the declared contract requires.
3. EXECUTION — whether the declared execution occurred and its terminal state.
4. CAUSALITY — why that execution reached its terminal state.
5. REPRODUCIBILITY — whether the behavior recurs under a controlled profile.
6. ATTESTATION — what execution system produced as evidence.
7. RELEASE_INTEGRITY — whether the verified object is exactly the released object.

IDENTITY and PROVENANCE are explicit cross-cutting properties:
- IDENTITY binds the subject to an exact source/object.
- PROVENANCE binds tested source to built, packaged, and released objects.

## Non-implication matrix

EXISTENCE verified ↛ REQUIREMENT_MET
REQUIREMENT verified ↛ REQUIREMENT_MET
EXECUTION occurred ↛ execution success
EXECUTION failure ↛ historical causality
CAUSALITY known ↛ reproducibility
REPRODUCIBILITY ↛ historical causality
ATTESTATION ↛ release integrity without release-boundary identity
IDENTITY ↛ PROVENANCE
PROVENANCE ↛ RELEASE_INTEGRITY without a complete chain

## Controlled reproduction phases

P0 Freeze source
P1 Verify source identity
P2 Verify contract
P3 Declare hypotheses as non-authorizing
P4 Freeze execution profile
P5 Execute exact command sequence
P6 Capture complete output
P7 Hash evidence
P8 Create reproduction record
P9 Evaluate reproduction proposition
P10 Apply approved causal derivation rules
P11 Preserve historical causality independently
P12 Reassess current release gates

## Causality subpropositions

Where causal analysis matters, keep separate:
- mechanism: can X produce Y?
- sufficiency: does X produce Y under declared conditions?
- necessity: does removing X remove Y?
- exclusivity: is X the only relevant causal factor?

A reproduction normally provides evidence about the reproduction subject, not the historical subject.
