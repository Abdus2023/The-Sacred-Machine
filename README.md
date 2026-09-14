# The-Sacred-Machine

Reclassifying Gods, Magic, and Memory from the Ancient Near East to the Abrahamic Religions (A Study of Cultural Transmission, Translation, Authority, and the Reproduction of Tradition)

## Provenance-preserving reconstruction pipeline

This repository contains the authoritative `Source.md`. The mechanical pipeline in `pipeline/` can decompose it into lossless, hashed source blocks and assemble a book only when an authoritative `BOOK_OUTLINE.md` and a reviewed mapping input are present.

The pipeline is deliberately not an author. It does not rewrite, summarize, translate, correct, merge, delete, or invent source content. Source text is carried in `original_text` fields and exact UTF-8 payload ranges in `BOOK_ASSEMBLY.jsonl`. The first assembled book artifact is `BOOK_FINAL_CANDIDATE.md`; `BOOK_FINAL.md` is impossible until certification succeeds.

Run from the repository root:

```sh
python3 -m pipeline.run outline-preflight
python3 -m pipeline.run review-verify
python3 -m pipeline.run mapping-validate
python3 -m pipeline.run run
python3 -m pipeline.run candidate-verify
python3 -m pipeline.run certify
python3 -m pipeline.run finalize
python3 -m pipeline.run verify-final
```

`outline-preflight` is structural validation only; it never creates or repairs an outline. `review-verify` replays only an externally supplied `artifacts/review/OUTLINE_REVIEW.json`; it never creates approval. `mapping-validate` is review-gated and stops at mapping authorization. The Revision 1.7 `run` command may continue to deterministic assembly only after `MAPPING_STATUS: AUTHORIZED` and ASM-000 pass; it terminates at a verified candidate, assembly manifest, dependency record, and assembly evidence. Revision 1.8 adds `candidate-verify`, `certify`, and `certificate-verify`: the verifier independently rechecks the complete dependency chain and certification gates, and certification may create only a hash-bound `artifacts/verification/RELEASE_CERTIFICATE.json`. Revision 1.9 adds `finalize` and `verify-final`; finalization revalidates that certificate, performs an atomic binary copy to the repository-root `BOOK_FINAL.md`, and records `pipeline/manifests/final.manifest.json`. It never regenerates or edits the candidate. No revision certifies by assembly success, and no finalization occurs without the current certificate.

`BOOK_OUTLINE.md` is now present as a source-grounded structural proposal. It is explicitly marked `outline_status: PROVISIONAL` and has not been human-reviewed. A run therefore passes structural outline preflight but stops with `BLOCKED` at outline review. This is intentional: the pipeline must not infer human approval from file existence, hashes, parsing, tests, CI, or fixture behavior. No mapping validation, candidate book, certificate, or `BOOK_FINAL.md` is released while outline review is incomplete.

A human-controlled review event must supply `artifacts/review/OUTLINE_REVIEW.json` with `evidence_class: "MANUAL_REVIEW"`, `decision: "ACCEPT"`, `review_status: "REVIEWED"`, non-empty `reviewer` and `reviewed_at_utc`, `outline_status_before: "PROVISIONAL"`, `outline_status_after: "REVIEWED"`, and exact `outline_raw_sha256` and `outline_normalized_sha256` values for the current `BOOK_OUTLINE.md`. `ACCEPT_WITH_CHANGES` requires revision and a new review; `REJECT` remains blocked. The authoritative outline metadata must explicitly become `outline_status: REVIEWED`; the pipeline never performs that transition. Any outline byte change revokes the replay and requires a new review.

After review admission, a separately supplied `artifacts/mapping/mapping.input.json` is required. It must independently bind `source_manifest_hash`, `outline_raw_sha256`, `outline_normalized_sha256`, `outline_review_hash`, and `review_status: "REVIEWED"`. Entries use `block_id`, `target_id`, `role`, and deterministic integer `placement`; every block must have exactly one `PRIMARY` or an explicit unmapped disposition. `MAP-000` precedes `MAP-001` through `MAP-015`. The verifier computes canonical mapping bytes and `mapping_sha256`, then stops at `MAPPING_STATUS: AUTHORIZED`; mapping validation is recorded only after review admission and mapping input existence. Assembly consumes that existing authorization only: it retrieves verified blocks by canonical `block_id`, excludes explicitly unmapped and non-payload dispositions, preserves normalized source hashes and provenance, and fails closed on any stale dependency or candidate mutation. A fixture mapping is never repository authorization.

Generated canonical manifests are under `pipeline/manifests/`; decomposition, duplicate-audit, and verification artifacts are under `artifacts/`. Missing or invalid authority also produces `RECOVERY_PLAN.md`. `Source.md` is never modified by the pipeline.
