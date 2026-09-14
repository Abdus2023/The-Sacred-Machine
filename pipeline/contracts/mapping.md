# Mapping input and admission contract — Revision 1.6

`artifacts/mapping/mapping.input.json` is external input. The pipeline validates it but never creates, repairs, reorders in place, or adopts a proposal as input.

The input binds `source_manifest_hash`, `outline_raw_sha256`, `outline_normalized_sha256`, `outline_review_hash`, and `review_status: REVIEWED`. It uses versioned entries with `block_id`, `target_id`, `role`, and explicit integer `placement`. Every source block is either mapped with exactly one `PRIMARY` disposition or explicitly unmapped; omissions, unknown references, duplicate PRIMARY entries, ambiguous placement, and conflicts fail.

`MAP-000` is the admission prerequisite. It requires current source and block manifests, current outline, an external accepted reviewed outline review, exact review bindings, mapping input presence, supported version, and valid foundational schema/bindings. Only after `MAP-000` and `MAP-001` through `MAP-015` pass does the state become `AUTHORIZED`.

Canonical mapping bytes use UTF-8 JSON with the repository canonical JSON policy, sorted object keys, semantic entry ordering by block, role, target, and placement, explicit null handling, and LF termination. The generated `mapping_sha256` is not included in its own preimage; it is calculated from those bytes and recorded in `mapping.canonical.json`, `mapping.validation.json`, and `pipeline/manifests/mapping.manifest.json`.

Revision 1.6 stops at mapping authorization. Revision 1.7 consumes this authorization in a separate ASM-000-gated deterministic assembly boundary; mapping admission itself never assembles, certifies, or creates `BOOK_FINAL.md`.
