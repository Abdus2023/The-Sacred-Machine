# External outline review contract

`BOOK_OUTLINE.md` remains `PROVISIONAL` until an explicit human-controlled event. Structural preflight, hashes, tests, CI, coverage, confidence, and fixture behavior are not human approval.

The human supplies `artifacts/review/OUTLINE_REVIEW.json`; the pipeline never creates, edits, or promotes this record. It must carry `evidence_class: MANUAL_REVIEW`, `decision: ACCEPT`, `review_status: REVIEWED`, reviewer metadata, `outline_status_before: PROVISIONAL`, `outline_status_after: REVIEWED`, and the exact `outline_raw_sha256` and `outline_normalized_sha256` values of the current outline. The authoritative outline metadata must also already read `outline_status: REVIEWED` when replay is admitted.

`ACCEPT_WITH_CHANGES` is `REVISION_REQUIRED`, and `REJECT` is blocked. Any byte change to the outline invalidates both bindings and requires a new review record. Review governance metadata is evidence only and never becomes book content.

`MAP-000` is the mapping admission gate. Mapping validation cannot run until review replay is verified and a separately supplied `artifacts/mapping/mapping.input.json` exists. Review, repository, fixture, unit-test, and mutation-test evidence classes remain distinct.
