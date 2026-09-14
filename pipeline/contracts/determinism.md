# Determinism contract

Canonical JSON is UTF-8, no BOM, lexicographically sorted keys, fixed separators, standard escaping with non-ASCII characters unescaped, and LF terminated. Canonical CSV policy is recorded in the pipeline manifest.

The pipeline bundle hash includes executable pipeline code, contracts, and schemas. Semantic classification, duplicate detection, mapping, and gap analysis are not claimed deterministic unless their prompts, schemas, model identity/version, runtime, decoding parameters, tool versions, and input hashes are frozen. The current mechanical path invokes no semantic model, and its semantic determinism status remains `PROVISIONAL` rather than being upgraded by assertion.
