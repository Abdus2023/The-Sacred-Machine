# Certified Candidate Finalization Contract — Revision 1.9

Finalization is a controlled, byte-preserving materialization of an already-certified candidate. It consumes `artifacts/assembly/BOOK_FINAL_CANDIDATE.md` and the current hash-bound `artifacts/verification/RELEASE_CERTIFICATE.json`; it never invokes assembly, mapping, decomposition, normalization, analysis, rewriting, deduplication, or interpretation.

FINAL-000 independently revalidates the certificate and the complete source, block, outline, review, mapping, assembly, candidate, verification, and pipeline dependency chain. A missing, stale, mutated, provisional, fixture-inappropriate, or otherwise invalid dependency blocks finalization. A preexisting `BOOK_FINAL.md` is not overwritten by the finalization command.

The finalizer performs a binary-safe atomic copy. The only successful content operation is reading candidate bytes and writing identical candidate bytes. FINAL-001 compares exact final and candidate bytes and hashes. `pipeline/manifests/final.manifest.json` records the certificate, dependency hashes, candidate hash, final hash, finalization schema version, provenance, completeness, and roundtrip evidence.

Revision 1.9 ends at `FINALIZATION_STATUS: FINALIZED` and `RELEASE_STATUS: READY`. The final artifact is immutable after publication. No automatic recertification, candidate regeneration, or repair is permitted. Fixture finalization remains `FIXTURE` evidence and cannot authorize the repository release. CI evidence is never inferred from local execution. Later release tagging or publication is outside this boundary.
