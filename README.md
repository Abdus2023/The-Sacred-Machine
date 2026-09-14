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
```

`outline-preflight` is structural validation only; it never creates or repairs an outline. `review-verify` replays only an externally supplied `artifacts/review/OUTLINE_REVIEW.json`; it never creates approval. `mapping-validate` is review-gated and stops before assembly.

`BOOK_OUTLINE.md` is now present as a source-grounded structural proposal. It is explicitly marked `outline_status: PROVISIONAL` and has not been human-reviewed. A run therefore passes structural outline preflight but stops with `BLOCKED` at outline review. This is intentional: the pipeline must not infer human approval from file existence, hashes, parsing, tests, CI, or fixture behavior. No mapping validation, candidate book, certificate, or `BOOK_FINAL.md` is released while outline review is incomplete.

A human-controlled review event must supply `artifacts/review/OUTLINE_REVIEW.json` with `evidence_class: "MANUAL_REVIEW"`, `decision: "ACCEPT"`, non-empty `reviewer` and `reviewed_at_utc`, `outline_status_before: "PROVISIONAL"`, `outline_status_after: "REVIEWED"`, and exact raw and normalized hashes for the current `BOOK_OUTLINE.md`. `ACCEPT_WITH_CHANGES` requires revision and a new review; `REJECT` remains blocked. The authoritative outline metadata must explicitly become `outline_status: REVIEWED`; the pipeline never performs that transition. Any outline byte change revokes the replay and requires a new review.

After review admission, a separately supplied `artifacts/mapping/mapping.input.json` is required. `MAP-000` replays the valid review before `MAP-001` through `MAP-015` validate mapping. The repository-defined mapping schema requires `mapping_version: "1.0"`, `source_manifest_hash`, `outline_hash`, `outline_status: "REVIEWED"`, `review_status: "REVIEWED"`, referential `entries` with deterministic `placement` values, one `PRIMARY` placement or explicit `EXPLICITLY_UNMAPPED` disposition for each block, and only valid outline IDs. Mapping remains metadata; it cannot change block payloads. Mapping validation is recorded in `artifacts/mapping/mapping.validation.json` only after review admission succeeds.

Generated canonical manifests are under `pipeline/manifests/`; decomposition, duplicate-audit, and verification artifacts are under `artifacts/`. Missing or invalid authority also produces `RECOVERY_PLAN.md`. `Source.md` is never modified by the pipeline.
