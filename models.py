"""
SQLAlchemy models for Library Service.
Complete schema redesign with UUID users, workflow, versioning, and social features.
"""

from datetime import datetime, timezone
from enum import Enum as PyEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Float,
    String,
    Table,
    Text,
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


# ========================================
# Enums
# ========================================


class ContentStatus(str, PyEnum):
    """Content workflow status."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class UploadStatus(str, PyEnum):
    """Upload lifecycle status."""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    REVOKED = "REVOKED"


class VoteType(str, PyEnum):
    """Review vote types."""

    HELPFUL = "HELPFUL"
    UNHELPFUL = "UNHELPFUL"


class EditAction(str, PyEnum):
    """Edit history action types."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    RECOVER = "RECOVER"


# ========================================
# Association Tables
# ========================================

author_book_relation = Table(
    "author_book_relation",
    Base.metadata,
    Column("author_id", ForeignKey("authors.id", ondelete="CASCADE"), primary_key=True),
    Column("book_id", ForeignKey("books.id", ondelete="CASCADE"), primary_key=True),
)


# ========================================
# Core Content Models
# ========================================


class Author(Base):
    """
    Author model with workflow and social features.
    Supports wiki-style submissions with approval workflow.
    """

    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    bio: Mapped[str | None] = mapped_column(Text)
    avatar_key: Mapped[str | None] = mapped_column(String(255))

    # Ownership & Creation
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    linked_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )

    # Workflow
    status: Mapped[ContentStatus] = mapped_column(
        Enum(ContentStatus, name="content_status", native_enum=True),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    is_public: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    is_deleted: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Versioning (Optimistic Locking)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    last_edited_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    last_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Social, also can used in statistics
    follower_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # Jury Voting (Democratic Approval)
    vote_score: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=datetime.now(timezone.utc),
    )

    # Relationships
    books: Mapped[list["Book"]] = relationship(
        secondary="author_book_relation",
        back_populates="authors",
    )
    followers: Mapped[list["AuthorFollow"]] = relationship(
        "AuthorFollow",
        back_populates="author",
        cascade="all, delete-orphan",
    )

    # TODO, add versioning constraints
    __table_args__ = (
        CheckConstraint(
            "email IS NULL OR email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$'",
            name="ck_authors_email_format",
        ),
        CheckConstraint(
            "follower_count >= 0", name="ck_authors_follower_count_positive"
        ),
        CheckConstraint(
            "vote_score >= 0 AND vote_score <= 5", name="ck_authors_vote_score_range"
        ),
        CheckConstraint("trim(name) != ''", name="ck_authors_name_not_empty"),
        Index("ix_authors_created_by", "created_by_user_id"),
        Index("ix_authors_linked_user", "linked_user_id"),
        Index("ix_authors_status_public", "status", "is_public"),
        Index("ix_authors_deleted", "is_deleted", "deleted_at"),
        Index(
            "idx_authors_name_trgm",
            text("immutable_unaccent(name::text)"),
            postgresql_ops={"immutable_unaccent(name::text)": "gin_trgm_ops"},
            postgresql_using="gin",
        ),
        Index(
            "idx_authors_email_trgm",
            text("immutable_unaccent(email::text)"),
            postgresql_ops={"immutable_unaccent(email::text)": "gin_trgm_ops"},
            postgresql_using="gin",
        ),
    )


class Book(Base):
    """
    Book model with workflow, versioning, and full-text search.
    Supports media uploads and subscription tracking.
    """

    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str] | None] = mapped_column(
        ARRAY(String)
    )  # Flat tag list: ['fantasy', 'classic']

    # Media
    cover_key: Mapped[str | None] = mapped_column(String(255))
    file_key: Mapped[str | None] = mapped_column(String(255))
    file_format: Mapped[str | None] = mapped_column(String(20))  # pdf/epub/mobi

    # Ownership
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )

    # Workflow
    status: Mapped[ContentStatus] = mapped_column(
        Enum(ContentStatus, name="content_status", native_enum=True),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    is_public: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    is_deleted: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Versioning
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    last_edited_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    last_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Social, also can be used in statistics
    subscriber_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # Jury Voting (Democratic Approval)
    vote_score: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=datetime.now(timezone.utc),
    )

    # Statistics
    average_rating: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0.0")
    )
    view_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    trending_score: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0.0")
    )

    # Full-Text Search (computed column)
    search_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(description, '')), 'C')",
            persisted=True,
        ),
    )

    # Relationships
    authors: Mapped[list["Author"]] = relationship(
        secondary="author_book_relation",
        back_populates="books",
    )
    reviews: Mapped[list["Review"]] = relationship(
        "Review",
        back_populates="book",
        cascade="all, delete-orphan",
    )
    subscribers: Mapped[list["BookSubscription"]] = relationship(
        "BookSubscription",
        back_populates="book",
        cascade="all, delete-orphan",
    )
    collections: Mapped[list["CollectionBook"]] = relationship(
        "CollectionBook",
        back_populates="book",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("year IS NULL OR year > 0", name="ck_books_year_positive"),
        CheckConstraint(
            "file_format IS NULL OR file_format IN ('pdf', 'epub', 'mobi')",
            name="ck_books_file_format",
        ),
        CheckConstraint(
            "subscriber_count >= 0", name="ck_books_subscriber_count_positive"
        ),
        CheckConstraint(
            "vote_score >= 0 AND vote_score <= 5", name="ck_books_vote_score_range"
        ),
        CheckConstraint("trim(title) != ''", name="ck_books_title_not_empty"),
        CheckConstraint(
            "(file_key IS NULL AND file_format IS NULL) OR "
            "(file_key IS NOT NULL AND file_format IS NOT NULL)",
            name="ck_books_file_consistency",
        ),
        Index("ix_books_created_by", "created_by_user_id"),
        Index("ix_books_status_public", "status", "is_public"),
        Index("ix_books_deleted", "is_deleted", "deleted_at"),
        Index("ix_books_file_format", "file_format"),
        Index("ix_books_tags_gin", "tags", postgresql_using="gin"),
        Index("ix_books_search_tsv", "search_tsv", postgresql_using="gin"),
        Index(
            "idx_books_title_trgm",
            "title",
            postgresql_ops={"title": "gin_trgm_ops"},
            postgresql_using="gin",
        ),
        Index(
            "ix_books_view_count",
            "view_count",
            postgresql_where="view_count > 0 and is_public = true and is_deleted = false",
        ),
        Index(
            "ix_books_trending_score",
            "trending_score",
            postgresql_where="trending_score > 0 and is_public = true and is_deleted = false",
        ),
        Index(
            "ix_books_average_rating",
            "average_rating",
            postgresql_where="average_rating > 0 and is_public = true and is_deleted = false",
        ),
        Index(
            "ix_books_subscriber_count",
            "subscriber_count",
            postgresql_where="subscriber_count > 0 and is_public = true and is_deleted = false",
        ),
    )


class Review(Base):
    """
    Book review with voting and trust rewards.
    Changed from reviewer_name to user_id for auth integration.
    """

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)

    # Voting & Trust
    helpful_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    unhelpful_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    trust_awarded: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # Soft Delete
    is_deleted: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=datetime.now(timezone.utc),
    )

    # Relationships
    book: Mapped["Book"] = relationship("Book", back_populates="reviews")
    votes: Mapped[list["ReviewVote"]] = relationship(
        "ReviewVote",
        back_populates="review",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("rating > 0 AND rating <= 5", name="ck_reviews_rating_1_5"),
        CheckConstraint("helpful_count >= 0", name="ck_reviews_helpful_count_positive"),
        CheckConstraint(
            "unhelpful_count >= 0", name="ck_reviews_unhelpful_count_positive"
        ),
        CheckConstraint(
            "trust_awarded >= -5 AND trust_awarded <= 5",
            name="ck_reviews_trust_awarded_range",
        ),
        CheckConstraint(
            "comment IS NULL OR trim(comment) != ''",
            name="ck_reviews_comment_not_empty",
        ),
        UniqueConstraint("book_id", "user_id", name="uq_reviews_book_user"),
        Index("ix_reviews_user_id", "user_id"),
        Index("ix_reviews_deleted", "is_deleted", "deleted_at"),
        Index("ix_reviews_book_helpful", "book_id", "helpful_count"),
        Index("ix_reviews_book_rating", "book_id", "rating"),
    )


class Collection(Base):
    """
    Curated collection of books with workflow.
    Wiki-style submissions requiring approval.
    """

    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    cover_key: Mapped[str | None] = mapped_column(String(255))

    # Full-text search vector (computed from name + description)
    search_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(name, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(description, '')), 'C')",
            persisted=True,
        ),
    )

    # Ownership
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )

    # Workflow
    status: Mapped[ContentStatus] = mapped_column(
        Enum(ContentStatus, name="content_status", native_enum=True),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    is_public: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    is_deleted: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Versioning
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    last_edited_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    last_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Social & Statistics
    subscriber_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    book_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    view_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    trending_score: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0.0")
    )

    # Jury Voting (Democratic Approval)
    vote_score: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=datetime.now(timezone.utc),
    )

    # Relationships
    books: Mapped[list["CollectionBook"]] = relationship(
        "CollectionBook",
        back_populates="collection",
        cascade="all, delete-orphan",
        order_by="CollectionBook.position",
    )
    subscribers: Mapped[list["CollectionSubscription"]] = relationship(
        "CollectionSubscription",
        back_populates="collection",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "subscriber_count >= 0", name="ck_collections_subscriber_count_positive"
        ),
        CheckConstraint(
            "book_count >= 0 AND book_count <= 100",
            name="ck_collections_book_count_limit",
        ),
        CheckConstraint(
            "vote_score >= 0 AND vote_score <= 5",
            name="ck_collections_vote_score_range",
        ),
        CheckConstraint("trim(name) != ''", name="ck_collections_name_not_empty"),
        Index("ix_collections_created_by", "created_by_user_id"),
        Index("ix_collections_status_public", "status", "is_public"),
        Index("ix_collections_deleted", "is_deleted", "deleted_at"),
        # FTS index
        Index("ix_collections_search_tsv", "search_tsv", postgresql_using="gin"),
        # Trigram index for name similarity
        Index(
            "ix_collections_name_trgm",
            text("immutable_unaccent(name)"),
            postgresql_ops={"immutable_unaccent(name)": "gin_trgm_ops"},
            postgresql_using="gin",
        ),
        # Statistics indexes
        Index(
            "ix_collections_subscriber_count",
            "subscriber_count",
            postgresql_where="subscriber_count > 0 and is_public = true and is_deleted = false",
        ),
        Index(
            "ix_collections_view_count",
            "view_count",
            postgresql_where="view_count > 0 and is_public = true and is_deleted = false",
        ),
        Index(
            "ix_collections_trending_score",
            "trending_score",
            postgresql_where="trending_score > 0 and is_public = true and is_deleted = false",
        ),
    )


class CollectionBook(Base):
    """
    Association table for collections and books with ordering.
    """

    __tablename__ = "collection_books"

    collection_id: Mapped[int] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"),
        primary_key=True,
    )
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # Timestamps
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    # Relationships
    collection: Mapped["Collection"] = relationship(
        "Collection", back_populates="books"
    )
    book: Mapped["Book"] = relationship("Book", back_populates="collections")

    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_collection_books_position_positive"),
        UniqueConstraint(
            "collection_id", "position", name="uq_collection_books_position"
        ),
        Index("ix_collection_books_position", "collection_id", "position"),
        Index("ix_collection_books_book", "book_id"),
    )


class PendingUpload(Base):
    """
    Track pending media uploads (covers, avatars, PDFs).
    Updated with UUID user IDs and entity tracking.
    """

    __tablename__ = "pending_uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(500), nullable=False)

    # Upload Context
    upload_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # cover/avatar/file
    entity_type: Mapped[str | None] = mapped_column(
        String(50)
    )  # author/book/collection
    entity_id: Mapped[int | None] = mapped_column(Integer)

    # Status
    status: Mapped[UploadStatus] = mapped_column(
        Enum(UploadStatus, name="upload_status", native_enum=True),
        nullable=False,
        server_default=text("'PENDING'"),
    )

    # Expiry
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP + INTERVAL '10 minutes'"),
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        CheckConstraint(
            "upload_type IN ('cover', 'avatar', 'file')",
            name="ck_pending_uploads_upload_type",
        ),
        CheckConstraint(
            "entity_type IS NULL OR entity_type IN ('author', 'book', 'collection')",
            name="ck_pending_uploads_entity_type",
        ),
        CheckConstraint(
            "(entity_type IS NULL AND entity_id IS NULL) OR "
            "(entity_type IS NOT NULL AND entity_id IS NOT NULL)",
            name="ck_pending_uploads_entity_consistency",
        ),
        Index("ix_pending_uploads_user_status", "user_id", "status"),
        Index("ix_pending_uploads_expires_at", "expires_at"),
        Index("ix_pending_uploads_status_expires", "status", "expires_at"),
    )


# ========================================
# Edit History
# ========================================

# NOTE: EditHistory stores JSONB snapshots that may reference deleted entities
# (e.g., book_ids of deleted books, linked_user_id of deleted users).
# This is expected behavior for audit trail - historical records should not be modified.
# Rollback operations handle missing references gracefully with warnings.


class EditHistory(Base):
    """
    Complete edit history for all content with version tracking.
    Stores JSON diffs for auditing and rollback.
    """

    __tablename__ = "edit_history"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[EditAction] = mapped_column(
        Enum(EditAction, name="edit_action", native_enum=True),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)

    # Version Tracking
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version: Mapped[int | None] = mapped_column(Integer)

    # Change Data (JSON)
    old_data: Mapped[dict | None] = mapped_column(JSONB)
    new_data: Mapped[dict | None] = mapped_column(JSONB)
    changes: Mapped[dict | None] = mapped_column(JSONB)  # Diff summary

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
    )

    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('author', 'book', 'review', 'collection')",
            name="ck_edit_history_entity_type",
        ),
        Index(
            "ix_edit_history_entity",
            "entity_type",
            "entity_id",
            desc("created_at"),
        ),
        Index("ix_edit_history_user", "user_id", "created_at"),
        Index(
            "ix_edit_history_entity_version",
            "entity_type",
            "entity_id",
            "version",
            unique=True,
        ),
    )


# ========================================
# Social Features
# ========================================


class AuthorFollow(Base):
    """User following an author."""

    __tablename__ = "author_follows"

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    author_id: Mapped[int] = mapped_column(
        ForeignKey("authors.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    # Relationships
    author: Mapped["Author"] = relationship("Author", back_populates="followers")

    __table_args__ = (Index("ix_author_follows_author", "author_id", "created_at"),)


class BookSubscription(Base):
    """User subscribed to book updates."""

    __tablename__ = "book_subscriptions"

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    # Relationships
    book: Mapped["Book"] = relationship("Book", back_populates="subscribers")

    __table_args__ = (Index("ix_book_subscriptions_book", "book_id", "created_at"),)


class CollectionSubscription(Base):
    """User subscribed to collection updates."""

    __tablename__ = "collection_subscriptions"

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    # Relationships
    collection: Mapped["Collection"] = relationship(
        "Collection", back_populates="subscribers"
    )

    __table_args__ = (
        Index("ix_collection_subscriptions_collection", "collection_id", "created_at"),
    )


class ReviewVote(Base):
    """User voting on review helpfulness."""

    __tablename__ = "review_votes"

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    review_id: Mapped[int] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"),
        primary_key=True,
    )
    vote: Mapped[VoteType] = mapped_column(
        Enum(VoteType, name="vote_type", native_enum=True),
        nullable=False,
    )

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    # Relationships
    review: Mapped["Review"] = relationship("Review", back_populates="votes")

    __table_args__ = (Index("ix_review_votes_review", "review_id", "created_at"),)


class JuryVote(Base):
    """
    Democratic voting on pending content (authors, books, collections).

    Jury members can vote on PENDING content:
    - Contributors with jury:vote: +1 vote
    - Trusted users with jury:vote_weighted: +5 votes

    When vote_score >= 5, content is auto-published to APPROVED.
    """

    __tablename__ = "jury_votes"

    # Composite Primary Key: one vote per user per entity
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    entity_type: Mapped[str] = mapped_column(
        String(50), primary_key=True
    )  # 'author', 'book', 'collection'
    entity_id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Vote value (+1 for contributor, +5 for trusted)
    vote_value: Mapped[int] = mapped_column(Integer, nullable=False)

    # When vote was cast
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        CheckConstraint("vote_value IN (1, 5)", name="ck_jury_votes_valid_values"),
        CheckConstraint(
            "entity_type IN ('author', 'book', 'collection')",
            name="ck_jury_votes_valid_entity_type",
        ),
        Index("ix_jury_votes_entity", "entity_type", "entity_id"),
        Index("ix_jury_votes_user", "user_id", "created_at"),
    )
