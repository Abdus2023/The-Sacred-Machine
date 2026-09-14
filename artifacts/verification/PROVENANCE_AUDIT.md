# PROVENANCE_AUDIT

**Status:** VERIFIED
**Evidence class:** REPOSITORY

Scope: source-to-block provenance only; mapping and book placement are not yet authorized.

| Gate | Status | Expected | Observed |
|---|---|---|---|
| G-SRC-001 | PASS | current Source.md equals declared raw and normalized hashes | match |
| G-BLK-002 | PASS | each stored normalized hash equals its stored payload | all match |

## Affected artifacts

- /home/user/The-Sacred-Machine/Source.md
- artifacts/decomposition/CONTENT_BLOCKS.jsonl

## Affected blocks

- None
