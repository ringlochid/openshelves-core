"""Pagination and sorting dependencies for FastAPI."""
from fastapi import Query
from typing import List


def parse_sort(
    sort: List[str] = Query(
        default=[], description="Sort spec like 'similarity:desc', 'title:asc'"
    )
) -> List[str]:
    """
    Parse sort query parameters.
    
    Returns list of sort strings to be processed by routers.
    Validation happens at router level with enum checks.
    """
    return sort


__all__ = ["parse_sort"]