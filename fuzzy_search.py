"""
Fuzzy search module for XDF table/scalar definitions.

Combines multiple matching strategies with weighted scoring:
  1. Exact substring match (highest confidence)
  2. Token-based match (each word must appear somewhere)
  3. Fuzzy ratio match (approximate string similarity via rapidfuzz)
  4. Partial fuzzy match (substring similarity)

Results are ranked by score and returned with match metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Protocol, TypeVar


# ── Rapidfuzz is mandatory ──────────────────────────────────────────────────

from rapidfuzz import fuzz as _fuzz


# ── Data structures ──────────────────────────────────────────────────────────

class HasTitle(Protocol):
    """Minimal structural interface for searchable items."""
    @property
    def title(self) -> str: ...
    @property
    def unique_id(self) -> str: ...


T = TypeVar("T", bound=HasTitle)


@dataclass
class SearchResult:
    """A single search result with score and match info."""
    title: str
    score: float
    match_type: str  # "exact", "token", "fuzzy", "partial"
    unique_id: str = ""
    extra: dict | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "title": self.title,
            "score": round(self.score, 1),
            "match_type": self.match_type,
        }
        if self.unique_id:
            d["unique_id"] = self.unique_id
        if self.extra:
            d.update(self.extra)
        return d


# ── Scoring weights ──────────────────────────────────────────────────────────
# Each strategy contributes up to its weight toward the final score (0-100).

_WEIGHTS = {
    "exact":   40,   # exact case-insensitive substring
    "token":   25,   # all search tokens found in title
    "fuzzy":   25,   # fuzzy ratio (overall similarity)
    "partial": 10,   # partial fuzzy (substring similarity)
}


# ── Public API ───────────────────────────────────────────────────────────────

def search_items(
    query: str,
    items: list[T],
    *,
    threshold: float = 25.0,
    max_results: int = 100,
    extra_fn: Callable[[T], dict] | None = None,
) -> list[SearchResult]:
    """
    Search a list of items using multiple strategies, returning scored results.

    Args:
        query:        The user's search string.
        items:        List of TableDef or ConstantDef objects.
        threshold:    Minimum combined score to include (0-100).
        max_results:  Maximum results to return.
        extra_fn:     Optional callable that returns extra metadata dict for an item.

    Returns:
        List of SearchResult sorted by score descending.
    """
    if not query.strip():
        return []

    query_lower = query.lower().strip()
    tokens = re.findall(r'\w+', query_lower)
    results: list[SearchResult] = []

    for item in items:
        title_lower = item.title.lower()
        score = 0.0
        match_type = "none"

        # Strategy 1: Exact substring match
        if query_lower in title_lower:
            coverage = len(query_lower) / len(title_lower) if title_lower else 0
            score += _WEIGHTS["exact"] * (0.5 + 0.5 * coverage)
            match_type = "exact"

        # Strategy 2: Token-based match (all tokens present somewhere)
        if len(tokens) > 1:
            found = sum(1 for tok in tokens if tok in title_lower)
            if found == len(tokens):
                score += _WEIGHTS["token"] * (found / len(tokens))
                if match_type == "none":
                    match_type = "token"
            elif found > 0:
                score += _WEIGHTS["token"] * 0.3 * (found / len(tokens))

        # Strategy 3: Fuzzy ratio (overall string similarity)
        fr = _fuzz.ratio(query_lower, title_lower)
        if fr > 40:
            score += _WEIGHTS["fuzzy"] * (fr / 100.0)
            if match_type == "none":
                match_type = "fuzzy"

        # Strategy 4: Partial fuzzy (best substring match)
        pr = _fuzz.partial_ratio(query_lower, title_lower)
        if pr > 50:
            score += _WEIGHTS["partial"] * (pr / 100.0)
            if match_type == "none":
                match_type = "partial"

        # Bonus: token-set fuzzy (handles reordering and extra words)
        tsr = _fuzz.token_set_ratio(query_lower, title_lower)
        if tsr > 70:
            score += 5 * (tsr / 100.0)

        if score >= threshold:
            extra = extra_fn(item) if extra_fn else {}
            results.append(SearchResult(
                title=item.title,
                score=round(score, 2),
                match_type=match_type,
                unique_id=item.unique_id,
                extra=extra or None,
            ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:max_results]


def fuzzy_find(
    query: str,
    items: list[T],
    *,
    best_only: bool = True,
) -> T | None:
    """
    Find the best matching item for a specific lookup (e.g., read_table).

    When best_only=True, returns the single best match or None.
    """
    results = search_items(query, items, threshold=15, max_results=5)
    if not results:
        return None

    # Build title -> item mapping
    title_map = {item.title: item for item in items}
    return title_map.get(results[0].title)
