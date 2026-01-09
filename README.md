# OpenShelves (formerly Library Service)

> 📘 **Technical Report**: [View Full Architecture & Design](docs/technical-report.md)

wiki-style content platform with RBAC, trust scoring, jury-based governance, and full media upload pipeline.

## 🚀 Live Demo & Testing

**Public Test Environment**: [TestLink](https://8kgscauecu.ap-southeast-2.awsapprunner.com/test)

> 📧 **Bug Reports & Suggestions**: Please email [admin@ringlochid.me](mailto:admin@ringlochid.me)

This interactive test console allows you to explore the full feature set of OpenShelves without setting up a local environment.

### Testable Features
*   **Authentication**: Register, Login, Refresh Tokens, and Session Management.
*   **Content Management**: Create, Edit, and Delete Authors, Books, and Collections.
*   **Media Pipeline**: Upload Avatars and Covers using the S3 Presigned URL workflow.
*   **Governance**: Participate in the Jury System to vote on pending content.
*   **Search**: Full-text search across all content types.

### Public Testing Workflow
1.  **Register a User**: Go to the **Auth** tab and create a new account.
2.  **Create Content**:
    *   Navigate to **Authors** or **Books**, select the **CRUD** tab, and create a draft.
    *   Note: New content starts in `pending` status.
3.  **Upload Media**:
    *   Go to **Media Upload**.
    *   Use the ID of your created content to upload a cover or avatar.
    *   The system will automatically process (resize/scan) the image in the background.
4.  **Jury Voting**:
    *   Go to **Jury Queue**.
    *   You (or another user) can view the pending content and cast an "Approve" vote.
    *   Once approved, the content becomes visible in the public **Browse** lists.

---

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
| **API** | FastAPI + Uvicorn |
| **Database** | PostgreSQL + SQLAlchemy 2.x (async) |
| **Cache/Broker** | Redis |
| **Background Jobs** | Celery (Redis Broker/Backend) |
| **Media Storage** | AWS S3 |
| **Virus Scanning** | ClamAV (optional) |
| **Container** | Docker Compose |

## Quick Start (Local Development)

```bash
# 1. Copy environment template
cp .env.example .env
# Edit .env with your settings (S3 credentials, SERVICE_API_KEY, etc.)

# 2. Start all services
docker compose up --build

# 3. Apply database migrations
docker compose exec app alembic upgrade head

# 4. Run tests
docker compose exec app pytest tests/ -v

# 5. Access documentation
# API Docs: http://localhost:8000/docs
# Frontend Tester: http://localhost:8000/test
```

## API Endpoints Overview

### Core Resources
*   **Authors** (`/authors`): Profiles, bibliography, following.
*   **Books** (`/books`): Details, reviews, subscribing.
*   **Collections** (`/collections`): User-curated book lists.

### Governance
*   **Jury** (`/jury`): Voting queues for content approval.
*   **Uploads** (`/uploads`): Two-step upload process (Presign → Commit).

### System
*   **Health** (`/health`, `/ready`): Liveness and readiness probes.

## Configuration

See `.env.example` for all configuration options. Key settings:

| Variable | Description |
|----------|-------------|
| `DATABASE_ASYNC_URL` | PostgreSQL connection string |
| `SERVICE_API_KEY` | Shared secret for Auth Service communication |
| `AWS_ACCESS_KEY_ID` | S3 credentials for media storage |
| `S3_BUCKET_NAME` | Target S3 bucket name |
| `CLAMAV_ENABLED` | Toggle for virus scanning (`true`/`false`) |

## License

MIT
