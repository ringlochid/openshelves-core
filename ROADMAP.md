# Library Service Implementation Roadmap

## Overview
Transform prototype CRUD library service into production-grade wiki-style content platform with RBAC, trust scoring, and jury-based governance integrated with Auth Service.

**Architecture:** FastAPI + PostgreSQL + Redis + Celery + Auth Service Integration

**Timeline:** 6-8 weeks (1 developer full-time)

---

## Key Architecture Decisions

1. **Auth Architecture:** JWT validation only (no events, Auth Service is ground truth)
2. **Service Communication:** Shared secret (`SERVICE_API_KEY`) for trust score adjustments
3. **Edit History:** Linear with optimistic locking (Wikipedia-style, like Wikipedia)
4. **User IDs:** UUID (matching Auth Service)
5. **Migrations:** Nuclear approach (drop old, fresh start - no production data)
6. **ISBN:** Keep as optional field for external API integration

---

## Trust Score Rules (Applied by Auth Service)

**Content Submission:**
- Author/Collection approved: **+10** (instant contributor promotion)
- Author/Collection rejected: **-5**
- Book approved: **+20** (doubled reward)
- Book rejected: **-10** (doubled penalty)

**Review Helpfulness:**
- Marked helpful by trusted+ user: **+1** (max +5 per review)
- Marked unhelpful by trusted+ user: **-1** (max -5 per review)

**Social Engagement Bonus:**
- Author followed by another user: **+3** to submitter (max +6 per author)
- Book/Collection subscribed: **+3** to submitter (max +6 per item)

**Auto-Blacklist:**
- When `trust_score <= 0`: Set `is_blacklisted=True`, requires admin unlock

---

## Phase 0: Cleanup & Foundation (2-3 days) ✅ COMPLETED

### Cleanup Tasks
- [x] Delete old migrations: `rm -rf migrations/versions/*.py`
- [x] Delete old tests: `rm -rf tests/*`
- [x] Delete seed scripts: `rm scripts/seed*.py scripts/generate_big_data.py`
- [x] Delete prototype data: `rm data_feeding.txt devcontainer_test.txt`

### Configuration Setup
- [x] Create `settings.py` with Pydantic BaseSettings
  - Database configuration (async only)
  - Redis configuration
  - Auth Service integration (URL, SERVICE_API_KEY, JWT public key path)
  - S3 media configuration
  - Celery configuration
  - Caching settings
  - ClamAV settings
  - Soft delete window (24h default)
- [x] Generate shared secret: `python -c "import secrets; print(secrets.token_urlsafe(32))"` and put in env.
- [x] Copy JWT public key from Auth Service: `curl http://auth-service:8000/keys/public.pem > keys/public_key.pem`(or simply copy from the DevNotes/AuthUserServiceCopyForReference_deleteoncewired/keys/public_key.pem)
- [x] use postgresql+psycopg://postgres:123456@localhost:5432/library_app in `.env` for alembic for run alembic using the exposed port
- [x] Create `.env.example`,`.env.bak` with all required variables
- [x] Move all hardcoded variables in code to `.env.example`,`.env.bak`
- [x] Copy from `.env.example` to `.env`
- [x] Update `.gitignore` to exclude `.env`,`.env.example`,`keys/private*`,`.env.bak`

### Database Simplification
- [x] Update `database.py` to remove sync engine for app (keep for Alembic)
  - Remove `SessionLocal`, `get_db()` (no sync sessions for app)
  - Keep `async_engine`, `AsyncSessionLocal`, `get_async_db()`, `Base`
  - Add `sync_engine` for Alembic migrations only
  - Remove `load_dotenv()` (handled by Pydantic settings)
  - Import from `settings` instead of `os.getenv()`

### Update Existing Files
- [x] Update `cache.py` to use `settings` instead of `os.getenv()`
  - Remove `load_dotenv()`
  - Import `settings` for Redis config
- [x] Update `celery_app.py` to use `settings`
  - Remove `load_dotenv()` and `os.getenv()`
  - Add Beat schedule for cleanup tasks (placeholder)
- [x] Update `main.py` to import from `settings`
  - Remove scattered config imports

### Testing
- [x] Test settings load from `.env`
- [x] Test database connection with async engine only (and sync for Alembic)
- [x] Test Redis connection
- [x] Test Celery app initializes correctly
- [x] Test FastAPI app starts successfully

---

## Phase 1: Auth Integration & Core Models (4-5 days) 🔄 IN PROGRESS

### Auth Dependencies
- [x] Create `dependencies/auth.py`
  - `get_current_user()` - Decode JWT, validate, check blacklist
  - `require_scope(*scopes)` - Dependency factory for scope checks
  - `require_role(*roles)` - Dependency factory for role checks
  - `verify_service_token()` - Validate X-Service-Token header
- [x] Load JWT public key at startup in `main.py`

### Auth Service Client
- [x] Create `services/auth_client.py`
  - `adjust_user_trust()` - Call Auth Service POST /admin/users/{user_id}/trust/adjust
  - `record_submission_outcome()` - Placeholder (handled by trust adjustment)
  - Implement retry logic and error handling
  - Add timeout configuration (10s default)

### Database Schema Redesign
- [x] **Completely rewrite `models.py`** with UUID user IDs and workflow fields:

**Core Content Tables:**
- [x] Update `Author` model
  - Change to UUID foreign keys: `created_by_user_id`, `linked_user_id`
  - Add workflow fields: `status` (pending/approved/rejected), `is_public`, `is_deleted`, `deleted_at`
  - Add versioning: `version`, `last_edited_by`, `last_edited_at`
  - Add social: `follower_count`
  - Add `bio` field (Text)
  - Keep `name`, `avatar_key`

- [x] Update `Book` model
  - Change to UUID: `created_by_user_id`
  - Add workflow: `status`, `is_public`, `is_deleted`, `deleted_at`
  - Add versioning: `version`, `last_edited_by`, `last_edited_at`
  - Add social: `subscriber_count`
  - Add media: `file_key`, `file_format` (pdf/epub/mobi)
  - Remove `isbn`
  - Remove `genre_name` add `tags`(JSONB + GIN index)
  - Keep existing: `title`, `year`, `description`, `cover_key`, `search_tsv`

- [x] Update `Review` model
  - Change `reviewer_name` → `user_id` (UUID)
  - Add helpfulness: `helpful_count`, `unhelpful_count`, `trust_awarded`(-5 ~ +5, negative for penalty)
  - Add soft delete: `is_deleted`, `deleted_at`
  - Keep: `book_id`, `rating`, `comment`

- [x] Update `PendingUpload` model
  - Change `user_id` from int → UUID
  - Add: `upload_type`, `entity_type`, `entity_id`
  - Update: `expires_at` default to 10 minutes

**New Tables:**
- [x] Create `Collection` model
  - Fields: `id`, `name`, `description`, `cover_key`
  - Ownership: `created_by_user_id` (UUID)
  - Workflow: `status`, `is_public`, `is_deleted`, `deleted_at`
  - Versioning: `version`, `last_edited_by`, `last_edited_at`
  - Social: `subscriber_count`
  - Timestamps: `created_at`, `updated_at`

- [x] Create `CollectionBook` model (association table)
  - `collection_id`, `book_id`, `position`

- [x] Create `EditHistory` model
  - Fields: `id`, `entity_type`, `entity_id`, `action`, `user_id`
  - History: `version`, `parent_version`, `old_data`, `new_data`, `changes` (all JSONB)
  - Timestamp: `created_at`
  - Index: `(entity_type, entity_id, created_at DESC)`

- [x] Create `AuthorFollow` model
  - `user_id` (UUID), `author_id`, `created_at`
  - Primary key: `(user_id, author_id)`

- [x] Create `BookSubscription` model
  - `user_id` (UUID), `book_id`, `created_at`
  - Primary key: `(user_id, book_id)`

- [x] Create `CollectionSubscription` model
  - `user_id` (UUID), `collection_id`, `created_at`
  - Primary key: `(user_id, collection_id)`

- [x] Create `ReviewVote` model
  - `user_id` (UUID), `review_id`, `vote` (helpful/unhelpful), `created_at`
  - Primary key: `(user_id, review_id)`
  - Check constraint: `vote IN ('helpful', 'unhelpful')`

- [x] Add all necessary indexes for performance
- [x] Add check constraints (rating 1-5, year > 0, etc.)

### PostgreSQL Extensions & Search Features
**IMPORTANT: These features exist in old migrations but are NOT in models.py - must be preserved!**

**Note: The genre name has changed to tags so don't use genre but make tags in the tsv("setweight(to_tsvector('english', coalesce(genre_name, '')), 'B') no longer valid)**

- [x] **Enable PostgreSQL extensions** (in migration `upgrade()`):
  ```python
  op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
  op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
  ```

- [x] **Create immutable_unaccent function** (for functional indexes):
  ```python
  op.execute("""
      CREATE OR REPLACE FUNCTION immutable_unaccent(text)
      RETURNS text
      LANGUAGE sql
      IMMUTABLE
      AS $$
          SELECT public.unaccent($1);
      $$;
  """)
  ```

- [x] **Add GIN indexes for full-text search on Book**:
  - `search_tsv` column (already in models.py as Computed column)
  - GIN index: `ix_books_search_tsv` using gin on `search_tsv`
  - GIN trigram: `idx_books_title_trgm` using gin on `title gin_trgm_ops`
  ```python
  # After Book table creation
  op.create_index(
      "idx_books_title_trgm",
      "books",
      [sa.text("title gin_trgm_ops")],
      postgresql_using="gin",
  )
  op.create_index(
      "ix_books_search_tsv",
      "books",
      ["search_tsv"],
      postgresql_using="gin",
  )
  ```

- [x] **Add GIN trigram indexes for Author similarity search**:
  - On `name`: `idx_authors_name_trgm` using `immutable_unaccent(name) gin_trgm_ops`
  - On `email`: `idx_authors_email_trgm` using `immutable_unaccent(email) gin_trgm_ops`
  ```python
  op.create_index(
      "idx_authors_name_trgm",
      "authors",
      [sa.text("immutable_unaccent(name) gin_trgm_ops")],
      postgresql_using="gin",
  )
  op.create_index(
      "idx_authors_email_trgm",
      "authors",
      [sa.text("immutable_unaccent(email) gin_trgm_ops")],
      postgresql_using="gin",
  )
  ```

### Migrations
- [x] Generate fresh migration: `alembic revision --autogenerate -m "complete_schema_redesign"`
- [x] **Manually add** PostgreSQL extensions, immutable_unaccent function, and GIN indexes to migration
- [x] Review migration file for correctness (ensure all search features included)
- [x] Apply migration: `alembic upgrade head`
- [x] Verify all tables created correctly
- [x] Test full-text search: `SELECT * FROM books WHERE search_tsv @@ to_tsquery('english', 'python')`
- [x] Test trigram search: `SELECT * FROM authors WHERE similarity(name, 'tolkien') > 0.3`

### Schema Updates
- [ ] Update `schemas/author.py`
  - Add: `status`, `version`, `bio`, `linked_user_id`, `follower_count`
  - Create `AuthorCreate`, `AuthorUpdate`, `AuthorRead`, `AuthorDetail`

- [ ] Update `schemas/book.py`
  - Add: `status`, `version`, `file_key`, `file_format`, `created_by_user_id`, `subscriber_count`
  - Update pagination response
  - Keep cursor pagination logic

- [ ] Update `schemas/review.py`
  - Change `reviewer_name` → `user_id`
  - Add: `helpful_count`, `unhelpful_count`

- [ ] Update `schemas/shared.py`
  - Update base schemas to match new fields

- [ ] Create `schemas/collection.py`
  - `CollectionCreate`, `CollectionUpdate`, `CollectionRead`, `CollectionDetail`

- [ ] Create `schemas/history.py`
  - `EditHistoryRead`, `EditHistoryResponse`

### Helper Functions
- [ ] Create `helpers/edit_history.py`
  - `record_edit()` - Record edit in history table with version tracking
  - `calculate_changes()` - Diff old_data and new_data
  - Helper for version conflict error messages

### Testing
- [ ] Test JWT validation (valid token, expired token, invalid signature)
- [ ] Test scope checks (missing scope returns 403)
- [ ] Test role checks (missing role returns 403)
- [ ] Test service token validation
- [ ] Test all models create correctly
- [ ] Test optimistic locking (version conflicts)
- [ ] Test edit history recording
- [ ] Test Auth Service client calls (mock responses)

---

## Phase 2: Author Workflow (4-5 days) ⏳ NOT STARTED

### Author Router Rewrite
- [ ] Backup old `routers/author.py` as `author.py.old`
- [ ] Create new `routers/author.py` with complete workflow

**Public Endpoints (No Auth):**
- [ ] `GET /authors` - List public approved authors
  - Filter: `is_public=True`, `is_deleted=False`, `status=approved`
  - Add similarity search on name (keep existing logic)
  - Return: `List[AuthorRead]`

- [ ] `GET /authors/{id}` - Get author detail
  - Check: `is_deleted=False`
  - If not approved/public: return 403 (unless authenticated owner/curator)
  - Return: `AuthorRead`

- [ ] `GET /authors/{id}/books` - Books by author
  - Return only public books
  - Return: `List[BookBase]`

**Authenticated Endpoints:**
- [ ] `POST /authors` - Create author (pending approval)
  - Requires: `content:submit` scope
  - Set: `status=pending`, `is_public=False`, `created_by_user_id=current_user`
  - Optional: `link_to_self` flag to set `linked_user_id`
  - Record edit history: action=create
  - Invalidate cache
  - Return: `AuthorRead`

- [ ] `PATCH /authors/{id}` - Update author (with version check)
  - Requires: owner OR `content:edit_any` scope
  - Query param: `expected_version` (required)
  - Check version match (409 if mismatch)
  - Snapshot old_data before changes
  - Apply partial updates from `AuthorUpdate` schema
  - Increment version, update `last_edited_by`, `last_edited_at`
  - Record edit history: action=update with old/new data
  - Invalidate cache
  - Return: `AuthorRead`

- [ ] `DELETE /authors/{id}` - Soft delete
  - Requires: owner OR `content:delete_any` scope
  - Set: `is_deleted=True`, `deleted_at=now()`, `is_public=False`
  - Record edit history: action=delete
  - Invalidate cache
  - Return: 204 No Content

**Curator Endpoints (Admin Prefix):**
- [ ] `GET /admin/authors/pending` - List pending submissions
  - Requires: `content:review` scope
  - Filter: `status=pending`, `is_deleted=False`
  - Order by: `created_at ASC`
  - Pagination: limit/offset
  - Return: `List[AuthorRead]`

- [ ] `POST /admin/authors/{id}/approve` - Approve author
  - Requires: `content:approve` scope
  - Set: `status=approved`, `is_public=True`
  - Call Auth Service: `adjust_trust(+10, source="upload")`
  - Invalidate cache
  - Return: `{"message": "...", "trust_delta": 10}`

- [ ] `POST /admin/authors/{id}/reject` - Reject author
  - Requires: `content:approve` scope
  - Query param: `reason` (required, min 10 chars)
  - Set: `status=rejected`, `is_public=False`
  - Call Auth Service: `adjust_trust(-5, source="upload")`
  - Invalidate cache
  - Return: `{"message": "...", "trust_delta": -5, "reason": "..."}`

- [ ] `POST /admin/authors/{id}/recover` - Recover soft-deleted
  - Requires: `content:recover` scope
  - Check: `is_deleted=True` and `deleted_at` within 24h window
  - Set: `is_deleted=False`, `deleted_at=None`, `status=pending`, `is_public=False`
  - Record edit history: action=recover
  - Invalidate cache
  - Return: `AuthorRead`

**Social Endpoints:**
- [ ] `POST /authors/{id}/follow` - Follow author
  - Requires: `social:follow` scope
  - Check: author is public and not deleted
  - Check: not already following (prevent duplicate)
  - Create `AuthorFollow` record
  - Increment `author.follower_count`
  - Call Auth Service: `adjust_trust(submitter, +3, source="social")`
    - Note: Max +6 cap needs external tracking (future enhancement)
  - Return: `{"message": "...", "follower_count": N}`

- [ ] `DELETE /authors/{id}/follow` - Unfollow author
  - Requires: authenticated user
  - Delete `AuthorFollow` record
  - Decrement `author.follower_count`
  - Return: 204 No Content

- [ ] `GET /authors/{id}/followers` - List followers
  - Return: `List[UUID]` of follower user IDs
  - Pagination: limit/offset

### Cache Helpers
- [ ] Update `cache.py` with new functions:
  - `invalidate_author_follows(author_id)` - Clear follow cache
  - Update `invalidate_author()` to handle new relationships
  - Add versioned cache keys for author lists by status

### Testing
- [ ] Test create author (pending status, owner tracking)
- [ ] Test approve author (status change, trust +10)
- [ ] Test reject author (status change, trust -5)
- [ ] Test update with correct version (succeeds)
- [ ] Test update with wrong version (409 conflict with error details)
- [ ] Test soft delete and recover within 24h (succeeds)
- [ ] Test recover after 24h (fails with 400)
- [ ] Test follow author (increment count, trust +3)
- [ ] Test unfollow author (decrement count)
- [ ] Test duplicate follow (fails with 400)
- [ ] Test permission checks (owner vs curator vs public)
- [ ] Test cache invalidation on mutations
- [ ] Test edit history records all changes

---

## Phase 3: Books & Reviews Workflow (5-6 days) ⏳ NOT STARTED

### Book Router Refactor
- [ ] Backup old `routers/book.py` as `book.py.old`
- [ ] Refactor `routers/book.py` with workflow

**Public Endpoints(rate limit with ip):**
- [ ] `GET /books` - List public books
  - Keep existing cursor pagination and similarity search
  - Chang add a tags parameter to filtering using tags, remove any genre related logic.
  - Filter: `is_public=True`, `is_deleted=False`, `status=approved`
  - Return: `PaginatedBooks`

- [ ] `GET /books/{id}` - Book detail
  - Check: `is_deleted=False`
  - If not public: check ownership or curator scope
  - Eager load: authors, reviews
  - Return: `BookDetailRead`

- [ ] `GET /books/{id}/reviews` - Reviews for book
  - Filter: `is_deleted=False`
  - Include helpfulness counts
  - Return: `List[ReviewRead]`

**Authenticated Endpoints:**
- [ ] `POST /books` - Create book (pending)
  - Requires: `content:submit` scope
  - Set: `status=pending`, `is_public=False`, `created_by_user_id`
  - Associate with authors (validate author IDs exist)
  - Record edit history: action=create
  - Return: `BookDetailRead`

- [ ] `PATCH /books/{id}` - Update book (with version check)
  - Requires: owner OR `content:edit_any`
  - Query param: `expected_version`
  - Check version (409 if mismatch)
  - Apply partial updates
  - Increment version
  - Record edit history: action=update
  - Return: `BookDetailRead`

- [ ] `DELETE /books/{id}` - Soft delete
  - Requires: owner OR `content:delete_any`
  - Soft delete with timestamp
  - Record edit history: action=delete
  - Return: 204

- [ ] Remove `PUT /books/{id}` - Use PATCH only
- [ ] Remove `PUT /books/{id}/authors` - Use PATCH with author_ids instead

**Review Endpoints:**
- [ ] `POST /books/{id}/reviews` - Add review
  - Extract `user_id` from JWT (no more reviewer_name)
  - Check: user hasn't reviewed this book yet (unique constraint)
  - Create review with `user_id`
  - Invalidate book cache
  - Return: `ReviewRead`

- [ ] `PATCH /reviews/{id}` - Update own review
  - Requires: review owner
  - Allow update: `rating`, `comment`
  - Return: `ReviewRead`

- [ ] `DELETE /reviews/{id}` - Soft delete own review
  - Requires: review owner OR `content:delete_any`
  - Soft delete with timestamp
  - Return: 204

**Curator Endpoints:**
- [ ] `GET /admin/books/pending` - List pending books
  - Requires: `content:review`
  - Same pattern as authors
  - Return: `List[BookListRead]`

- [ ] `POST /admin/books/{id}/approve` - Approve book
  - Requires: `content:approve`
  - Set: `status=approved`, `is_public=True`
  - Call Auth Service: `adjust_trust(+20, source="upload")`
  - Return: `{"message": "...", "trust_delta": 20}`

- [ ] `POST /admin/books/{id}/reject` - Reject book
  - Requires: `content:approve`
  - Query param: `reason`
  - Set: `status=rejected`
  - Call Auth Service: `adjust_trust(-10, source="upload")`
  - Return: `{"message": "...", "trust_delta": -10}`

- [ ] `POST /admin/books/{id}/recover` - Recover soft-deleted
  - Same pattern as authors
  - Return: `BookDetailRead`

**Social Endpoints:**
- [ ] `POST /books/{id}/subscribe` - Subscribe to book
  - Requires: `social:follow` scope
  - Create `BookSubscription` record
  - Increment `book.subscriber_count`
  - Call Auth Service: `adjust_trust(submitter, +3, source="social")`
  - Return: `{"message": "...", "subscriber_count": N}`

- [ ] `DELETE /books/{id}/subscribe` - Unsubscribe
  - Delete subscription
  - Decrement count
  - Return: 204

**Review Voting Endpoints:**
- [ ] `POST /reviews/{id}/vote` - Vote helpful/unhelpful
  - Requires: `social:vote` scope AND `trust_score >= 50` (trusted+)
  - Body: `{"vote": "helpful"|"unhelpful"}`
  - Check: voter hasn't voted on this review yet
  - Check: review hasn't reached max trust (±5)
  - Create `ReviewVote` record
  - Update review counts: `helpful_count` or `unhelpful_count`
  - Update review `trust_awarded` (±1)
  - Call Auth Service: `adjust_trust(reviewer, ±1, source="review")`
  - Return: `{"message": "...", "trust_delta": ±1}`

### Review Voting Service
- [ ] Create `services/review_voting.py`
  - `vote_on_review()` function with business logic:
    - Eligibility check (trust >= 50)
    - Duplicate vote handling (allow vote change)
    - Trust cap enforcement (max ±5 per review)
    - Counter updates (helpful_count, unhelpful_count)
    - Trust adjustment call to Auth Service

### Schema Updates
- [ ] Update `schemas/book.py`
  - Add: `status`, `version`, `file_key`, `file_format`, `created_by_user_id`, `subscriber_count`
  - Update `BookDetailRead` to include review helpfulness

- [ ] Update `schemas/review.py`
  - Change `reviewer_name` → `user_id`
  - Add: `helpful_count`, `unhelpful_count`
  - Remove `ReviewUpdate` with reviewer_name

### Testing
- [ ] Test book creation (pending, with authors)
- [ ] Test book approval (trust +20)
- [ ] Test book rejection (trust -10)
- [ ] Test book update with version check
- [ ] Test book soft delete and recover
- [ ] Test review creation with user_id (not reviewer_name)
- [ ] Test review unique constraint (one per user per book)
- [ ] Test review voting (helpful/unhelpful)
- [ ] Test review voting requires trust >= 50
- [ ] Test review voting duplicate prevention
- [ ] Test review voting trust cap (max ±5)
- [ ] Test review trust adjustment calls Auth Service
- [ ] Test subscribe/unsubscribe to book

---

## Phase 4: Collections (3-4 days) ⏳ NOT STARTED

### Collection Router
- [ ] Create `routers/collection.py`

**Public Endpoints:**
- [ ] `GET /collections` - List public collections
  - Filter: `is_public=True`, `is_deleted=False`, `status=approved`
  - Pagination: limit/offset
  - Return: `List[CollectionRead]`

- [ ] `GET /collections/{id}` - Collection detail with books
  - Eager load books in collection
  - Return: `CollectionDetail`

**Authenticated Endpoints:**
- [ ] `POST /collections` - Create collection (pending)
  - Requires: `content:submit`
  - Set: `status=pending`, `is_public=False`
  - Return: `CollectionRead`

- [ ] `PATCH /collections/{id}` - Update collection (with version check)
  - Requires: owner OR `content:edit_any`
  - Query param: `expected_version`
  - Apply updates
  - Record edit history
  - Return: `CollectionRead`

- [ ] `DELETE /collections/{id}` - Soft delete
  - Requires: owner OR `content:delete_any`
  - Return: 204

- [ ] `POST /collections/{id}/books` - Add book to collection
  - Requires: owner OR `content:edit_any`
  - Body: `{"book_id": int, "position": int}`
  - Validate book exists and is public
  - Create `CollectionBook` record
  - Bump collection version
  - Record edit history
  - Return: `{"message": "...", "book_count": N}`

- [ ] `DELETE /collections/{id}/books/{book_id}` - Remove book
  - Requires: owner OR `content:edit_any`
  - Delete `CollectionBook` record
  - Bump collection version
  - Record edit history
  - Return: 204

**Curator Endpoints:**
- [ ] `GET /admin/collections/pending` - List pending
  - Requires: `content:review`
  - Return: `List[CollectionRead]`

- [ ] `POST /admin/collections/{id}/approve` - Approve
  - Requires: `content:approve`
  - Set: `status=approved`, `is_public=True`
  - Call Auth Service: `adjust_trust(+10, source="upload")`
  - Return: `{"message": "...", "trust_delta": 10}`

- [ ] `POST /admin/collections/{id}/reject` - Reject
  - Requires: `content:approve`
  - Query param: `reason`
  - Call Auth Service: `adjust_trust(-5, source="upload")`
  - Return: `{"message": "...", "trust_delta": -5}`

- [ ] `POST /admin/collections/{id}/recover` - Recover
  - Requires: `content:recover`
  - Return: `CollectionRead`

**Social Endpoints:**
- [ ] `POST /collections/{id}/subscribe` - Subscribe
  - Requires: `social:follow`
  - Create `CollectionSubscription`
  - Increment `subscriber_count`
  - Call Auth Service: `adjust_trust(+3, source="social")`
  - Return: `{"message": "...", "subscriber_count": N}`

- [ ] `DELETE /collections/{id}/subscribe` - Unsubscribe
  - Delete subscription
  - Decrement count
  - Return: 204

### Collection Schemas
- [ ] Create `schemas/collection.py`
  - `CollectionCreate` - name, description
  - `CollectionUpdate` - partial updates
  - `CollectionRead` - basic info with subscriber_count
  - `CollectionDetail` - includes books list

### Testing
- [ ] Test create collection (pending)
- [ ] Test approve collection (trust +10)
- [ ] Test reject collection (trust -5)
- [ ] Test update with version check
- [ ] Test soft delete and recover
- [ ] Test add/remove books from collection
- [ ] Test subscribe/unsubscribe
- [ ] Test permission checks

---

## Phase 5: Media Management (5-6 days) ⏳ NOT STARTED

### Upload Router
- [ ] Create `routers/upload.py`

**Presigned Upload:**
- [ ] `POST /uploads/presign` - Get presigned URL
  - Requires: authenticated user
  - Body: `{"upload_type": "book_cover"|"book_file"|"author_avatar", "entity_type": "book"|"author"|"collection", "entity_id": int, "file_name": str, "content_type": str}`
  - Validate: user owns entity OR has `content:edit_any`
  - Generate S3 key: `tmp/{upload_type}/{user_id}/{uuid}.{ext}`
  - Create `PendingUpload` record (expires in 10 min)
  - Generate presigned PUT URL (10 min expiry)
  - Return: `{"upload_url": str, "s3_key": str, "expires_at": datetime}`

**Commit Upload:**
- [ ] `POST /uploads/commit` - Confirm upload and trigger processing
  - Requires: authenticated user
  - Body: `{"s3_key": str, "entity_type": str, "entity_id": int}`
  - Validate: `PendingUpload` exists and not expired
  - Validate: user owns entity
  - Update status to `completed`
  - Trigger Celery task: `process_media_upload.delay(s3_key, entity_type, entity_id)`
  - Return: `{"message": "Processing started", "task_id": str}`

### Storage Service
- [ ] Create `services/storage.py`
  - `get_s3_client()` - Boto3 client factory
  - `generate_presigned_upload_url()` - Presigned PUT URL
  - `generate_presigned_download_url()` - Presigned GET URL (for serving)
  - `move_s3_object()` - Move from tmp to final location
  - `delete_s3_object()` - Cleanup

### Celery Media Tasks
- [ ] Update `tasks/media.py` with real implementations

**Cover Processing:**
- [ ] `process_book_cover(s3_key, entity_type, entity_id)` task
  - Download from S3 tmp location to local tempfile
  - Validate image (PIL, check format, dimensions, file size)
  - Resize to multiple sizes: 512px, 256px, 128px (max side)
  - Convert to WebP format
  - Upload to final location: `{entity_type}s/{entity_id}/cover_{size}.webp`
  - Update entity record with `cover_key` (largest size)
  - Delete tmp object
  - Return: final_key

**Book File Processing:**
- [ ] `process_book_file(s3_key, book_id)` task
  - Download from S3 to tempfile
  - Validate file format (PDF/EPUB/MOBI magic bytes)
  - Scan with ClamAV (if configured)
  - Extract metadata (title, author, page count if possible)
  - Move to final location: `books/{book_id}/file.{ext}`
  - Update book record: `file_key`, `file_format`, metadata
  - Delete tmp object
  - Return: final_key

**Virus Scanning:**
- [ ] `scan_with_clamav(file_path)` helper
  - Check if ClamAV configured
  - Use `clamd` library to scan file
  - Raise exception if malware detected
  - Return: scan result

### Docker Updates
- [ ] Update `docker-compose.yml`
  - Add `worker` service (Celery worker)
    - Command: `celery -A celery_app worker -Q media,email,default -l info`
    - Depends on: redis, db
  - Add `beat` service (Celery beat)
    - Command: `celery -A celery_app beat -l info`
    - Depends on: redis, db
  - Add `clamav` service (optional)
    - Image: `clamav/clamav:stable`
    - Expose port 3310
    - Volume for virus definitions

- [ ] Update `requirements.txt`
  - Add: `boto3`, `Pillow`, `clamd` (optional)

### Testing
- [ ] Test presigned URL generation
- [ ] Test presign permission checks (owner only)
- [ ] Test upload commit
- [ ] Test cover processing (mock S3, test resizing logic)
- [ ] Test book file processing (mock S3)
- [ ] Test ClamAV scanning (if configured)
- [ ] Test expired upload cleanup
- [ ] Test file format validation
- [ ] Test entity update after processing

---

## Phase 6: Celery Cleanup & Notifications (3-4 days) ⏳ NOT STARTED

### Cleanup Tasks
- [ ] Create `tasks/cleanup.py`

**Soft Delete Cleanup:**
- [ ] `cleanup_soft_deleted_content()` task
  - Runs daily at 2 AM
  - Cutoff: `deleted_at <= now() - 24 hours`
  - Delete from: `authors`, `books`, `collections`, `reviews`
  - Log: deleted counts by entity type
  - Return: summary dict

**Upload Cleanup:**
- [ ] `cleanup_expired_uploads()` task
  - Runs hourly
  - Delete: `PendingUpload` where `expires_at <= now()` AND `status=pending`
  - Delete associated S3 tmp objects
  - Return: count deleted

### Celery Beat Schedule
- [ ] Update `celery_app.py` with beat schedule
  - `cleanup-soft-deleted-daily`: crontab(hour=2, minute=0)
  - `cleanup-expired-uploads-hourly`: crontab(minute=0)

### Notification Stubs
- [ ] Create `tasks/notifications.py`

**Notification Tasks:**
- [ ] `notify_followers(author_id, event, data)` task
  - Log notification for now
  - TODO comment: Implement WebSocket/email in future

- [ ] `notify_subscribers(entity_type, entity_id, event, data)` task
  - Log notification for now
  - TODO comment: Implement WebSocket/email in future

### Testing
- [ ] Test soft delete cleanup (mock datetime)
- [ ] Test expired upload cleanup
- [ ] Test Beat schedule configuration
- [ ] Test notification stubs (log output)
- [ ] Test cleanup runs async (doesn't block app)

---

## Phase 7: Testing & Polish (4-5 days) ⏳ NOT STARTED

### Integration Tests
- [ ] Create comprehensive test suite in `tests/`

**Auth Integration Tests:**
- [ ] `tests/test_auth_integration.py`
  - Test JWT validation with real public key
  - Test scope checks on all endpoints
  - Test role checks
  - Test service token validation
  - Test token blacklist check

**Workflow Tests:**
- [ ] `tests/test_author_workflow.py`
  - Test full lifecycle: create → approve → update → delete → recover
  - Test trust score integration (mock Auth Service calls)
  - Test version conflicts
  - Test permission enforcement

- [ ] `tests/test_book_workflow.py`
  - Test book lifecycle with approval
  - Test review creation and updates
  - Test review voting with trust caps
  - Test subscribe/unsubscribe

- [ ] `tests/test_collection_workflow.py`
  - Test collection lifecycle
  - Test adding/removing books
  - Test versioning

**History & Versioning:**
- [ ] `tests/test_edit_history.py`
  - Test history records all mutations
  - Test version conflict detection (409)
  - Test recovery with history

**Media Tests:**
- [ ] `tests/test_media_upload.py`
  - Test presigned URL generation
  - Test upload commit
  - Test cover processing (mocked S3)
  - Test file validation

**Social Features:**
- [ ] `tests/test_social_features.py`
  - Test follow/unfollow authors
  - Test subscribe to books/collections
  - Test trust bonuses applied

**Cleanup Tests:**
- [ ] `tests/test_cleanup_tasks.py`
  - Test soft delete cleanup (24h window)
  - Test expired upload cleanup
  - Test runs without errors

### Health & Monitoring
- [ ] Add health endpoints to `main.py`

**Health Endpoint:**
- [ ] `GET /health` - Liveness probe
  - Always return: `{"status": "healthy"}`
  - Status: 200

**Ready Endpoint:**
- [ ] `GET /ready` - Readiness probe
  - Check database: `SELECT 1`
  - Check Redis: `PING`
  - Check Celery: worker status (optional)
  - Return: `{"status": "ready"|"not ready", "checks": {...}}`
  - Status: 200 if all OK, 503 if any fail

### Documentation
- [ ] Create `API.md`
  - Document all endpoints with examples
  - Include auth headers
  - Include error responses
  - Include trust score rules

- [ ] Update `README.md`
  - New architecture overview
  - Setup instructions (with .env example)
  - Docker Compose usage
  - Development workflow
  - Testing commands

### Performance Testing
- [ ] Load testing with `locust` or `k6`
  - Test: concurrent book list requests
  - Test: concurrent review voting
  - Test: cache hit rates
  - Target: 100 req/s per endpoint

- [ ] Database query optimization
  - Review all N+1 queries
  - Add indexes where needed
  - Use `selectinload` for relationships

### Testing Summary
- [ ] Run full test suite: `pytest`
- [ ] Check coverage: `pytest --cov=. --cov-report=html`
- [ ] Target: 80%+ coverage
- [ ] Fix any failing tests
- [ ] Document known issues/limitations

---

## Phase 8: Deployment (2-3 days) ⏳ NOT STARTED

### Docker Compose Production
- [ ] Update `docker-compose.yml` for production
  - Environment variable references
  - Health checks for all services
  - Restart policies
  - Volume mounts
  - Network configuration

- [ ] Create `.env.example`
  - All required variables documented
  - Placeholder values
  - Comments for each variable

### Environment Setup
- [ ] Document environment variables
  - Database URLs (async)
  - Redis URL
  - Auth Service URL and API key
  - JWT public key path
  - S3 bucket and region
  - AWS credentials
  - Celery broker/backend
  - ClamAV host (optional)

- [ ] Generate secrets guide
  - Service API key: `secrets.token_urlsafe(32)`
  - Database password generation
  - Redis password (if used)

### Deployment Guide
- [ ] Create `DEPLOY.md`

**Docker Compose Deployment:**
- [ ] Step-by-step setup instructions
- [ ] Environment variable configuration
- [ ] Volume and network setup
- [ ] Migration commands
- [ ] Service startup order

**AWS App Runner Deployment:**
- [ ] Build and push Docker image to ECR
- [ ] Configure App Runner service
- [ ] Set environment variables
- [ ] Configure VPC connector (for RDS/Redis)
- [ ] Configure health check endpoints
- [ ] Configure auto-scaling

**Post-Deployment:**
- [ ] Run migrations: `docker-compose exec app alembic upgrade head`
- [ ] Verify services: check `/health` and `/ready`
- [ ] Test endpoints with curl/Postman
- [ ] Monitor logs

### Initial Data Seeding
- [ ] Create `scripts/seed_initial_data.py`
  - Create admin user (via Auth Service if possible)
  - Create sample approved authors (5-10)
  - Create sample approved books (10-20)
  - Create sample collections (2-3)
  - Output summary

### Monitoring Setup
- [ ] Document logging strategy
  - Structured logs (JSON)
  - Log levels (INFO, WARNING, ERROR)
  - Request ID tracking

- [ ] Document metrics to track
  - Request counts by endpoint
  - Response times (p50, p95, p99)
  - Cache hit rates
  - Trust score adjustment frequency
  - Content approval rates

### Testing Summary
- [ ] Test full stack with docker-compose
- [ ] Test migrations run cleanly
- [ ] Test all services start and stay healthy
- [ ] Test data seeding works
- [ ] Test health/ready endpoints
- [ ] Document any deployment issues

---

## Post-Launch Enhancements (Future Phases)

### Phase 9: Advanced Features
- [ ] Full-text search improvements (Elasticsearch integration)
- [ ] Advanced analytics dashboard
- [ ] Bulk import tools for librarians
- [ ] Admin panel UI
- [ ] Export functionality (CSV, JSON)

### Phase 10: Real Notifications
- [ ] WebSocket server for real-time notifications
- [ ] Email digest system (daily/weekly)
- [ ] In-app notification center
- [ ] Notification preferences per user

### Phase 11: Content Reporting
- [ ] Report flagging system (like Auth Service Phase 4)
- [ ] Auto-lock malicious contributors
- [ ] Curator review queue for reports
- [ ] Ban/suspend workflow

### Phase 12: Performance Optimization
- [ ] Query caching at application level
- [ ] CDN for static assets
- [ ] Read replicas for database
- [ ] Redis cluster for cache
- [ ] Background queue for heavy operations

---

## Development Commands Reference

```bash
# Phase 0: Setup
python -c "import secrets; print('SERVICE_API_KEY=' + secrets.token_urlsafe(32))"
curl http://auth-service:8000/keys/public.pem > keys/public_key.pem
rm -rf migrations/versions/*.py tests/* scripts/seed*.py

# Development
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Celery
celery -A celery_app worker -Q media,email,default -l info
celery -A celery_app beat -l info

# Migrations
alembic revision --autogenerate -m "description"
alembic upgrade head
alembic downgrade -1

# Testing
pytest
pytest tests/test_author_workflow.py -v
pytest --cov=. --cov-report=html
pytest -k "test_approve"

# Docker
docker-compose up --build
docker-compose down -v
docker-compose exec app alembic upgrade head
docker-compose logs -f app
docker-compose exec app pytest

# Seed data
python scripts/seed_initial_data.py
```

---

## Success Criteria

**Phase 0-1 Complete:**
- ✅ Settings loaded from .env with Pydantic
- ✅ JWT authentication working with Auth Service
- ✅ All models created with UUID user IDs
- ✅ Fresh migration applied successfully
- ✅ Edit history tracking implemented

**Phase 2-3 Complete:**
- ✅ Author/Book CRUD requires proper scopes
- ✅ Content approval workflow triggers trust adjustments
- ✅ Optimistic locking prevents edit conflicts
- ✅ Soft deletes with 24h recovery window
- ✅ Review voting adjusts reviewer trust (max ±5)

**Phase 4-6 Complete:**
- ✅ Collections fully functional
- ✅ Media uploads work (S3 presign → process → store)
- ✅ Celery cleanup tasks run on schedule
- ✅ Social features award trust bonuses

**Phase 7-8 Complete:**
- ✅ 80%+ test coverage
- ✅ Health/ready endpoints working
- ✅ Docker Compose full stack runs successfully
- ✅ Deployment documentation complete
- ✅ Initial data seeding works

---

## Notes & Reminders

1. **Trust Score Caps:** Max +6 per author (follows), max ±5 per review. Track externally or in separate table.
2. **Optimistic Lock UI:** Frontend should handle 409 conflicts with diff view.
3. **Service API Key:** Rotate every 30-90 days for security.
4. **JWT Public Key:** Must update if Auth Service rotates keys.
5. **S3 Lifecycle:** Consider deleting tmp uploads older than 24h automatically.
6. **ClamAV:** Optional but recommended for production (catches malicious PDFs).
7. **Rate Limiting:** Consider adding to public endpoints (auth service has examples).
8. **CORS:** Update `main.py` CORS settings for production frontend domain.
9. **Alembic Async:** May need to configure `env.py` to use async engine for migrations.
10. **Testing Auth Service:** Mock HTTP calls in tests, don't hit real Auth Service.

---

**Last Updated:** December 15, 2025  
**Status:** Phase 0 - Ready to start cleanup  
**Next Step:** Delete old code and create settings.py

```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyUrl

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    # Database
    DATABASE_ASYNC_URL: str
    
    # Redis
    REDIS_URL: str | None = None
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    
    # Auth Service Integration
    AUTH_SERVICE_URL: str = "http://auth-service:8000"
    SERVICE_API_KEY: str  # Shared secret - generate with: secrets.token_urlsafe(32)
    JWT_PUBLIC_KEY_PATH: str = "keys/public_key.pem"
    JWT_ALGORITHM: str = "RS256"
    JWT_ISSUER: str = "auth-service"
    JWT_AUDIENCE: str = "backend-services"
    
    # S3 Media
    S3_MEDIA_BUCKET: str | None = None
    S3_MEDIA_REGION: str = "us-east-1"
    
    # Celery
    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str
    CELERY_TASK_DEFAULT_QUEUE: str = "default"
    CELERY_TIMEZONE: str = "UTC"
    
    # Caching
    CACHE_DEFAULT_TTL_SECONDS: int = 300
    
    # ClamAV (optional, for virus scanning)
    CLAMAV_HOST: str | None = None
    CLAMAV_PORT: int = 3310
    
    # Soft Delete Cleanup
    SOFT_DELETE_WINDOW_HOURS: int = 24

settings = Settings()
```

**Environment Variables (.env):**
```bash
# Database
DATABASE_ASYNC_URL=postgresql+asyncpg://postgres:123456@localhost:5432/library_app

# Redis
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

# Auth Service Integration
AUTH_SERVICE_URL=http://auth-service:8000
SERVICE_API_KEY=<generate-with-secrets.token_urlsafe(32)>
JWT_PUBLIC_KEY_PATH=keys/public_key.pem

# S3
S3_MEDIA_BUCKET=library-media
S3_MEDIA_REGION=us-east-1

# Celery
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1
```

### 0.3 Simplify database.py

**Remove:**
- `sync_engine` (only needed for Alembic, will use async there too)
- `SessionLocal`
- `get_db()`
- `load_dotenv()` (handled by Pydantic)

**Keep:**
- `async_engine`
- `AsyncSessionLocal`
- `get_async_db()`
- `Base`

**Updated `database.py`:**
```python
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from settings import settings

async_engine = create_async_engine(
    settings.DATABASE_ASYNC_URL,
    echo=False,
    future=True,
    pool_size=5,
    max_overflow=10
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    autoflush=False,
    expire_on_commit=False
)

Base = declarative_base()


async def get_async_db():
    async with AsyncSessionLocal() as db:
        yield db
```

### 0.4 Update Alembic to Use Async

**File:** `migrations/env.py`

Update to use `async_engine` instead of sync engine.

### 0.5 Update cache.py

**Changes:**
- Remove `load_dotenv()`
- Import from `settings` instead of `os.getenv()`
- Update `_build_redis_url()` to use `settings.REDIS_*`

### 0.6 Update celery_app.py

**Changes:**
- Import from `settings` instead of `os.getenv()`
- Remove `load_dotenv()`
- Add Beat schedule for cleanup tasks

### 0.7 Get JWT Public Key from Auth Service

```bash
# Copy public key from auth service
curl http://auth-service:8000/keys/public.pem > keys/public_key.pem

# Or if auth service uses files:
cp ../library-app-auth-service/keys/public_key.pem keys/
```

**Deliverables:**
- ✅ `settings.py` with all configs
- ✅ Cleaned `database.py` (async-only)
- ✅ Updated `cache.py` (no load_dotenv)
- ✅ Updated `celery_app.py` (settings-based)
- ✅ Empty `migrations/versions/`
- ✅ Empty `tests/` directory
- ✅ JWT public key in `keys/public_key.pem`

---

## 🔐 PHASE 1: Auth Integration & Core Models (4-5 days)

### 1.1 Auth Dependencies (Day 1)

**Create:** `dependencies/auth.py`

```python
import jwt
import uuid
from fastapi import Depends, HTTPException, Header, status
from fastapi.security import HTTPBearer
from pathlib import Path
from redis.asyncio import Redis

from settings import settings
from cache import get_redis

security = HTTPBearer()

# Load JWT public key at startup
PUBLIC_KEY = Path(settings.JWT_PUBLIC_KEY_PATH).read_text()


async def get_current_user(
    credentials = Depends(security),
    r: Redis = Depends(get_redis)
) -> dict:
    """Decode and validate JWT. Returns user claims."""
    token = credentials.credentials
    
    try:
        payload = jwt.decode(
            token,
            PUBLIC_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(401, f"Invalid token: {e}")
    
    # Optional: Check if token is blacklisted in Redis
    jti = payload.get("jti")
    if jti:
        bl_key = f"blacklist:access:{jti}"
        is_blacklisted = await r.exists(bl_key)
        if is_blacklisted:
            raise HTTPException(401, "Token has been revoked")
    
    return payload


def require_scope(*required_scopes: str):
    """Dependency factory: check user has at least one required scope."""
    async def _check(user: dict = Depends(get_current_user)) -> dict:
        user_scopes = set(user.get("scopes", []))
        if not any(scope in user_scopes for scope in required_scopes):
            raise HTTPException(
                403,
                f"Missing required scope. Need one of: {required_scopes}"
            )
        return user
    return _check


def require_role(*required_roles: str):
    """Dependency factory: check user has at least one required role."""
    async def _check(user: dict = Depends(get_current_user)) -> dict:
        user_roles = set(user.get("roles", []))
        if not any(role in user_roles for role in required_roles):
            raise HTTPException(
                403,
                f"Missing required role. Need one of: {required_roles}"
            )
        return user
    return _check


async def verify_service_token(x_service_token: str = Header(None)):
    """Verify service-to-service calls (from auth service)."""
    if not settings.SERVICE_API_KEY:
        return  # Dev mode - no validation
    
    if not x_service_token or x_service_token != settings.SERVICE_API_KEY:
        raise HTTPException(401, "Invalid service token")
```

### 1.2 Auth Service Client (Day 1)

**Create:** `services/auth_client.py`

```python
import httpx
import uuid
from typing import Literal

from settings import settings


async def adjust_user_trust(
    user_id: uuid.UUID,
    delta: int,
    reason: str,
    source: Literal["upload", "review", "social"]
) -> dict:
    """
    Call Auth Service to adjust user's trust score.
    
    This triggers:
    - Trust score update
    - Reputation recalculation
    - Role changes (delayed upgrade or immediate downgrade)
    - Token blacklisting if roles changed
    
    Args:
        user_id: User UUID
        delta: Trust score change (positive or negative)
        reason: Human-readable explanation
        source: Category of adjustment
        
    Returns:
        Response with new trust_score, roles, pending_upgrade
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{settings.AUTH_SERVICE_URL}/user/admin/users/{user_id}/trust/adjust",
                headers={"X-Service-Token": settings.SERVICE_API_KEY},
                json={
                    "delta": delta,
                    "reason": reason,
                    "source": source
                },
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            # Log but don't fail the operation
            print(f"Failed to adjust trust score for {user_id}: {e}")
            return {"error": str(e)}


async def record_submission_outcome(
    user_id: uuid.UUID,
    success: bool,
    entity_type: str,
    entity_name: str
) -> dict:
    """
    Record content submission outcome for reputation calculation.
    
    This is called alongside trust adjustment to update:
    - successful_submissions (if success=True)
    - total_submissions (always)
    - reputation_percentage (recalculated)
    
    Note: This is implicit in approve/reject - Auth Service handles it
    when trust is adjusted with source="upload".
    """
    # Auth Service automatically handles reputation when source="upload"
    # This function is a placeholder for explicit reputation updates if needed
    pass
```

### 1.3 Redesign Models (Day 2-3)

**Complete rewrite of `models.py`** with:
- UUID user IDs (matching Auth Service)
- Status workflow (pending/approved/rejected)
- Soft deletes (is_deleted, deleted_at)
- Optimistic locking (version, last_edited_by, last_edited_at)
- Social features (follower_count, subscriber_count)
- Edit history tracking
- All new tables (Collection, EditHistory, Follows, Subscriptions, Votes)

**File:** `models.py` (see full schema in earlier response - 500+ lines)

**Key Changes:**
- `Author`: Add `created_by_user_id`, `linked_user_id`, `status`, `is_public`, `is_deleted`, `version`, `bio`
- `Book`: Add `created_by_user_id`, `status`, `is_public`, `is_deleted`, `version`, `file_key`, `file_format`
- `Review`: Change `reviewer_name` → `user_id`, add helpfulness tracking
- `PendingUpload`: Change `user_id` from `int` → `UUID`, add `entity_type`, `entity_id`
- **New:** `Collection`, `CollectionBook`, `EditHistory`, `AuthorFollow`, `BookSubscription`, `CollectionSubscription`, `ReviewVote`

### 1.4 Generate Fresh Migration (Day 3)

```bash
# Create new migration
alembic revision --autogenerate -m "complete_schema_redesign"

# Review migration file, then apply
alembic upgrade head
```

### 1.5 Update Schemas (Day 4)

**Update all schema files** to match new models:

- `schemas/author.py`: Add status, version, bio, linked_user_id
- `schemas/book.py`: Add status, version, file_key, created_by_user_id
- `schemas/review.py`: Change reviewer_name → user_id, add helpfulness
- `schemas/shared.py`: Update base schemas
- **New:** `schemas/collection.py`
- **New:** `schemas/history.py`

### 1.6 Helper Functions (Day 4)

**Create:** `helpers/edit_history.py`

```python
import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from models import EditHistory


async def record_edit(
    db: AsyncSession,
    entity_type: str,
    entity_id: int,
    action: str,
    user_id: uuid.UUID,
    version: int,
    parent_version: int | None = None,
    old_data: dict | None = None,
    new_data: dict | None = None
):
    """Record an edit in history table."""
    changes = {}
    if old_data and new_data:
        for key in new_data:
            if key in old_data and old_data[key] != new_data[key]:
                changes[key] = {"old": old_data[key], "new": new_data[key]}
    
    history = EditHistory(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        user_id=user_id,
        version=version,
        parent_version=parent_version,
        old_data=old_data,
        new_data=new_data,
        changes=changes,
        created_at=datetime.now(timezone.utc)
    )
    db.add(history)
```

**Deliverables:**
- ✅ `dependencies/auth.py` (JWT validation, scope/role checks)
- ✅ `services/auth_client.py` (trust score API calls)
- ✅ Complete `models.py` (12 tables, UUID user IDs)
- ✅ Fresh migration (drop old, create new schema)
- ✅ Updated schemas (match new models)
- ✅ `helpers/edit_history.py` (reusable edit tracking)

---

## 📚 PHASE 2: Author Workflow (4-5 days)

### 2.1 Rewrite Author Router (Day 1-3)

**File:** `routers/author.py` - Complete rewrite

**Endpoints:**

**Public (No Auth):**
- `GET /authors` - List public approved authors (with search)
- `GET /authors/{id}` - Get author detail (public if approved)
- `GET /authors/{id}/books` - Books by author

**Authenticated:**
- `POST /authors` - Create author (pending) - `content:submit` scope
- `PATCH /authors/{id}` - Update author (owner or `content:edit_any`) - **with version check**
- `DELETE /authors/{id}` - Soft delete (owner or `content:delete_any`)

**Curator (Admin Prefix):**
- `GET /admin/authors/pending` - List pending submissions - `content:review` scope
- `POST /admin/authors/{id}/approve` - Approve author - `content:approve` scope
  - Sets status=approved, is_public=True
  - Calls Auth Service: `adjust_trust(+10, source="upload")`
- `POST /admin/authors/{id}/reject` - Reject author - `content:approve` scope
  - Sets status=rejected, is_public=False
  - Calls Auth Service: `adjust_trust(-5, source="upload")`
- `POST /admin/authors/{id}/recover` - Recover soft-deleted (24h window) - `content:recover` scope

**Social:**
- `POST /authors/{id}/follow` - Follow author - `social:follow` scope
  - Increment follower_count
  - Calls Auth Service: `adjust_trust(submitter, +3, source="social")` (max +6 cap via external tracking)
- `DELETE /authors/{id}/follow` - Unfollow author
- `GET /authors/{id}/followers` - List followers (user IDs)

**Implementation Notes:**
- All mutations record edit history via `record_edit()`
- PATCH requires `expected_version` query param for optimistic lock
- Return 409 Conflict if version mismatch
- Cache invalidation on all mutations

### 2.2 Update Cache Helpers (Day 3)

Add cache functions for:
- `invalidate_author_follows(author_id)`
- Extend `invalidate_author()` to handle new relationships

### 2.3 Write Tests (Day 4)

**Create:** `tests/test_author_workflow.py`

Test cases:
- Create author (pending status)
- Approve author (trust +10)
- Reject author (trust -5)
- Update with correct version (succeeds)
- Update with wrong version (409 conflict)
- Soft delete and recover (within 24h)
- Follow/unfollow (trust +3)

### 2.4 Update main.py (Day 4)

- Import new `settings`
- Remove old imports
- Health check endpoint

**Deliverables:**
- ✅ Complete `routers/author.py` (public, auth, curator, social)
- ✅ Edit history on all mutations
- ✅ Optimistic locking with version checks
- ✅ Trust score integration (approval/rejection/follows)
- ✅ Cache invalidation
- ✅ Basic tests

---

## 📖 PHASE 3: Books & Reviews Workflow (5-6 days)

### 3.1 Rewrite Book Router (Day 1-3)

**File:** `routers/book.py` - Major refactor

**Public:**
- `GET /books` - List public books (keep cursor pagination)
- `GET /books/{id}` - Book detail
- `GET /books/{id}/reviews` - Reviews for book

**Authenticated:**
- `POST /books` - Create book (pending) - `content:submit` scope
- `PATCH /books/{id}` - Update book - **with version check**
- `DELETE /books/{id}` - Soft delete
- `POST /books/{id}/reviews` - Add review (user_id from JWT)
- `PATCH /reviews/{id}` - Update own review
- `DELETE /reviews/{id}` - Soft delete own review

**Curator:**
- `GET /admin/books/pending` - List pending books
- `POST /admin/books/{id}/approve` - Approve (+20 trust)
- `POST /admin/books/{id}/reject` - Reject (-10 trust)
- `POST /admin/books/{id}/recover` - Recover soft-deleted

**Social:**
- `POST /books/{id}/subscribe` - Subscribe (+3 trust to uploader)
- `DELETE /books/{id}/subscribe` - Unsubscribe
- `POST /reviews/{id}/vote` - Vote helpful/unhelpful (triggers trust ±1 to reviewer, max ±5)

**Key Changes:**
- Remove PUT endpoints (use PATCH only)
- Add version checks
- Change reviewer_name → user_id
- Add helpfulness voting logic with caps

### 3.2 Review Voting Logic (Day 3)

**Create:** `services/review_voting.py`

```python
async def vote_on_review(
    review_id: int,
    voter_id: uuid.UUID,
    vote: Literal["helpful", "unhelpful"],
    voter_trust_score: int,
    db: AsyncSession
) -> dict:
    """
    Vote on review helpfulness and adjust reviewer's trust.
    
    Rules:
    - Only trusted+ users (trust >= 50) can vote
    - Max ±5 trust per review (tracked in review.trust_awarded)
    - Helpful: +1, Unhelpful: -1
    """
    # Check voter eligibility
    if voter_trust_score < 50:
        raise ValueError("Need trust score >= 50 to vote on reviews")
    
    # Check existing vote
    stmt = select(ReviewVote).where(
        ReviewVote.review_id == review_id,
        ReviewVote.user_id == voter_id
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    
    if existing:
        if existing.vote == vote:
            return {"message": "Already voted", "trust_delta": 0}
        # Change vote - reverse previous and apply new
        # ... implementation
    
    # Load review
    review = await db.get(Review, review_id)
    if not review:
        raise ValueError("Review not found")
    
    # Check trust cap
    if abs(review.trust_awarded) >= 5:
        return {"message": "Review already at max trust adjustment", "trust_delta": 0}
    
    # Calculate delta
    delta = 1 if vote == "helpful" else -1
    
    # Apply vote
    vote_record = ReviewVote(
        user_id=voter_id,
        review_id=review_id,
        vote=vote
    )
    db.add(vote_record)
    
    # Update counts
    if vote == "helpful":
        review.helpful_count += 1
    else:
        review.unhelpful_count += 1
    
    review.trust_awarded += delta
    
    await db.commit()
    
    # Adjust reviewer's trust (async call to auth service)
    await adjust_user_trust(
        user_id=review.user_id,
        delta=delta,
        reason=f"Review voted {vote}",
        source="review"
    )
    
    return {"message": "Vote recorded", "trust_delta": delta}
```

### 3.3 Update Book Schemas (Day 4)

- Add version, status, file_key, created_by_user_id
- Remove ISBN from required (optional)
- Update BookDetailRead to include reviews with helpfulness counts

### 3.4 Write Tests (Day 5)

- Book approval/rejection workflow
- Review creation with user_id
- Review voting (helpful/unhelpful)
- Trust cap enforcement (max ±5 per review)
- Version conflict handling

**Deliverables:**
- ✅ Rewritten `routers/book.py` (workflow + voting)
- ✅ `services/review_voting.py` (helpfulness logic)
- ✅ Trust adjustments on approval/rejection/voting
- ✅ Optimistic locking
- ✅ Tests for book + review workflows

---

## 📂 PHASE 4: Collections (3-4 days)

### 4.1 Collection Router (Day 1-2)

**Create:** `routers/collection.py`

**Endpoints:**
- `GET /collections` - List public collections
- `GET /collections/{id}` - Collection detail with books
- `POST /collections` - Create (pending) - `content:submit` scope
- `PATCH /collections/{id}` - Update - **with version check**
- `DELETE /collections/{id}` - Soft delete
- `POST /collections/{id}/books` - Add book to collection
- `DELETE /collections/{id}/books/{book_id}` - Remove book
- `POST /collections/{id}/subscribe` - Subscribe (+3 trust)
- `DELETE /collections/{id}/subscribe` - Unsubscribe

**Curator:**
- `GET /admin/collections/pending`
- `POST /admin/collections/{id}/approve` (+10 trust)
- `POST /admin/collections/{id}/reject` (-5 trust)
- `POST /admin/collections/{id}/recover`

### 4.2 Collection Schemas (Day 2)

**Create:** `schemas/collection.py`

Similar structure to Author/Book schemas.

### 4.3 Write Tests (Day 3)

**Deliverables:**
- ✅ `routers/collection.py` (full workflow)
- ✅ `schemas/collection.py`
- ✅ Tests

---

## 🎨 PHASE 5: Media Management (5-6 days)

### 5.1 S3 Upload Flow (Day 1-2)

**Create:** `routers/upload.py`

**Endpoints:**
- `POST /uploads/presign` - Get presigned URL for upload
  - Request: `{upload_type: "book_cover"|"book_file"|"author_avatar", entity_type, entity_id}`
  - Response: `{upload_url, s3_key, expires_at}`
  - Creates `PendingUpload` record
- `POST /uploads/commit` - Confirm upload, trigger processing
  - Request: `{s3_key, entity_type, entity_id}`
  - Triggers Celery task: `process_media_upload.delay(s3_key, ...)`

**Create:** `services/storage.py`

```python
import boto3
from botocore.client import Config
from settings import settings

def get_s3_client():
    return boto3.client(
        "s3",
        region_name=settings.S3_MEDIA_REGION,
        config=Config(signature_version="s3v4")
    )

async def generate_presigned_upload_url(
    s3_key: str,
    content_type: str,
    max_size: int = 10 * 1024 * 1024  # 10MB
) -> str:
    """Generate presigned URL for direct upload to S3."""
    s3 = get_s3_client()
    
    url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.S3_MEDIA_BUCKET,
            "Key": s3_key,
            "ContentType": content_type,
        },
        ExpiresIn=600,  # 10 minutes
        HttpMethod="PUT"
    )
    
    return url
```

### 5.2 Celery Media Tasks (Day 3-4)

**Update:** `tasks/media.py`

```python
from celery import shared_task
import boto3
import tempfile
from PIL import Image

@shared_task
def process_book_cover(s3_key: str, entity_type: str, entity_id: int):
    """
    Download cover, validate, resize to multiple sizes, upload back.
    
    Sizes: 512px, 256px, 128px (max side)
    Format: WebP
    """
    # 1. Download from S3 (tmp location)
    # 2. Validate image (PIL, check dimensions, file size)
    # 3. Resize to multiple sizes
    # 4. Upload to final location: covers/{entity_type}/{entity_id}/{size}.webp
    # 5. Update entity record with cover_key
    # 6. Delete tmp S3 object
    pass

@shared_task
def process_book_file(s3_key: str, book_id: int):
    """
    Download book file, validate, scan for viruses, extract metadata.
    
    Formats: PDF, EPUB, MOBI
    """
    # 1. Download from S3
    # 2. Validate file format
    # 3. ClamAV scan (if configured)
    # 4. Extract metadata (title, author, page count)
    # 5. Move to final location: books/{book_id}/file.{ext}
    # 6. Update book record with file_key, metadata
    # 7. Delete tmp object
    pass

@shared_task
def scan_with_clamav(file_path: str) -> dict:
    """Scan file with ClamAV if configured."""
    if not settings.CLAMAV_HOST:
        return {"status": "skipped", "clean": True}
    
    # Use clamd library
    import clamd
    cd = clamd.ClamdNetworkSocket(
        host=settings.CLAMAV_HOST,
        port=settings.CLAMAV_PORT
    )
    result = cd.scan(file_path)
    # ... parse result
    pass
```

### 5.3 Update docker-compose.yml (Day 5)

Add services:
- `worker`: Celery worker
- `beat`: Celery beat (for cleanup tasks)
- `clamav`: ClamAV daemon (optional)

### 5.4 Write Tests (Day 6)

- Presigned URL generation
- Upload commit flow
- Cover processing (mock S3)

**Deliverables:**
- ✅ `routers/upload.py` (presign + commit)
- ✅ `services/storage.py` (S3 helpers)
- ✅ `tasks/media.py` (cover + file processing)
- ✅ Updated docker-compose.yml (worker + clamav)
- ✅ Tests

---

## 🧹 PHASE 6: Celery Cleanup & Notifications (3-4 days)

### 6.1 Cleanup Tasks (Day 1-2)

**Create:** `tasks/cleanup.py`

```python
from celery import shared_task
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, delete
from database import AsyncSessionLocal
from models import Author, Book, Collection, Review
from settings import settings

@shared_task
def cleanup_soft_deleted_content():
    """
    Hard delete content soft-deleted more than 24 hours ago.
    
    Runs daily at 2 AM.
    """
    import asyncio
    
    async def _cleanup():
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=settings.SOFT_DELETE_WINDOW_HOURS
        )
        
        async with AsyncSessionLocal() as db:
            deleted_counts = {}
            
            # Authors
            stmt = delete(Author).where(
                Author.is_deleted == True,
                Author.deleted_at <= cutoff
            )
            result = await db.execute(stmt)
            deleted_counts["authors"] = result.rowcount
            
            # Books
            stmt = delete(Book).where(
                Book.is_deleted == True,
                Book.deleted_at <= cutoff
            )
            result = await db.execute(stmt)
            deleted_counts["books"] = result.rowcount
            
            # Collections
            stmt = delete(Collection).where(
                Collection.is_deleted == True,
                Collection.deleted_at <= cutoff
            )
            result = await db.execute(stmt)
            deleted_counts["collections"] = result.rowcount
            
            # Reviews
            stmt = delete(Review).where(
                Review.is_deleted == True,
                Review.deleted_at <= cutoff
            )
            result = await db.execute(stmt)
            deleted_counts["reviews"] = result.rowcount
            
            await db.commit()
            
            return deleted_counts
    
    return asyncio.run(_cleanup())

@shared_task
def cleanup_expired_uploads():
    """Delete expired pending uploads."""
    import asyncio
    
    async def _cleanup():
        async with AsyncSessionLocal() as db:
            now = datetime.now(timezone.utc)
            stmt = delete(PendingUpload).where(
                PendingUpload.expires_at <= now,
                PendingUpload.status == "pending"
            )
            result = await db.execute(stmt)
            await db.commit()
            return result.rowcount
    
    return asyncio.run(_cleanup())
```

### 6.2 Update Celery Beat Schedule (Day 2)

**Update:** `celery_app.py`

```python
from celery.schedules import crontab

app.conf.beat_schedule = {
    'cleanup-soft-deleted-daily': {
        'task': 'tasks.cleanup.cleanup_soft_deleted_content',
        'schedule': crontab(hour=2, minute=0),  # 2 AM daily
    },
    'cleanup-expired-uploads-hourly': {
        'task': 'tasks.cleanup.cleanup_expired_uploads',
        'schedule': crontab(minute=0),  # Every hour
    },
}
```

### 6.3 Notification Stubs (Day 3)

**Create:** `tasks/notifications.py`

```python
@shared_task
def notify_followers(author_id: int, event: str, data: dict):
    """
    Notify followers when author content updates.
    
    Phase 1: Just log
    Future: WebSocket push, email digest
    """
    print(f"[NOTIFICATION] Author {author_id} - {event}: {data}")
    # TODO: Implement real notifications
    pass

@shared_task
def notify_subscribers(entity_type: str, entity_id: int, event: str, data: dict):
    """
    Notify subscribers of book/collection updates.
    """
    print(f"[NOTIFICATION] {entity_type} {entity_id} - {event}: {data}")
    pass
```

**Deliverables:**
- ✅ `tasks/cleanup.py` (soft delete + upload cleanup)
- ✅ Beat schedule configured
- ✅ `tasks/notifications.py` (stubs)

---

## 🧪 PHASE 7: Testing & Polish (4-5 days)

### 7.1 Integration Tests (Day 1-3)

**Create comprehensive test suite:**

- `tests/test_auth_integration.py` - JWT validation, scope checks
- `tests/test_author_workflow.py` - Full workflow + trust integration
- `tests/test_book_workflow.py` - Approval + review voting
- `tests/test_collection_workflow.py` - Collection management
- `tests/test_edit_history.py` - Version conflicts, recovery
- `tests/test_media_upload.py` - Presign + processing (mocked)
- `tests/test_social_features.py` - Follow/subscribe + trust caps
- `tests/test_cleanup_tasks.py` - Celery cleanup tasks

### 7.2 Health & Ready Endpoints (Day 3)

**Update:** `main.py`

```python
@app.get("/health")
async def health():
    """Liveness probe."""
    return {"status": "healthy"}

@app.get("/ready")
async def ready(db: AsyncSession = Depends(get_async_db)):
    """Readiness probe with dependency checks."""
    checks = {}
    
    # Database
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
    
    # Redis
    try:
        r = await init_redis()
        await r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"
    
    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503
    
    return JSONResponse(
        content={"status": "ready" if all_ok else "not ready", "checks": checks},
        status_code=status_code
    )
```

### 7.3 Documentation (Day 4)

**Create:** `API.md` - API documentation with examples

**Update:** `README.md` - New architecture, setup instructions

### 7.4 Performance Testing (Day 5)

- Load test with `locust` or `k6`
- Cache hit rate monitoring
- Database query optimization

**Deliverables:**
- ✅ Comprehensive test suite (80%+ coverage)
- ✅ Health/ready endpoints
- ✅ API documentation
- ✅ Updated README
- ✅ Performance benchmarks

---

## 🚀 PHASE 8: Deployment (2-3 days)

### 8.1 Update docker-compose.yml (Day 1)

**Complete stack:**
- app (FastAPI)
- worker (Celery worker)
- beat (Celery beat)
- db (PostgreSQL 16)
- redis (Redis 7)
- clamav (optional)

### 8.2 Environment Variables (Day 1)

Create `.env.example`:
```bash
# Database
DATABASE_ASYNC_URL=postgresql+asyncpg://postgres:password@db:5432/library_app

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# Auth Service Integration
AUTH_SERVICE_URL=http://auth-service:8000
SERVICE_API_KEY=CHANGE_ME_GENERATE_WITH_secrets.token_urlsafe(32)
JWT_PUBLIC_KEY_PATH=keys/public_key.pem

# S3
S3_MEDIA_BUCKET=library-media-bucket
S3_MEDIA_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...

# Celery
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1

# Optional
CLAMAV_HOST=clamav
CLAMAV_PORT=3310
```

### 8.3 Deployment Docs (Day 2)

**Create:** `DEPLOY.md`

- Docker Compose deployment
- AWS App Runner deployment
- Environment setup
- Migration commands
- Monitoring setup

### 8.4 Initial Data Seeding (Day 3)

**Create:** `scripts/seed_initial_data.py`

Minimal seed data for testing:
- A few approved authors
- A few approved books
- Sample collections

**Deliverables:**
- ✅ Production-ready docker-compose
- ✅ `.env.example`
- ✅ `DEPLOY.md`
- ✅ Seed script

---

## 📋 Post-Launch (Future Phases)

### Phase 9: Advanced Features
- Full-text search improvements (Elasticsearch?)
- Advanced analytics dashboard
- Bulk import tools
- Admin panel UI

### Phase 10: Notifications
- WebSocket real-time notifications
- Email digests
- In-app notification center

### Phase 11: Reporting System
- Content flagging (like Auth Service Phase 4)
- Auto-lock malicious users
- Curator review queue

---

## 🔧 Development Commands

```bash
# Phase 0: Setup
python -c "import secrets; print(secrets.token_urlsafe(32))"  # Generate SERVICE_API_KEY
curl http://auth-service:8000/keys/public.pem > keys/public_key.pem

# Development
uvicorn main:app --reload

# Celery
celery -A celery_app worker -Q media,email,default -l info
celery -A celery_app beat -l info

# Migrations
alembic revision --autogenerate -m "description"
alembic upgrade head

# Tests
pytest
pytest tests/test_author_workflow.py -v
pytest --cov=. --cov-report=html

# Docker
docker-compose up --build
docker-compose exec app alembic upgrade head
```

---

## 🎯 Success Criteria

- ✅ JWT authentication working with Auth Service
- ✅ All CRUD operations require proper scopes
- ✅ Content approval workflow triggers trust adjustments
- ✅ Edit history tracks all changes with version control
- ✅ Soft deletes with 24h recovery window
- ✅ Social features (follow/subscribe) award trust bonuses
- ✅ Review voting adjusts reviewer trust (max ±5)
- ✅ Media uploads work (S3 presign → process → store)
- ✅ Celery cleanup tasks run on schedule
- ✅ 80%+ test coverage
- ✅ Health/ready endpoints working
- ✅ Docker Compose full stack runs successfully

---

## 📝 Notes

1. **Trust Score Caps:** Need to track caps externally (e.g., max +6 per author from follows). Store in Redis or separate table.
2. **Optimistic Lock UI:** Frontend should handle 409 conflicts gracefully with diff view.
3. **Service API Key:** Rotate periodically (30-90 days).
4. **JWT Public Key:** Update if Auth Service rotates keys.
5. **S3 Costs:** Consider lifecycle policies for old uploads (delete after 30 days if not committed).
6. **ClamAV:** Optional but recommended for production (catches malicious PDFs).
7. **Rate Limiting:** Consider adding rate limits to public endpoints (auth service has examples).
8. **CORS:** Update `main.py` CORS settings for production frontend domain.

---

**Last Updated:** December 15, 2025
**Status:** Ready to implement Phase 0
