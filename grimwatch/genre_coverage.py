"""Deterministic genre coverage gap analysis.

No AI calls. Computes a weighted gap score per genre so that large under-covered
shelves rank above tiny ones with the same raw coverage ratio. The output feeds
both the stats display and the recommendation engine's candidate weighting.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Literal, Sequence

from .parser import GenreBlock, Movie


# Tier thresholds calibrated for a 200-600 film archive. A genre with only
# 1-2 entries cannot produce a meaningful gap regardless of coverage.
_CRITICAL_SCORE = 2.0
_SIGNIFICANT_SCORE = 0.8
_MIN_TOTAL_CRITICAL = 5
_MIN_TOTAL_SIGNIFICANT = 3
_MIN_TOTAL_ANY = 2


@dataclass(frozen=True)
class CoverageGap:
    """Gap analysis result for one genre shelf.

    gap_score uses log-weighting so large shelves with low coverage dominate
    over small shelves with the same raw ratio. A genre with total=1 produces
    gap_score=0.0 regardless of watched count.
    """

    genre: str
    watched: int
    total: int
    coverage_ratio: float
    expected_ratio: float
    gap_score: float
    tier: Literal["critical", "significant", "minor"]

    @property
    def display_pct(self) -> int:
        return round(self.coverage_ratio * 100)


def compute_coverage_gaps(genres: Sequence[GenreBlock]) -> list[CoverageGap]:
    """Return coverage gaps sorted by gap_score descending.

    Genres with fewer than 2 entries are excluded — too small to draw
    conclusions about. The archive-wide watched/total ratio is the baseline;
    any genre below that baseline has a positive gap score.
    """
    eligible = [g for g in genres if g.total >= _MIN_TOTAL_ANY]
    if not eligible:
        return []

    archive_total = sum(g.total for g in eligible)
    archive_watched = sum(g.watched_count for g in eligible)
    expected_ratio = archive_watched / archive_total if archive_total else 0.0

    gaps: list[CoverageGap] = []
    for block in eligible:
        coverage_ratio = block.watched_count / block.total
        raw_gap = max(0.0, expected_ratio - coverage_ratio)
        gap_score = raw_gap * math.log(block.total + 1)

        if gap_score > _CRITICAL_SCORE and block.total >= _MIN_TOTAL_CRITICAL:
            tier: Literal["critical", "significant", "minor"] = "critical"
        elif gap_score > _SIGNIFICANT_SCORE and block.total >= _MIN_TOTAL_SIGNIFICANT:
            tier = "significant"
        else:
            tier = "minor"

        gaps.append(CoverageGap(
            genre=block.header,
            watched=block.watched_count,
            total=block.total,
            coverage_ratio=coverage_ratio,
            expected_ratio=expected_ratio,
            gap_score=gap_score,
            tier=tier,
        ))

    return sorted(gaps, key=lambda g: g.gap_score, reverse=True)


def weighted_sample_by_gap(
    movies: Sequence[Movie],
    gaps: Sequence[CoverageGap],
    count: int,
    seed: str = "",
) -> list[Movie]:
    """Sample movies weighted by their genre's gap score.

    Movies from under-covered genres are over-represented in the output.
    Uses reservoir sampling so the result is reproducible given the same seed.

    Weight per movie: 1 + (gap_score * 2.5) for its genre's gap, or 1.0
    for genres not in the gap list (genres at or above expected coverage).
    """
    if not movies:
        return []

    gap_by_genre = {g.genre.casefold(): g for g in gaps}
    weights = [
        1.0 + gap_by_genre[m.genre.casefold()].gap_score * 2.5
        if m.genre.casefold() in gap_by_genre
        else 1.0
        for m in movies
    ]

    rng = random.Random(seed or None)
    # Weighted sampling without replacement via reservoir algorithm A-Res.
    # Each item receives key = uniform(0,1)^(1/weight); take the top `count`.
    keyed = [
        (rng.random() ** (1.0 / w), movie)
        for w, movie in zip(weights, movies)
        if w > 0
    ]
    keyed.sort(key=lambda pair: pair[0], reverse=True)
    return [movie for _, movie in keyed[:count]]
