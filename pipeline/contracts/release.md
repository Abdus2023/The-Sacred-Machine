# Release contract

Revision 1.6 ends at the mapping authorization boundary. A successful mapping validation creates only deterministic mapping authorization evidence: `mapping.canonical.json`, `mapping.validation.json`, and `pipeline/manifests/mapping.manifest.json`. It does not create `BOOK_FINAL_CANDIDATE.md`, a certificate, or `BOOK_FINAL.md`.

Assembly, verification, certification, and finalization are later boundaries. Repository evidence and fixture evidence remain distinct. A fixture mapping cannot certify or authorize the authoritative repository mapping. Any stale or revoked review invalidates mapping authorization and all downstream authorization.

Any absent authority, missing evidence, unresolved gap, failed gate, or unfrozen manifest is `BLOCKED`. There is no manual override. A blocked run must stop and write a failure report with stage, gate, check, expected result, observed result, affected artifacts, affected blocks, last valid artifact, and recovery action.
