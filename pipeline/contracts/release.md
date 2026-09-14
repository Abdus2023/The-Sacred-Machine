# Release contract

The pipeline emits `BOOK_FINAL_CANDIDATE.md` first. The candidate is an object of verification and is never treated as released. Only after mandatory completeness, verbatim, provenance, order, roundtrip, determinism, idempotence, mutation, gap, duplicate, coverage, and release gates pass may it create `RELEASE_CERTIFICATE.json`, `RELEASE_CERTIFICATE.md`, and finally `BOOK_FINAL.md`.

Repository evidence and fixture evidence are distinct. A fixture result cannot certify the authoritative repository. Certificate bindings include source, outline, mapping, block, verification, pipeline, and candidate hashes. Finalization is a byte-for-byte copy followed by a final hash check.

Any absent authority, missing evidence, unresolved gap, failed gate, or unfrozen manifest is `BLOCKED`. There is no manual override. A blocked run must stop and write `FAILURE_REPORT.md` with stage, gate, check, expected result, observed result, affected artifacts, affected blocks, last valid artifact, and recovery action.
