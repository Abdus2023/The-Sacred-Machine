# Outline construction contract

`BOOK_OUTLINE.md` is structural authority only. A generated outline is `PROVISIONAL` until human review; successful structural preflight does not imply review or scholarly validity. The pipeline does not change `Source.md`, strengthen source claims, resolve contradictions, or fill structural gaps with prose.

The generated proposal in `artifacts/analysis/OUTLINE_PROPOSAL.json` records exact source block support, support type, and structural confidence. Each referenced block must exist in the verified block manifest. A reviewer may accept or revise the proposal by supplying an authoritative outline revision; the pipeline never infers `REVIEWED` from parsing, confidence, or file existence.

Outline hashes use separate domains: `raw_sha256` is exact file bytes, while `normalized_sha256` is the declared canonical UTF-8 representation. Revision 1.6 mappings and Revision 1.7 assembly dependencies independently carry `outline_raw_sha256` and `outline_normalized_sha256`, plus the exact `outline_review_hash`; all must be regenerated or re-reviewed after any outline or review change.
