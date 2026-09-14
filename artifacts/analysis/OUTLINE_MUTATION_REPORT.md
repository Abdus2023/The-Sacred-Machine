# OUTLINE_MUTATION_REPORT

**Status:** VERIFIED
**Evidence class:** MUTATION_TEST

The outline mutation matrix is exercised by the 21-test suite. The tests verify that title, identifier, hierarchy, support, unknown-block, duplicate-ID, orphan-parent, reorder, and hash mutations change identity or fail structural/support validation. Fixture mutation evidence does not authorize the repository outline or release.

| Mutation | Expected result |
|---|---|
| OM-01 title mutation | identity changes |
| OM-02 node ID mutation | dependent identity changes |
| OM-03 hierarchy mutation | hierarchy gate fails |
| OM-04 support mutation | support validation changes |
| OM-05 support deletion | source support fails |
| OM-06 unknown block insertion | support validation fails |
| OM-07 duplicate ID insertion | identifier gate fails |
| OM-08 orphan node insertion | parent validation fails |
| OM-09 outline reorder | identity changes |
| OM-10 outline hash mutation | bound identity changes |
