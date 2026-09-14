# Idempotence contract

Determinism and idempotence are separate gates. A repeat run must not create new block identities, duplicate payloads, change order, alter hashes, or rewrite source text. Runtime timestamps are not part of canonical manifests. The release path must execute and record a repeat-run comparison before certification.
