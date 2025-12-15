from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, asc, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from models import Author, Book, Review
from database import get_async_db
from dependencies import parse_sort
from schemas.book import (
    BookCreate,
    BookDetailRead,
    BookListRead,
    BookUpdate,
    BookSortControl,
    SortField,
    SortDirection,
    PaginatedBooks,
)
from schemas.review import ReviewCreate, ReviewRead
from helpers.cursor import encode_cursor, decode_cursor
from cache import (
    Redis,
    bump_cache_version,
    cache_book,
    cache_list,
    cache_list_with_params,
    get_book,
    get_list,
    get_list_with_params,
    get_redis,
    invalidate_author,
    invalidate_book,
    make_books_list_key,
    make_reviews_key,
)

router = APIRouter(prefix="/books", tags=["books"])


@router.get("/", response_model=PaginatedBooks)
async def get_books_router(
    q: str | None = Query(None, description="Full-text query"),
    title: str | None = Query(None, description="Exact title filter"),
    isbn: str | None = Query(None, description="Exact ISBN filter"),
    author_id: int | None = Query(None, description="Filter by author id"),
    before: int | None = Query(None, description="Filter by Year(before)"),
    after: int | None = Query(None, description="Filter by Year(after)"),
    limit: int = Query(20, ge=1, le=100, description="Pagination limit"),
    offset: int | None = Query(None, description="Work when no cursor"),
    cursor: str | None = Query(None, description="Pagination cursor"),
    sort: List[BookSortControl] = Depends(parse_sort),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(get_redis),
):
    if q and not sort:
        sort = [
            BookSortControl(
                sort_field=SortField.by_similarity, sort_direction=SortDirection.desc
            )
        ]

    sort_param = [s.model_dump() for s in sort]
    params = {
        "q": q,
        "title": title,
        "isbn": isbn,
        "author_id": author_id,
        "before": before,
        "after": after,
        "limit": limit,
        "offset": offset,
        "cursor": cursor,
        "sort": sort_param,
    }
    key, payload = await make_books_list_key(params, r=r)
    cache = await get_list_with_params(key, payload, r)
    if cache is not None:
        return cache
    stmt = select(Book).options(selectinload(Book.authors))
    total = None

    if title:
        stmt = stmt.where(Book.title == title)
    if isbn:
        stmt = stmt.where(Book.book_isbn == isbn)
    if author_id:
        stmt = stmt.where(Book.authors.any(Author.id == author_id))
    if before:
        stmt = stmt.where(Book.year <= before)
    if after:
        stmt = stmt.where(Book.year >= after)

    if q:
        tsq = func.websearch_to_tsquery("english", q)
        fts_score = func.ts_rank(Book.search_tsv, tsq)
        title_sim = func.similarity(Book.title, q)
        author_sim = func.coalesce(func.max(func.similarity(Author.name, q)), 0.0)
        total = 0.6 * fts_score + 0.25 * title_sim + 0.15 * author_sim
        stmt = (
            stmt.join(Book.authors, isouter=True)
            .where(
                or_(
                    Book.search_tsv.op("@@")(tsq),
                    Book.title.op("%")(q),
                    Author.name.op("%")(q),
                )
            )
            .group_by(Book.id)
            .add_columns(
                fts_score.label("fts_score"),
                title_sim.label("title_sim"),
                author_sim.label("author_sim"),
                total.label("total_score"),
            )
        )

    order_exprs: list = []

    for s in sort:
        if s.sort_field is SortField.by_similarity:
            if not q:
                raise HTTPException(
                    status_code=400,
                    detail="by_similarity only works with q",
                )
            col = total
        elif s.sort_field is SortField.by_title:
            col = Book.title
        elif s.sort_field is SortField.by_year:
            col = Book.year
        else:
            continue

        order_exprs.append(
            asc(col) if s.sort_direction is SortDirection.asc else desc(col)
        )

    # if q and not order_exprs:
    #     order_exprs.append(desc(total))

    order_exprs.append(Book.id.asc())
    stmt = stmt.order_by(*order_exprs)

    primary = sort[0] if sort else None
    by_similarity = primary and primary.sort_field is SortField.by_similarity

    if cursor is not None and offset is not None:
        raise HTTPException(
            status_code=400,
            detail="Cannot use both cursor and offset",
        )

    # cursor keyset for similarity sort
    if cursor and by_similarity:
        data = decode_cursor(cursor)
        last_score = data["score"]
        last_id = data["id"]

        # use HAVING because total uses aggregated author similarity
        stmt = stmt.having(
            or_(
                total < last_score,
                and_(total == last_score, Book.id > last_id),  # handle edge cases
            )
        )
    elif cursor and not by_similarity:
        raise HTTPException(
            status_code=400,
            detail="cursor pagination is only supported for by_similarity sort",
        )

    # offset when no cursor
    if offset is not None and cursor is None:
        stmt = stmt.offset(offset)

    # check if there's a next page
    stmt = stmt.limit(limit + 1)
    result = await db.execute(stmt)
    rows = result.unique().all()

    items_rows = rows[:limit]
    has_next = len(rows) > limit

    books = [r[0] for r in items_rows]

    next_cursor = None
    if has_next and by_similarity:
        last_row = items_rows[-1]
        last_book: Book = last_row[0]
        last_total_score = last_row[-1]  # total_score is last selected column

        next_cursor = encode_cursor(
            {"id": last_book.id, "score": float(last_total_score)}
        )
    serialized = PaginatedBooks(
        items=books,  # type: ignore
        next_cursor=next_cursor,
    ).model_dump()
    await cache_list_with_params(key, serialized, payload, r)
    return serialized


@router.get("/{book_id}", response_model=BookDetailRead)
async def get_book_router(
    book_id: int,
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(get_redis),
):
    cached = await get_book(book_id, r)
    if cached is not None:
        return cached

    stmt = (
        select(Book)
        .options(selectinload(Book.authors))
        .options(selectinload(Book.reviews))
        .where(Book.id == book_id)
    )
    book = (await db.execute(stmt)).scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    payload = BookDetailRead.model_validate(book, from_attributes=True).model_dump()
    await cache_book(book_id, payload, r=r)
    return payload


@router.get("/{book_id}/reviews", response_model=List[ReviewRead])
async def get_reviews(
    book_id: int,
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(get_redis),
):
    key = make_reviews_key(book_id)
    cached = await get_list(key, r)
    if cached is not None:
        return cached
    stat = select(Book).options(selectinload(Book.reviews)).where(Book.id == book_id)
    book = (await db.execute(stat)).scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    payload = [
        ReviewRead.model_validate(review, from_attributes=True).model_dump()
        for review in book.reviews
    ]
    await cache_list(key, payload, r)
    return payload


@router.post("/", response_model=BookDetailRead)
async def create_book(
    book: BookCreate,
    db: AsyncSession = Depends(get_async_db),
    r: Redis | None = Depends(get_redis),
):
    author_objs: list[Author] = []
    new_book = Book(
        title=book.title,
        year=book.year,
        book_isbn=book.book_isbn,
        genre_name=book.genre_name,
        description=book.description,
    )

    if book.author_ids:
        author_ids = set(book.author_ids)
        stmt_authors = select(Author).where(Author.id.in_(author_ids))
        author_objs = (await db.execute(stmt_authors)).scalars().all()

        if len(author_objs) != len(author_ids):
            raise HTTPException(
                status_code=400, detail="At least one author id does not exist."
            )

        new_book.authors = list(author_objs)
    else:
        new_book.authors = []

    db.add(new_book)
    await db.commit()
    await db.refresh(new_book)
    stmt_reload = (
        select(Book)
        .options(selectinload(Book.authors))
        .options(selectinload(Book.reviews))
        .where(Book.id == new_book.id)
    )
    new_book = (await db.execute(stmt_reload)).scalar_one()
    await bump_cache_version("books:list", r)
    for author in author_objs:
        await invalidate_author(author.id, r, book_ids=[new_book.id])
    return new_book


@router.post("/{book_id}/reviews", response_model=ReviewRead)
async def create_review(
    book_id: int,
    review: ReviewCreate,
    db: AsyncSession = Depends(get_async_db),
    r: Redis | None = Depends(get_redis),
):
    stat_book = (
        select(Book)
        .options(selectinload(Book.authors))
        .options(selectinload(Book.reviews))
        .where(Book.id == book_id)
    )
    book = (await db.execute(stat_book)).scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    dup_check = select(Review).where(
        Review.book_id == book_id, Review.reviewer_name == review.reviewer_name
    )
    if (await db.execute(dup_check)).scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail="Reviewer has already reviewed this book."
        )
    new_review = Review(
        book_id=book_id,
        reviewer_name=review.reviewer_name,
        rating=review.rating,
        comment=review.comment,
    )
    author_ids = [author.id for author in book.authors]
    db.add(new_review)
    await db.commit()
    await db.refresh(new_review)
    for aid in author_ids:
        await invalidate_author(aid, r, book_ids=[book_id])
    await invalidate_book(book_id, r)
    return new_review


@router.put("/{book_id}", response_model=BookDetailRead)
async def replace_book(
    book_id: int,
    new_book: BookCreate,
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(get_redis),
):
    stat = (
        select(Book)
        .options(selectinload(Book.authors))
        .options(selectinload(Book.reviews))
        .where(Book.id == book_id)
    )
    old_book = (await db.execute(stat)).scalar_one_or_none()
    if not old_book:
        raise HTTPException(status_code=404, detail="Book not found")

    previous_author_ids = {a.id for a in old_book.authors}

    if new_book.author_ids:
        author_ids = set(new_book.author_ids)
        stmt_authors = select(Author).where(Author.id.in_(author_ids))
        authors = (await db.execute(stmt_authors)).scalars().all()

        if len(authors) != len(author_ids):
            raise HTTPException(
                status_code=400, detail="At least one author id does not exist."
            )

        old_book.authors = list(authors)
        updated_author_ids = author_ids
    else:
        old_book.authors = []
        updated_author_ids = set()

    old_book.title = new_book.title
    old_book.year = new_book.year
    old_book.book_isbn = new_book.book_isbn
    old_book.genre_name = new_book.genre_name
    old_book.description = new_book.description
    # old_book.reviews = []
    affected_author_ids = previous_author_ids | updated_author_ids
    await db.commit()
    await db.refresh(old_book)
    for aid in affected_author_ids:
        await invalidate_author(aid, r, book_ids=[book_id])
    await invalidate_book(book_id, r)
    await bump_cache_version("books:list", r)
    return old_book


@router.put("/{book_id}/authors", response_model=BookDetailRead)
async def update_author_list(
    book_id: int,
    new_author_list: list[int],
    db: AsyncSession = Depends(get_async_db),
    r: Redis | None = Depends(get_redis),
):
    stat_book = (
        select(Book)
        .options(selectinload(Book.authors))
        .options(selectinload(Book.reviews))
        .where(Book.id == book_id)
    )
    book = (await db.execute(stat_book)).scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Cant find the book")
    previous_author_ids = {a.id for a in book.authors}
    if not new_author_list:
        author_ids: set[int] = set()
        book.authors = []
    else:
        author_ids = set(new_author_list)
        stmt_authors = select(Author).where(Author.id.in_(author_ids))
        authors = (await db.execute(stmt_authors)).scalars().all()

        if len(author_ids) != len(authors):
            raise HTTPException(
                status_code=400, detail="At least one author id does not exist."
            )

        book.authors = list(authors)
    affected_author_ids = previous_author_ids | author_ids
    await db.commit()
    await db.refresh(book)
    for aid in affected_author_ids:
        await invalidate_author(aid, r, book_ids=[book_id])
    await invalidate_book(book_id, r)
    await bump_cache_version("books:list", r)
    return book


@router.patch("/{book_id}", response_model=BookDetailRead)
async def update_book(
    book_id: int,
    new_book: BookUpdate,
    db: AsyncSession = Depends(get_async_db),
    r: Redis | None = Depends(get_redis),
):
    stat = (
        select(Book)
        .options(selectinload(Book.authors))
        .options(selectinload(Book.reviews))
        .where(Book.id == book_id)
    )
    old_book = (await db.execute(stat)).scalar_one_or_none()

    if not old_book:
        raise HTTPException(status_code=404, detail="Book not found")

    update_data = new_book.model_dump(exclude_unset=True)
    for key, val in update_data.items():
        setattr(old_book, key, val)

    await db.commit()
    await db.refresh(old_book)
    await invalidate_book(book_id, r)
    await bump_cache_version("books:list", r)
    return old_book


@router.delete("/{book_id}", status_code=204)
async def delete_book(
    book_id: int,
    db: AsyncSession = Depends(get_async_db),
    r: Redis | None = Depends(get_redis),
):
    stat = select(Book).options(selectinload(Book.authors)).where(Book.id == book_id)

    old_book = (await db.execute(stat)).scalar_one_or_none()

    if not old_book:
        raise HTTPException(status_code=404, detail="Book not found")

    author_ids = [author.id for author in old_book.authors]
    await db.delete(old_book)
    await db.commit()
    for aid in author_ids:
        await invalidate_author(aid, r, book_ids=[book_id])
    await invalidate_book(book_id, r)
    await bump_cache_version("books:list", r)
