# FAILURE_REPORT

**Status:** BLOCKED

## Failure
- **Stage:** outline review
- **Gate:** REVIEW-001..008
- **Check:** human review replay and exact outline binding
- **Expected:** MANUAL_REVIEW ACCEPT record bound to current reviewed outline hashes
- **Observed:** outline review is absent, invalid, or the outline remains PROVISIONAL

## Affected artifacts
- artifacts/analysis/outline.manifest.json
- artifacts/review/OUTLINE_REVIEW.json
- artifacts/review/OUTLINE_REVIEW_VALIDATION.json

## Affected blocks
- None identified

## Last valid artifact
artifacts/analysis/outline.manifest.json

## Recovery action
Obtain an explicit human review record. Do not create or alter OUTLINE_REVIEW.json automatically and do not infer human approval from structural preflight.

No automatic editorial repair was attempted. No release is authorized.
