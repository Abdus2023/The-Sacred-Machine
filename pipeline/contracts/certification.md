# Candidate Verification and Certification Contract — Revision 1.8

Revision 1.8 introduces an independent verification and certification boundary after deterministic assembly. Assembly success is not verification success, and verification success is not finalization.

The verifier recomputes the current source, block, outline, review, mapping, assembly, candidate, dependency, provenance, completeness, order, structural-envelope, roundtrip, determinism, idempotence, and mutation properties. It does not authorize an artifact solely because a producer status field says PASS. It never repairs a mutated artifact.

`CERT-000` requires the complete dependency chain and an existing candidate, assembly manifest, and dependency record. `CERT-001` through `CERT-020` are stable semantic gates. A candidate must use the explicitly contracted NORMALIZED source representation; verification reports must not call normalized equality raw-byte identity.

Only an exact candidate that passes every mandatory gate may produce `artifacts/verification/RELEASE_CERTIFICATE.json`. The certificate is bound to the exact candidate, dependency hashes, assembly manifest hash, and verification manifest hash. Evidence classes remain distinct; FIXTURE, UNIT_TEST, MUTATION_TEST, and local execution never become repository or CI authority.

Revision 1.8 stops at `CERTIFIED CANDIDATE` plus the release certificate. It must not create `BOOK_FINAL.md`, copy a candidate into a final release path, or invoke finalization. Finalization is reserved for Revision 1.9.
