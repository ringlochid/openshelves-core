# Library Service (FastAPI + PostgreSQL + Redis)

Production-grade wiki-style content platform with RBAC, trust scoring, and jury-based governance. Complete schema redesign with workflow states, versioning, and full async support.

## Development Status

**Phase 1: Database Schema & Testing** ✅ **COMPLETED**
- Complete schema redesign with UUID user IDs, workflow states, and versioning
- 12 models with proper relationships and constraints (authors, books, reviews, collections, etc.)
- Full async session support with optimized connection pooling
- Edit history tracking with version conflict detection
- 26 passing unit tests for helpers and core functionality
- Legacy routers preserved as `.py.old` (awaiting Phase 2-4 rewrite)

**Phase 2: Auth Integration & Author Workflow** ✅ **COMPLETED**
- JWT authentication with Auth Service integration (RSA public key validation)
- Trust score adjustments with retry logic and graceful degradation
- Complete author workflow: create → approve/reject → follow/unfollow → soft delete
- 10 author endpoints with full CRUD, approval, and social features
- Optimistic locking with version conflict detection (HTTP 409)
- Edit history recording for all operations (CREATE/UPDATE/APPROVE/REJECT/DELETE)
- **Jury voting system**: Democratic approval (contributor=1 vote, trusted=5 votes, auto-publish at threshold)
- **Permission system**: Owner vs wiki-editor (APPROVED content only), curator override
- **88 tests passing** (12 auth + 10 cursor + 16 edit_history + 17 author workflow + 33 jury voting)
- All tests use real PostgreSQL database (no SQLite mocks)

**Phase 3: Books & Reviews Workflow** ⏳ **NEXT**
- Book workflow with approval system (+20/-10 trust, doubled from authors)
- Review system with user_id (no more reviewer_name)
- Review voting: helpful/unhelpful with trust scoring (±1, max ±5 per review)
- Book subscription system with social bonuses
- Target: 75+ tests passing

## Stack and Capabilities
- **FastAPI + Uvicorn** - High-performance async web framework with Pydantic v2
- **SQLAlchemy 2.x (async)** - Full async ORM with PostgreSQL
- **PostgreSQL** - Advanced features (FTS, trigram search, GIN indexes, ARRAY types)
- **Redis** - Caching with versioned invalidation
- **Alembic** - Database migrations with custom extensions
- **Celery** - Background tasks (cleanup, media processing)
- **Docker** - Containerized deployment

## Database Schema Highlights

**Core Models:**
- **Authors** - UUID user tracking, linked profiles, workflow states, follower counts
- **Books** - File uploads, tags (ARRAY), full-text search, version tracking
- **Reviews** - UUID users, voting system with helpful/unhelpful counts
- **Collections** - Ordered book lists with position management
- **Edit History** - Complete audit trail with field-level change tracking

**Advanced Features:**
- PostgreSQL extensions: `unaccent`, `pg_trgm` for fuzzy search
- Full-text search with `ts_vector` and similarity ranking
- Trigram indexes for typo-tolerant author/book search
- GIN indexes for ARRAY operations and text search
- CHECK constraints for data integrity (15+ constraints)
- Optimistic locking with version conflict detection

## Running the API

### Docker Compose (Development)
1) Copy environment template:
```bash
cp .env.example .env
# Edit .env with your settings
```

2) Build and start services:
```bash
docker-compose up --build
```

3) Apply migrations:
```bash
docker compose exec app alembic upgrade head
```

4) Run tests:
```bash
docker compose exec app pytest tests/ -v
```

5) API available at `http://localhost:8000` (docs at `/docs`)

## Testing

**All Tests (88 tests passing with real PostgreSQL):**
```bash
# Run all tests
docker compose exec app pytest tests/ -v

# Run specific test suites
docker compose exec app pytest tests/test_auth_jwt.py -v                # 12 auth tests
docker compose exec app pytest tests/test_cursor.py -v                  # 10 cursor tests
docker compose exec app pytest tests/test_edit_history.py -v            # 16 edit history tests
docker compose exec app pytest tests/test_author_workflow.py -v         # 17 author workflow tests
docker compose exec app pytest tests/test_jury_voting.py -v             # 12 jury voting tests
docker compose exec app pytest tests/test_author_ownership.py -v        # 8 ownership tests
docker compose exec app pytest tests/test_author_publish_paths.py -v    # 4 publish path tests
docker compose exec app pytest tests/test_curator_override.py -v        # 4 curator override tests
docker compose exec app pytest tests/test_author_edge_cases.py -v       # 5 edge case tests
```

**Test Coverage:**
- **Auth validation** (12 tests): JWT decoding, scope checks, role validation, trust requirements
- **Cursor pagination** (10 tests): encoding/decoding, error handling, URL-safe validation
- **Edit history** (16 tests): version conflicts, change calculation, entity serialization
- **Author workflow** (17 tests): model validation, permission logic, workflow states, social features
- **Jury voting** (12 tests): vote weights, auto-publish, retraction, queue filtering
- **Ownership permissions** (8 tests): owner vs wiki-editor, permission matrix
- **Publish paths** (4 tests): trusted direct publish vs regular pending
- **Curator override** (4 tests): instant approve/reject, vote clearing
- **Edge cases** (5 tests): status transitions, takedown vs delete

**Testing Strategy:**
- Uses real PostgreSQL database (not SQLite)
- Transaction rollback keeps tests isolated
- RS256 JWT authentication matching production
- All async operations properly tested with `pytest-asyncio`
- Mock Auth Service calls to avoid external dependencies

## Configuration

All settings managed via `settings.py` with Pydantic BaseSettings:

**Required Environment Variables:**
- `DATABASE_ASYNC_URL` - PostgreSQL async connection (asyncpg)
- `DATABASE_SYNC_URL` - PostgreSQL sync for Alembic (psycopg)
- `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB` - Redis configuration
- `SERVICE_API_KEY` - Shared secret for Auth Service communication
- `JWT_PUBLIC_KEY_PATH` - Path to JWT public key for validation

See `.env.example` for complete configuration template.

## Caching Strategy
- **Detail caches**: `author:{id}`, `book:{id}`, `review:{id}` (TTL: 300s)
- **List caches**: Versioned with `authors:list:v{n}`, `books:list:v{n}`
- **Invalidation**: Mutations bump version counters, orphaning old caches
- **Redis patterns**: Single-key operations to avoid CROSSSLOT errors in cluster mode

## Development Notes

**Async Session Patterns (SQLAlchemy 2.0):**
- Always use `AsyncSession` from `database.get_async_db()`
- **IMPORTANT**: `await db.delete(obj)` - delete IS a coroutine in SQLAlchemy 2.0
- `db.add()` is synchronous (no await needed)
- All query operations need await: `execute()`, `commit()`, `flush()`, `refresh()`, `delete()`
- Connection pool: 10 base connections, 20 max overflow, pre-ping enabled

**Migration Management:**
```bash
# Generate migration
docker compose exec app alembic revision --autogenerate -m "description"

# Apply migrations
docker compose exec app alembic upgrade head

# Rollback one version
docker compose exec app alembic downgrade -1
```

## Development Tips
- Migrations live in `migrations/`; use `alembic revision --autogenerate -m "msg"` then `alembic upgrade head`.
- SQLAlchemy is async in the API layer; ensure any new background workers reuse the async engine/session and Redis client.
- Keep `pydantic` instantiation via `model_validate(..., from_attributes=True)` for ORM objects (already applied).

## Roadmap / Next Steps
- Add auth and rate limiting; protect public endpoint (WAF/API key/JWT).
- Add request logging/metrics and basic observability.
- Media pipeline to S3 (covers, PDFs, avatars) with presigned URLs.
- Add a worker for bulk imports/cache warming.
- Expand tests around caching invariants, search/pagination edge cases, and high-concurrency writes.

## Deployed / Tested
- App Runner + RDS + ElastiCache (TLS) seeded successfully via `scripts/seed_file_async.py` with 116 authors and 50,000 books.

## API Routes and How to Call Them
Base URL defaults to `http://localhost:8000`. All payloads are JSON; send `Content-Type: application/json`.

### Books
- `GET /books` — List books with filters. Query: `q` (full-text + trigram search), `title`, `isbn`, `author_id`, `before`/`after` (year), `limit` (1–100, default 20), `offset` (works only when `cursor` is absent), `cursor` (keyset pagination only when primary sort is similarity), `sort` (repeatable; `title:asc`, `year:desc`, `similarity:desc`; `similarity` requires `q`). Response: `{"items": [...], "next_cursor": "..."|null}` with authors embedded on each item. Example: `curl 'http://localhost:8000/books?q=asimov&sort=similarity:desc&limit=5'`.
- `GET /books/{book_id}` — Book detail (authors + reviews). 404 if missing.
- `GET /books/{book_id}/reviews` — All reviews for a book.
- `POST /books` — Create book. Body `{"title": "...", "year": 1999, "book_isbn": "...", "genre_name": "...", "description": "...", "author_ids": [1,2]}`. Author IDs must exist; returns created book with authors.
- `POST /books/{book_id}/reviews` — Add review. Body `{"reviewer_name": "...", "rating": 1-5, "comment": "..."}`. Fails with 400 if the reviewer already reviewed the book.
- `PUT /books/{book_id}` — Replace a book using the same shape as `POST /books` (authors overwritten).
- `PUT /books/{book_id}/authors` — Replace the book’s author list. Body is an array of author IDs, e.g., `[3,4]`.
- `PATCH /books/{book_id}` — Partial update. Any subset of `title`, `year`, `book_isbn`, `genre_name`, `description`.
- `DELETE /books/{book_id}` — Delete book and cascade-delete its reviews. 204 on success.

### Authors
- `GET /authors` — List authors. Query: `q` (unaccented similarity on name/email), `name`, `email`, `limit` (1–100, default 20), `offset` (default 0). Returns `[{id, name, email}]`.
- `GET /authors/{author_id}` — Single author. Returns `{id, name, email}`; 404 if missing.
- `GET /authors/{author_id}/books` — Books for an author. Returns `[{id, title, year}]`.
- `POST /authors` — Create author. Body `{"name": "...", "email": "...", "book_ids": [1,2]}` (book IDs optional; must exist if provided).
- `PUT /authors/{author_id}` — Replace author with same shape as `POST /authors` (books overwritten).
- `PATCH /authors/{author_id}` — Partial update. `book_ids` is optional; when provided, it replaces the list (empty list clears all).
- `DELETE /authors/{author_id}` — Delete author. 204 on success.

### Reviews
- `DELETE /reviews/{review_id}` — Delete a review by id. 204 on success.
