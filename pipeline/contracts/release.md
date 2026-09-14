# Release contract

Revision 1.6 ends at the mapping authorization boundary. Revision 1.7 may construct only a deterministic `BOOK_FINAL_CANDIDATE.md` after that authorization, with `pipeline/manifests/assembly.manifest.json`, dependency, roundtrip, provenance, and verification evidence. Candidate construction is not certification or release.

Revision 1.8 adds independent candidate verification and a certification boundary. Only after CERT-000 and CERT-001 through CERT-020 pass may `artifacts/verification/RELEASE_CERTIFICATE.json` be created. The certificate binds the exact candidate and dependency set; it is not finalization and does not create `BOOK_FINAL.md`.

Finalization remains prohibited in Revision 1.8. Repository evidence and fixture evidence remain distinct. A fixture certificate cannot authorize repository release. Any stale or revoked review invalidates mapping, assembly, verification, and certification authorization.

Any absent authority, missing evidence, unresolved gap, failed gate, or unfrozen manifest is `BLOCKED`. There is no manual override. A blocked run must stop and write a failure report with stage, gate, check, expected result, observed result, affected artifacts, affected blocks, last valid artifact, and recovery action.
