# Legacy Tests - Partial Implementation Warning

## Status: ⚠️ INCOMPLETE - Missing Jury Voting System

These tests validate **basic CRUD operations only** and do NOT cover the core jury voting system required by the architecture.

## Legacy Test Files

- `test_author_workflow.legacy.py` - 17 tests for basic author operations
  - ✅ Author model creation/update/delete
  - ✅ Version control and optimistic locking
  - ✅ Follower count increments
  - ✅ Permission checks (partial)
  - ❌ **Missing**: Jury voting system
  - ❌ **Missing**: Vote accumulation and auto-publish
  - ❌ **Missing**: Curator override vs democratic voting
  - ❌ **Missing**: Direct publish for trusted users
  - ❌ **Missing**: Owner-based permissions (update_own, delete_own)

## Current Test Count: 55 Tests Passing

- 12 tests: JWT auth validation
- 10 tests: Cursor pagination
- 16 tests: Edit history
- 17 tests: Author workflow (PARTIAL - no jury system)

## Required Additional Tests: 33+

See Phase 2.3 Step 9 in ROADMAP.md for complete testing requirements:
- 12 tests: Jury voting system
- 8 tests: Ownership permissions
- 4 tests: Direct publish logic
- 4 tests: Curator override
- 5 tests: Edge cases

## Do Not Use These Tests for Production Validation

These tests only validate the incomplete implementation. The jury voting system (the core governance mechanism) is completely missing and untested.

## New Test Files (To Be Created)

- `test_jury_voting.py` - Democratic voting system
- `test_author_ownership.py` - Scope-based ownership permissions
- `test_author_publish_paths.py` - Direct publish and jury approval paths
- `test_curator_override.py` - Curator instant approve/reject

## Migration Path

Once new tests are complete:
1. New tests should cover all 88 required scenarios
2. Legacy tests can be archived or deleted
3. New implementation will have proper jury system validation
