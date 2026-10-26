"""Film quality scoring via AI, persisted to the shared metadata SQLite cache.

Quality scores live in a `quality_scores` table in the same database as
`metadata_cache`. Migration is additive: the table is created if absent,
existing data is never touched. The five-dimension breakdown is stored in the
DB; the aggregate is also written to the archive markdown as
`<!-- quality: 8.60 -->` so it is available without a DB query.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .backends import AIBackend
from .logging_utils import get_logger, grimwatch_data_dir, log_exception
from .parser import GenreBlock, Movie, MovieList, normalized_title_key

LOGGER = get_logger("quality")

QUALITY_CACHE_FILENAME = "metadata_cache.sqlite"
_SCORE_BATCH_SIZE = 10
_SCORE_MAX_TOKENS = 1024


@dataclass(frozen=True)
class FilmQualityScore:
    """Five-dimension quality assessment for one film.

    All numeric fields are 0.0–10.0. aggregate is the unweighted mean.
    scored_at is an ISO date string (YYYY-MM-DD).
    """

    title: str
    execution: float
    narrative: float
    enjoyment: float
    significance: float
    critical_reception: float
    aggregate: float
    justification: str
    scored_at: str


def _cache_path() -> Path:
    return grimwatch_data_dir() / QUALITY_CACHE_FILENAME


def _ensure_quality_table(connection: sqlite3.Connection) -> None:
    """Add the quality_scores table if it does not exist.

    This is the only schema migration path: purely additive, no DROP or ALTER.
    The metadata_cache table created by metadata.py is untouched.
    """
    connection.execute("""
        CREATE TABLE IF NOT EXISTS quality_scores (
            title_key   TEXT    NOT NULL PRIMARY KEY,
            title       TEXT    NOT NULL,
            execution   REAL    NOT NULL,
            narrative   REAL    NOT NULL,
            enjoyment   REAL    NOT NULL,
            significance REAL   NOT NULL,
            critical_reception REAL NOT NULL,
            aggregate   REAL    NOT NULL,
            justification TEXT  NOT NULL,
            scored_at   TEXT    NOT NULL,
            stored_at   REAL    NOT NULL
        )
    """)
    connection.commit()


def _connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or _cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    _ensure_quality_table(conn)
    return conn


def load_score(title: str, db_path: Path | None = None) -> FilmQualityScore | None:
    """Retrieve a cached quality score for one title, or None if absent."""
    key = normalized_title_key(title)
    if not key:
        return None
    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                """SELECT title, execution, narrative, enjoyment, significance,
                          critical_reception, aggregate, justification, scored_at
                   FROM quality_scores WHERE title_key = ?""",
                (key,),
            ).fetchone()
    except (OSError, sqlite3.Error) as exc:
        log_exception(LOGGER, f"Quality score read failed for {title!r}", exc)
        return None

    if row is None:
        return None
    t, ex, na, en, si, cr, ag, ju, sa = row
    return FilmQualityScore(
        title=t,
        execution=ex,
        narrative=na,
        enjoyment=en,
        significance=si,
        critical_reception=cr,
        aggregate=ag,
        justification=ju,
        scored_at=sa,
    )


def _store_score(score: FilmQualityScore, db_path: Path | None = None) -> None:
    key = normalized_title_key(score.title)
    if not key:
        return
    try:
        with _connect(db_path) as conn:
            conn.execute(
                """INSERT INTO quality_scores
                       (title_key, title, execution, narrative, enjoyment,
                        significance, critical_reception, aggregate,
                        justification, scored_at, stored_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(title_key) DO UPDATE SET
                       title=excluded.title,
                       execution=excluded.execution,
                       narrative=excluded.narrative,
                       enjoyment=excluded.enjoyment,
                       significance=excluded.significance,
                       critical_reception=excluded.critical_reception,
                       aggregate=excluded.aggregate,
                       justification=excluded.justification,
                       scored_at=excluded.scored_at,
                       stored_at=excluded.stored_at
                """,
                (
                    key, score.title, score.execution, score.narrative,
                    score.enjoyment, score.significance, score.critical_reception,
                    score.aggregate, score.justification, score.scored_at,
                    time.time(),
                ),
            )
            conn.commit()
    except (OSError, sqlite3.Error, TypeError) as exc:
        log_exception(LOGGER, f"Quality score write failed for {score.title!r}", exc)


_SCORE_SYSTEM = """\
You are a film critic with encyclopedic knowledge of world cinema.
Score each film on five criteria, each 0.0–10.0.
Base scores on critical consensus, canonical standing, and documented reception.
Do NOT inflate scores: 5.0 = average, 7.0 = genuinely good, 9.0+ = masterwork level.
Return ONLY JSON with no prose:
{"scores": [{"title": "...", "execution": 8.5, "narrative": 7.0,
"enjoyment": 9.0, "significance": 9.5, "critical_reception": 9.0,
"justification": "one sentence covering all five dimensions"}]}
Criteria:
  execution         — craft, direction, technical achievement
  narrative         — screenplay, structure, dramatic coherence
  enjoyment         — rewatchability, engagement, staying power
  significance      — cultural, historical, or artistic importance
  critical_reception — consensus across serious critical sources\
"""


def _parse_score_response(raw: str, titles: Sequence[str]) -> dict[str, FilmQualityScore]:
    """Parse the batch scoring response into a title-keyed dict.

    Tolerant of extra/missing entries; each returned score is validated
    against the 0.0-10.0 range before acceptance.
    """
    import re
    from datetime import date

    raw = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).strip().strip("`")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    scores_raw = payload.get("scores", []) if isinstance(payload, dict) else []
    if not isinstance(scores_raw, list):
        return {}

    scored_at = date.today().isoformat()
    result: dict[str, FilmQualityScore] = {}

    for item in scores_raw:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue

        dims: dict[str, float] = {}
        valid = True
        for dim in ("execution", "narrative", "enjoyment", "significance", "critical_reception"):
            raw_val = item.get(dim)
            try:
                val = float(raw_val)
            except (TypeError, ValueError):
                valid = False
                break
            if not 0.0 <= val <= 10.0:
                valid = False
                break
            dims[dim] = val

        if not valid:
            continue

        aggregate = round(sum(dims.values()) / len(dims), 2)
        justification = str(item.get("justification", "")).strip()[:400] or "Scored by AI."

        score = FilmQualityScore(
            title=title.strip(),
            scored_at=scored_at,
            aggregate=aggregate,
            justification=justification,
            **dims,  # type: ignore[arg-type]
        )
        result[normalized_title_key(title)] = score

    return result


def score_films(
    titles: Sequence[str],
    backend: AIBackend,
    model: str,
    db_path: Path | None = None,
) -> dict[str, FilmQualityScore]:
    """Score a batch of films and persist results to the quality_scores table.

    Returns a title-key dict of scores. Films that fail scoring are absent
    from the result; the caller decides whether to retry or show a warning.
    Batched at _SCORE_BATCH_SIZE to stay within model context limits.
    """
    clean_titles = [t.strip() for t in titles if isinstance(t, str) and t.strip()]
    if not clean_titles:
        return {}

    all_scores: dict[str, FilmQualityScore] = {}

    for start in range(0, len(clean_titles), _SCORE_BATCH_SIZE):
        batch = clean_titles[start : start + _SCORE_BATCH_SIZE]
        user = "\n".join(f"- {t}" for t in batch)
        try:
            raw = backend.chat(
                model,
                _SCORE_SYSTEM,
                user,
                max_tokens=_SCORE_MAX_TOKENS,
                json_mode=True,
            )
        except Exception as exc:
            log_exception(LOGGER, f"Quality scoring failed for batch starting at {start}", exc)
            continue

        batch_scores = _parse_score_response(raw, batch)
        for score in batch_scores.values():
            _store_score(score, db_path)
        all_scores.update(batch_scores)

    return all_scores


def score_genre(
    block: GenreBlock,
    backend: AIBackend,
    model: str,
    archive: MovieList,
    rescore: bool = False,
    db_path: Path | None = None,
) -> dict[str, FilmQualityScore]:
    """Score all films in a genre block, skip already-scored unless rescore=True.

    Updates movie.quality_score in memory and saves the archive after each
    batch so a crash mid-run does not lose all progress.
    """
    to_score: list[Movie] = []
    for movie in block.entries:
        if rescore or movie.quality_score is None:
            to_score.append(movie)

    if not to_score:
        return {}

    titles = [m.title for m in to_score]
    scores = score_films(titles, backend, model, db_path)

    for movie in to_score:
        key = normalized_title_key(movie.title)
        if key in scores:
            movie.quality_score = scores[key].aggregate

    archive.save()
    return scores
