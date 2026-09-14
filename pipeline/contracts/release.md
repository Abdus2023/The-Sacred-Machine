# Release contract

Revision 1.6 ends at the mapping authorization boundary. Revision 1.7 may construct only a deterministic `BOOK_FINAL_CANDIDATE.md` after that authorization, with `pipeline/manifests/assembly.manifest.json`, dependency, roundtrip, provenance, and verification evidence. Candidate construction is not certification or release.

Certification, finalization, and release remain prohibited in Revision 1.7. The pipeline must not create `BOOK_FINAL.md`, a release certificate, or release artifacts. Repository evidence and fixture evidence remain distinct. A fixture mapping cannot certify or authorize the authoritative repository mapping. Any stale or revoked review invalidates mapping authorization and all downstream assembly authorization.

Any absent authority, missing evidence, unresolved gap, failed gate, or unfrozen manifest is `BLOCKED`. There is no manual override. A blocked run must stop and write a failure report with stage, gate, check, expected result, observed result, affected artifacts, affected blocks, last valid artifact, and recovery action.
