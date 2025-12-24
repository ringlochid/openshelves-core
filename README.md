# Library Service (FastAPI + PostgreSQL + Redis + Celery)

Production-grade wiki-style content platform with RBAC, trust scoring, jury-based governance, and full media upload pipeline.

## Features

- **Wiki-style Content Management**: Authors, Books, Collections with workflow states
- **Jury Voting System**: Democratic approval with contributor/trusted vote weights
- **Full-Text Search**: PostgreSQL FTS + trigram similarity for typo tolerance
- **Media Upload Pipeline**: S3 presigned URLs → client upload → Celery processing
- **Multi-Size Image Processing**: Covers (2:3 ratio), Avatars (1:1 square)
- **ClamAV Integration**: Optional virus scanning for uploaded files
- **Optimistic Locking**: Version conflict detection for concurrent edits
- **Edit History**: Full audit trail with rollback capability
- **Trust Scoring**: Integration with Auth Service for reputation-based permissions

## Tech Stack

| Component | Technology |
|-----------|------------|
| API | FastAPI + Uvicorn |
| Database | PostgreSQL + SQLAlchemy 2.x (async) |
| Cache/Broker | Redis |
| Background Jobs | Celery |
| Media Storage | AWS S3 |
| Virus Scanning | ClamAV (optional) |
| Container | Docker Compose |

## Quick Start

```bash
# 1. Copy environment template
cp .env.example .env
# Edit .env with your settings (S3 credentials, SERVICE_API_KEY, etc.)

# 2. Start all services
docker compose up --build

# 3. Apply database migrations
docker compose exec app alembic upgrade head

# 4. Run tests (251 tests)
docker compose exec app pytest tests/ -v

# 5. API available at http://localhost:8000/docs
```

## API Endpoints

### Authors (`/authors`)
| Method | Endpoint | Description | Scope |
|--------|----------|-------------|-------|
| GET | `/authors` | List approved authors | Public |
| GET | `/authors/me` | My created authors | Authenticated |
| GET | `/authors/{id}` | Author detail | Public |
| GET | `/authors/{id}/books` | Author's books | Public |
| POST | `/authors` | Create author | `authors:draft` |
| PUT | `/authors/{id}` | Replace author | Owner/Wiki |
| PATCH | `/authors/{id}` | Update author | Owner/Wiki |
| POST | `/authors/{id}/rollback` | Version rollback | Owner/Wiki |
| DELETE | `/authors/{id}/own` | Soft delete own | `authors:delete_own` |
| DELETE | `/authors/{id}` | Takedown | `content:takedown` |
| POST | `/authors/{id}/recover` | Recover deleted | `jury:override` |
| POST | `/authors/{id}/follow` | Follow author | Authenticated |
| DELETE | `/authors/{id}/follow` | Unfollow | Authenticated |

### Books (`/books`)
| Method | Endpoint | Description | Scope |
|--------|----------|-------------|-------|
| GET | `/books` | List with FTS/trigram search | Public |
| GET | `/books/me` | My created books | Authenticated |
| GET | `/books/{id}` | Book detail | Public |
| GET | `/books/{id}/reviews` | Book reviews | Public |
| POST | `/books` | Create book | `books:draft` |
| PUT | `/books/{id}` | Replace book | Owner/Wiki |
| PATCH | `/books/{id}` | Update book | Owner/Wiki |
| POST | `/books/{id}/rollback` | Version rollback | Owner/Wiki |
| DELETE | `/books/{id}/own` | Soft delete own | `books:delete_own` |
| DELETE | `/books/{id}` | Takedown | `content:takedown` |
| POST | `/books/{id}/recover` | Recover deleted | `jury:override` |
| POST | `/books/{id}/approve` | Curator approve | `jury:override` |
| POST | `/books/{id}/reject` | Curator reject | `jury:override` |
| POST | `/books/{id}/reviews` | Create review | Authenticated |
| POST | `/books/{id}/subscribe` | Subscribe | Authenticated |
| DELETE | `/books/{id}/subscribe` | Unsubscribe | Authenticated |

### Collections (`/collections`)
| Method | Endpoint | Description | Scope |
|--------|----------|-------------|-------|
| GET | `/collections` | List collections | Public |
| GET | `/collections/me` | My collections | Authenticated |
| GET | `/collections/{id}` | Collection detail | Public |
| POST | `/collections` | Create collection | `collections:create` |
| PUT | `/collections/{id}` | Update collection | Owner/Wiki |
| POST | `/collections/{id}/rollback` | Version rollback | Owner/Wiki |
| DELETE | `/collections/{id}` | Delete collection | Owner/Curator |
| POST | `/collections/{id}/books` | Add book | Owner/Wiki |
| PATCH | `/collections/{id}/books/{book_id}` | Reorder book | Owner/Wiki |
| DELETE | `/collections/{id}/books/{book_id}` | Remove book | Owner/Wiki |
| POST | `/collections/{id}/approve` | Curator approve | `jury:override` |
| POST | `/collections/{id}/reject` | Curator reject | `jury:override` |

### Jury Voting (`/jury`)
| Method | Endpoint | Description | Scope |
|--------|----------|-------------|-------|
| GET | `/jury/authors` | Pending authors queue | `jury:view` |
| GET | `/jury/authors/{id}` | Pending author detail | `jury:view` |
| POST | `/jury/authors/{id}/vote` | Vote on author | `jury:vote` |
| DELETE | `/jury/authors/{id}/vote` | Retract vote | Authenticated |
| GET | `/jury/books` | Pending books queue | `jury:view` |
| POST | `/jury/books/{id}/vote` | Vote on book | `jury:vote` |
| GET | `/jury/collections` | Pending collections | `jury:view` |
| POST | `/jury/collections/{id}/vote` | Vote on collection | `jury:vote` |

### Media Uploads (`/uploads`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/uploads/books/{id}/cover/presign` | Get presigned URL for book cover |
| POST | `/uploads/books/{id}/cover/commit` | Confirm cover upload → Celery |
| POST | `/uploads/books/{id}/file/presign` | Get presigned URL for book file |
| POST | `/uploads/books/{id}/file/commit` | Confirm file upload → Celery |
| POST | `/uploads/authors/{id}/avatar/presign` | Get presigned URL for avatar |
| POST | `/uploads/authors/{id}/avatar/commit` | Confirm avatar upload → Celery |
| POST | `/uploads/collections/{id}/cover/presign` | Get presigned URL for collection cover |
| POST | `/uploads/collections/{id}/cover/commit` | Confirm cover upload → Celery |

### Health Checks
| Endpoint | Description |
|----------|-------------|
| `GET /health` | Liveness probe |
| `GET /ready` | Readiness probe (DB + Redis) |
| `GET /test` | Frontend test page |

## Media Upload Flow

```
1. Client → POST /uploads/.../presign (get S3 presigned URL + claim token)
2. Client → PUT to S3 presigned URL (upload file directly)
3. Client → POST /uploads/.../commit (validate claim + trigger Celery)
4. Celery → Download from S3, validate, resize, AV scan, save variants
5. Celery → Update DB with new key, invalidate cache
```

**Supported Formats:**
- Images: JPEG, PNG, WebP, AVIF → converted to WebP
- Book Files: PDF, EPUB

**Generated Sizes:**
- Covers: 1800x2700, 1200x1800, 600x900 (2:3 ratio)
- Avatars: 512, 256, 128 (square)

## Testing

```bash
# Run all tests (251 tests)
docker compose exec app pytest tests/ -v

# Specific test suites
docker compose exec app pytest tests/test_upload_endpoints.py -v
docker compose exec app pytest tests/test_media_processing.py -v
docker compose exec app pytest tests/test_jury_voting.py -v
```

## Configuration

See `.env.example` for all configuration options. Key settings:

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_ASYNC_URL` | PostgreSQL connection | Required |
| `SERVICE_API_KEY` | Auth Service shared secret | Required |
| `AWS_ACCESS_KEY_ID` | S3 credentials | Required for media |
| `S3_BUCKET_NAME` | Media bucket | `library-media-demo` |
| `CLAMAV_ENABLED` | Enable virus scanning | `false` |
| `COVER_OUTPUT_FORMAT` | Image output format | `WEBP` |

## License

MIT
