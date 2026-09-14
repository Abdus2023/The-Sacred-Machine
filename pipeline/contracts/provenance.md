# Provenance contract

The machine-verifiable chain is:

`Source.md -> source.manifest.json -> CONTENT_BLOCKS.jsonl -> mapping.manifest.json -> BOOK_ASSEMBLY.jsonl -> BOOK_FINAL_CANDIDATE.md`.

Each block has a stable source sequence, line span, byte span, original canonical text, raw hash, and normalized hash. Assembly sidecars identify exact UTF-8 byte ranges in the draft; the verifier reads those ranges rather than parsing Markdown markers.

A block may have at most one textual `PRIMARY` placement. `REFERENCE`, `SECONDARY`, and `DUPLICATE` rows are structural markers and do not silently copy source payload. Explicitly unmapped blocks are retained in an unmapped appendix when assembly is authorized.
