# RECOVERY_PLAN

**Status:** BLOCKED

- **Failed stage:** outline review
- **Missing or invalid prerequisite:** artifacts/review/OUTLINE_REVIEW.json
- **Last valid artifact:** artifacts/analysis/outline.manifest.json
- **Required human-provided input:** a human-supplied ACCEPT review record bound to the exact outline
- **Rerun command:** `python3 -m pipeline.run review-verify`

## Verification requirements
- review evidence_class must be MANUAL_REVIEW
- decision must be ACCEPT
- raw and normalized outline hashes must match
- BOOK_OUTLINE.md must explicitly be REVIEWED

No gate bypass, inferred authority, source modification, or manual downstream repair is permitted.
