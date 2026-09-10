"""Reliable, source-bound screen-work metadata for classification.

The resolver collects objective bibliographic facts only.  It never makes a
genre decision and contains no country, language, studio, or title-specific
classification routing.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from .logging_utils import get_logger, grimwatch_data_dir, log_exception
from .parser import clean_display_title, extract_title_year, normalized_title_key, strip_release_year


WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_QUERY_API = "https://query.wikidata.org/sparql"
WIKIDATA_PAGE = "https://www.wikidata.org/wiki/"
TMDB_API = "https://api.themoviedb.org/3"
OMDB_API = "https://www.omdbapi.com/"
USER_AGENT = "grimwatch/1.0.0 (metadata verification; contact: local application)"

WIKIDATA_TIMEOUT_SECONDS = 10
TMDB_TIMEOUT_SECONDS = 8
OMDB_TIMEOUT_SECONDS = 8
MAX_ENTITY_BATCH_SIZE = 50
REQUEST_PAUSE_SECONDS = 0.05
RATE_LIMIT_ATTEMPTS = 3
BATCH_QUERY_THRESHOLD = 5
MAX_FALLBACK_WORKERS = 4
METADATA_CACHE_FILENAME = "metadata_cache.sqlite"
METADATA_CACHE_TTL_SECONDS = 90 * 24 * 60 * 60

# These apply equally to every title and every source.  Fuzzy choices always
# remain reviewable rather than silently authoritative.
FUZZY_MATCH_THRESHOLD = 0.90
FUZZY_MATCH_MARGIN = 0.03

_CLAIM_PROPERTIES = {
    "P31": "types",
    "P495": "countries",
    "P57": "directors",
    "P272": "studios",
    "P136": "genres",
    "P179": "franchises",
    "P161": "cast",
}
_SCREEN_WORK_WORDS = (
    "film", "television", "tv series", "season", "miniseries",
    "television season", "film series", "documentary series", "web series",
)


def _log_failure(message: str, exc: BaseException) -> None:
    """Diagnostics are useful but must never break a lookup."""
    try:
        log_exception(get_logger("metadata"), message, exc)
    except Exception:  # pragma: no cover - logging is intentionally best effort
        pass


class MetadataProviderError(RuntimeError):
    """Provider exception carrying a user-actionable failure class."""

    def __init__(self, provider: str, status: str, detail: str) -> None:
        super().__init__(detail)
        self.provider = provider
        self.status = status


@dataclass(frozen=True)
class VerifiedMetadata:
    """The factual boundary delivered to the classifier.

    ``verified`` records are safe source selections.  Fuzzy records stay
    verified so the rest of a batch can proceed, but ``needs_review`` flags
    them for the caller.
    """

    title: str
    verified: bool
    reason: str
    entity_id: str | None = None  # Legacy Wikidata field retained for callers.
    release_year: int | None = None
    source_description: str = ""
    media_types: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    directors: tuple[str, ...] = ()
    studios: tuple[str, ...] = ()
    genres: tuple[str, ...] = ()
    franchises: tuple[str, ...] = ()
    cast: tuple[str, ...] = ()
    source: str = ""
    source_id: str | None = None
    source_url_value: str | None = None
    match_method: str = ""
    match_confidence: float | None = None
    needs_review: bool = False
    status: str = "verified"
    from_cache: bool = False
    fetched_at: float | None = None

    @classmethod
    def review(cls, title: str, reason: str, *, status: str = "no_match") -> "VerifiedMetadata":
        return cls(title=title, verified=False, reason=reason, needs_review=True, status=status)

    @property
    def source_url(self) -> str | None:
        if self.source_url_value:
            return self.source_url_value
        return f"{WIKIDATA_PAGE}{self.entity_id}" if self.entity_id else None

    @property
    def prompt_context(self) -> str:
        details: list[str] = []
        if self.release_year is not None:
            details.append(f"release year: {self.release_year}")
        if self.media_types:
            details.append(f"format: {', '.join(self.media_types[:3])}")
        if self.source_description:
            details.append(f"source description: {self.source_description[:280]}")
        if self.countries:
            details.append(f"countries of origin: {', '.join(self.countries[:4])}")
        if self.directors:
            details.append(f"director: {', '.join(self.directors[:3])}")
        if self.studios:
            details.append(f"production company: {', '.join(self.studios[:3])}")
        if self.genres:
            details.append(f"source genres: {', '.join(self.genres[:5])}")
        if self.franchises:
            details.append(f"series/franchise: {', '.join(self.franchises[:3])}")
        if self.cast:
            details.append(f"cast: {', '.join(self.cast[:8])}")
        return f"Verified {self.source or 'metadata source'} metadata — " + "; ".join(details) + "."


def _cache_path() -> Path:
    return grimwatch_data_dir() / METADATA_CACHE_FILENAME


def _payload(record: VerifiedMetadata) -> dict[str, Any]:
    return {
        "title": record.title, "verified": record.verified, "reason": record.reason,
        "entity_id": record.entity_id, "release_year": record.release_year,
        "source_description": record.source_description,
        "media_types": list(record.media_types), "countries": list(record.countries),
        "directors": list(record.directors), "studios": list(record.studios),
        "genres": list(record.genres), "franchises": list(record.franchises), "cast": list(record.cast),
        "source": record.source, "source_id": record.source_id,
        "source_url_value": record.source_url_value, "match_method": record.match_method,
        "match_confidence": record.match_confidence, "needs_review": record.needs_review,
        "status": record.status,
    }


def _tuple(value: object) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str) and item.strip()) if isinstance(value, list) else ()


def _from_payload(payload: object, fetched_at: float) -> VerifiedMetadata | None:
    if not isinstance(payload, dict) or not payload.get("verified"):
        return None
    title, reason = payload.get("title"), payload.get("reason")
    if not isinstance(title, str) or not isinstance(reason, str):
        return None
    confidence = payload.get("match_confidence")
    try:
        confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    return VerifiedMetadata(
        title=title, verified=True, reason=reason,
        entity_id=payload.get("entity_id") if isinstance(payload.get("entity_id"), str) else None,
        release_year=payload.get("release_year") if isinstance(payload.get("release_year"), int) else None,
        source_description=payload.get("source_description") if isinstance(payload.get("source_description"), str) else "",
        media_types=_tuple(payload.get("media_types")), countries=_tuple(payload.get("countries")),
        directors=_tuple(payload.get("directors")), studios=_tuple(payload.get("studios")),
        genres=_tuple(payload.get("genres")), franchises=_tuple(payload.get("franchises")),
        cast=_tuple(payload.get("cast")),
        source=payload.get("source") if isinstance(payload.get("source"), str) else "",
        source_id=payload.get("source_id") if isinstance(payload.get("source_id"), str) else None,
        source_url_value=payload.get("source_url_value") if isinstance(payload.get("source_url_value"), str) else None,
        match_method=payload.get("match_method") if isinstance(payload.get("match_method"), str) else "",
        match_confidence=confidence, needs_review=bool(payload.get("needs_review")),
        status=payload.get("status") if isinstance(payload.get("status"), str) else "verified",
        fetched_at=fetched_at,
    )


class MetadataCache:
    """Small persistent cache.  It stores successes only and is safe to lose."""

    def __init__(self, path: str | Path | None = None, ttl_seconds: int = METADATA_CACHE_TTL_SECONDS) -> None:
        self.path = Path(path) if path is not None else _cache_path()
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _year_key(year: int | None) -> str:
        return str(year) if year is not None else ""

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.execute(
            """CREATE TABLE IF NOT EXISTS metadata_cache (
                title_key TEXT NOT NULL, year_key TEXT NOT NULL,
                payload TEXT NOT NULL, fetched_at REAL NOT NULL,
                PRIMARY KEY (title_key, year_key)
            )"""
        )
        return connection

    def get(self, title_key: str, year: int | None, *, now: float | None = None) -> VerifiedMetadata | None:
        if not title_key:
            return None
        current = time.time() if now is None else now
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            row = connection.execute(
                "SELECT payload, fetched_at FROM metadata_cache WHERE title_key = ? AND year_key = ?",
                (title_key, self._year_key(year)),
            ).fetchone()
            if row is None:
                return None
            payload_text, fetched_at = row
            if not isinstance(fetched_at, (int, float)) or current - float(fetched_at) > self.ttl_seconds:
                connection.execute(
                    "DELETE FROM metadata_cache WHERE title_key = ? AND year_key = ?",
                    (title_key, self._year_key(year)),
                )
                connection.commit()
                return None
            try:
                payload = json.loads(payload_text)
            except (TypeError, json.JSONDecodeError):
                connection.execute(
                    "DELETE FROM metadata_cache WHERE title_key = ? AND year_key = ?",
                    (title_key, self._year_key(year)),
                )
                connection.commit()
                return None
            return _from_payload(payload, float(fetched_at))
        except (OSError, sqlite3.Error) as exc:
            _log_failure("Metadata cache read failed", exc)
            return None
        finally:
            if connection is not None:
                connection.close()

    def put(self, title_key: str, year: int | None, record: VerifiedMetadata, *, now: float | None = None) -> None:
        if not title_key or not record.verified:
            return
        fetched_at = time.time() if now is None else now
        connection: sqlite3.Connection | None = None
        try:
            serialized = json.dumps(_payload(replace(record, from_cache=False, fetched_at=fetched_at)))
            connection = self._connect()
            connection.execute(
                """INSERT INTO metadata_cache (title_key, year_key, payload, fetched_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(title_key, year_key) DO UPDATE SET
                     payload=excluded.payload, fetched_at=excluded.fetched_at""",
                (title_key, self._year_key(year), serialized, fetched_at),
            )
            connection.commit()
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            _log_failure("Metadata cache write failed", exc)
        finally:
            if connection is not None:
                connection.close()


class _HostRateLimiter:
    """Thread-safe per-host spacing for bounded parallel fallback lookups."""

    def __init__(self, interval: float = REQUEST_PAUSE_SECONDS) -> None:
        self.interval = interval
        self._lock = Lock()
        self._next_allowed: dict[str, float] = {}

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            scheduled = max(now, self._next_allowed.get(host, now))
            self._next_allowed[host] = scheduled + self.interval
        if scheduled > now:
            time.sleep(scheduled - now)

    def defer(self, host: str, seconds: float) -> None:
        with self._lock:
            self._next_allowed[host] = max(self._next_allowed.get(host, 0.0), time.monotonic() + seconds)


@dataclass(frozen=True)
class _ProviderResult:
    record: VerifiedMetadata | None = None
    status: str = "no_match"
    detail: str = ""

    @classmethod
    def resolved(cls, record: VerifiedMetadata) -> "_ProviderResult":
        return cls(record=record, status="verified")


@dataclass(frozen=True)
class _Candidate:
    identifier: str
    labels: tuple[str, ...]
    release_year: int | None
    payload: Any


def _status_for(exc: BaseException) -> str:
    if isinstance(exc, MetadataProviderError):
        return exc.status
    return "rate_limited" if isinstance(exc, HTTPError) and exc.code == 429 else "unreachable"


def _failure(provider: str, exc: BaseException) -> _ProviderResult:
    return _ProviderResult(status=_status_for(exc), detail=f"{provider}: {' '.join(str(exc).split())[:240]}")


def _token_set_ratio(left: str, right: str) -> float:
    """Dependency-free token-set ratio in 0..1, globally used by all sources."""
    left_tokens, right_tokens = set(normalized_title_key(left).split()), set(normalized_title_key(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    common = left_tokens & right_tokens
    common_text = " ".join(sorted(common))
    left_text = " ".join(sorted(left_tokens))
    right_text = " ".join(sorted(right_tokens))
    return max(
        SequenceMatcher(None, common_text, left_text).ratio(),
        SequenceMatcher(None, common_text, right_text).ratio(),
        SequenceMatcher(None, left_text, right_text).ratio(),
    )


class WikidataMetadataVerifier:
    """Cache-first resolver with non-fatal Wikidata, TMDb, and OMDb fallthrough."""

    def __init__(
        self,
        *,
        tmdb_api_key: str | None = None,
        omdb_api_key: str | None = None,
        cache_path: str | Path | None = None,
        cache_ttl_seconds: int = METADATA_CACHE_TTL_SECONDS,
        max_workers: int = MAX_FALLBACK_WORKERS,
    ) -> None:
        self.tmdb_api_key = (tmdb_api_key or "").strip()
        self.omdb_api_key = (omdb_api_key or "").strip()
        self._cache = MetadataCache(cache_path, cache_ttl_seconds)
        self._limiter = _HostRateLimiter()
        self.max_workers = max(1, min(MAX_FALLBACK_WORKERS, int(max_workers)))

    def _json_request(
        self, url: str, params: dict[str, str], *, provider: str, timeout: int,
        accept: str = "application/json",
    ) -> dict[str, Any]:
        request = Request(
            f"{url}{'&' if '?' in url else '?'}{urlencode(params)}",
            headers={"Accept": accept, "User-Agent": USER_AGENT},
        )
        host = urlsplit(url).netloc.casefold()
        for attempt in range(RATE_LIMIT_ATTEMPTS):
            self._limiter.wait(host)
            try:
                with urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                if exc.code == 429 and attempt < RATE_LIMIT_ATTEMPTS - 1:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        delay = float(retry_after) if retry_after is not None else 2.0 * (attempt + 1)
                    except ValueError:
                        delay = 2.0 * (attempt + 1)
                    self._limiter.defer(host, min(delay, 20.0))
                    continue
                _log_failure(f"{provider} metadata request failed", exc)
                status = "rate_limited" if exc.code == 429 else "unreachable"
                raise MetadataProviderError(provider, status, f"{provider} request failed: {exc}") from exc
            except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                _log_failure(f"{provider} metadata request failed", exc)
                raise MetadataProviderError(provider, "unreachable", f"{provider} request failed: {exc}") from exc
            if isinstance(payload, dict):
                return payload
            exc = ValueError("invalid JSON response")
            _log_failure(f"{provider} metadata request failed", exc)
            raise MetadataProviderError(provider, "unreachable", f"{provider} request returned invalid JSON.")
        exc = RuntimeError("rate-limit retries exhausted")
        _log_failure(f"{provider} metadata request failed", exc)
        raise MetadataProviderError(provider, "rate_limited", f"{provider} rate-limit retries exhausted.")

    def _request(self, params: dict[str, str]) -> dict[str, Any]:
        """Patchable Wikidata action-API seam retained for existing callers/tests."""
        payload = self._json_request(
            WIKIDATA_API, {"format": "json", "formatversion": "2", "maxlag": "5", **params},
            provider="Wikidata", timeout=WIKIDATA_TIMEOUT_SECONDS,
        )
        if payload.get("error"):
            detail = payload.get("error", {}).get("info", "invalid response")
            exc = RuntimeError(str(detail))
            _log_failure("Wikidata metadata request failed", exc)
            raise MetadataProviderError("Wikidata", "unreachable", f"Wikidata request failed: {detail}")
        return payload

    def _sparql(self, query: str) -> list[dict[str, Any]]:
        payload = self._json_request(
            WIKIDATA_QUERY_API, {"format": "json", "query": query}, provider="Wikidata",
            timeout=WIKIDATA_TIMEOUT_SECONDS, accept="application/sparql-results+json",
        )
        rows = payload.get("results", {}).get("bindings", [])
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        exc = RuntimeError("query returned an invalid response")
        _log_failure("Wikidata SPARQL request failed", exc)
        raise MetadataProviderError("Wikidata", "unreachable", "Wikidata query returned an invalid response.")

    @staticmethod
    def _sparql_literal(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")

    @staticmethod
    def _title_variants(title: str) -> tuple[str, ...]:
        base = strip_release_year(title) or title
        year = extract_title_year(title)
        variants = [base]
        season = re.match(r"^(?P<series>.+?)\s*(?:,\s*)?season\s+(?P<number>\d+)$", base, re.IGNORECASE)
        if season:
            variants.append(f"{season.group('series').strip()}, season {season.group('number')}")
        # Emit a year-qualified variant for the REST search path; helps narrow
        # results for common-word titles like "Mirror" or "Heat".
        if year is not None:
            variants.append(f"{base} ({year})")
        return tuple(dict.fromkeys(variants))

    @staticmethod
    def _binding_value(row: dict[str, Any], name: str) -> str | None:
        value = row.get(name, {}).get("value")
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _year_from_text(value: object) -> int | None:
        match = re.search(r"(?:\+|^)(\d{4})", value) if isinstance(value, str) else None
        return int(match.group(1)) if match else None

    @staticmethod
    def _screen_work(media_types: Sequence[str]) -> bool:
        return any(word in " ".join(media_types).casefold() for word in _SCREEN_WORK_WORDS)

    @staticmethod
    def _verified_reason(provider: str, method: str, confidence: float) -> str:
        return (
            f"Verified against {provider} (fuzzy match, {confidence:.2f} confidence — review recommended)."
            if method == "fuzzy" else f"Verified against {provider} (exact match)."
        )

    @staticmethod
    def _no_match() -> _ProviderResult:
        return _ProviderResult(status="no_match", detail="No unique source record matched the supplied title and year.")

    def _choose_candidate(self, title: str, candidates: Sequence[_Candidate]) -> tuple[_Candidate, str, float] | None:
        wanted = normalized_title_key(title)
        exact = [candidate for candidate in candidates if any(normalized_title_key(label) == wanted for label in candidate.labels)]
        if len(exact) == 1:
            return exact[0], "exact", 1.0
        if len(exact) > 1:
            return None
        scored = [
            (max((_token_set_ratio(title, label) for label in candidate.labels), default=0.0), candidate)
            for candidate in candidates
        ]
        scored = [item for item in scored if item[0] >= FUZZY_MATCH_THRESHOLD]
        if not scored:
            return None
        scored.sort(key=lambda item: (-item[0], item[1].identifier))
        if len(scored) > 1 and scored[0][0] - scored[1][0] < FUZZY_MATCH_MARGIN:
            return None
        return scored[0][1], "fuzzy", scored[0][0]

    def _verify_batch_query(self, titles: Sequence[str]) -> list[_ProviderResult]:
        """Exact batch query; every failure or miss deliberately falls through."""
        values = [
            f'("{index}" "{self._sparql_literal(variant)}"@en)'
            for index, title in enumerate(titles) for variant in self._title_variants(title)
        ]
        query = """
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX schema: <http://schema.org/>
PREFIX bd: <http://www.bigdata.com/rdf#>
PREFIX wikibase: <http://wikiba.se/ontology#>
SELECT ?input ?item ?date ?typeLabel ?countryLabel ?directorLabel ?studioLabel ?genreLabel ?seriesLabel ?castLabel ?description WHERE {
  VALUES (?input ?sourceLabel) { %s }
  ?item rdfs:label ?sourceLabel .
  OPTIONAL { ?item wdt:P577 ?releaseDate . }
  OPTIONAL { ?item wdt:P580 ?startDate . }
  BIND(COALESCE(?releaseDate, ?startDate) AS ?date)
  OPTIONAL { ?item wdt:P31 ?type . }
  OPTIONAL { ?item wdt:P495 ?country . }
  OPTIONAL { ?item wdt:P57 ?director . }
  OPTIONAL { ?item wdt:P272 ?studio . }
  OPTIONAL { ?item wdt:P136 ?genre . }
  OPTIONAL { ?item wdt:P179 ?series . }
  OPTIONAL { ?item wdt:P161 ?cast . }
  OPTIONAL { ?item schema:description ?description FILTER(LANG(?description) = "en") }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
""" % " ".join(values)
        try:
            rows = self._sparql(query)
        except Exception as exc:
            if not isinstance(exc, MetadataProviderError):
                _log_failure("Wikidata SPARQL lookup failed", exc)
            return [_failure("Wikidata", exc) for _ in titles]

        fields = {
            "typeLabel": "media_types", "countryLabel": "countries", "directorLabel": "directors",
            "studioLabel": "studios", "genreLabel": "genres", "seriesLabel": "franchises", "castLabel": "cast",
        }
        candidates: dict[int, dict[str, dict[str, Any]]] = {}
        for row in rows:
            raw_index, item_url = self._binding_value(row, "input"), self._binding_value(row, "item")
            if raw_index is None or item_url is None:
                continue
            try:
                index = int(raw_index)
            except ValueError:
                continue
            if not 0 <= index < len(titles):
                continue
            entity_id = item_url.rsplit("/", 1)[-1]
            candidate = candidates.setdefault(index, {}).setdefault(entity_id, {
                "entity_id": entity_id, "release_year": None, "source_description": "",
                **{field: [] for field in fields.values()},
            })
            release_year = self._year_from_text(self._binding_value(row, "date"))
            if release_year is not None:
                candidate["release_year"] = release_year
            description = self._binding_value(row, "description")
            if description:
                candidate["source_description"] = description
            for binding, field in fields.items():
                label = self._binding_value(row, binding)
                if label and label not in candidate[field]:
                    candidate[field].append(label)

        results: list[_ProviderResult] = []
        for index, title in enumerate(titles):
            year = extract_title_year(title)
            options = [
                candidate for candidate in candidates.get(index, {}).values()
                if (year is None or candidate["release_year"] == year) and self._screen_work(candidate["media_types"])
            ]
            if len(options) != 1:
                results.append(self._no_match())
                continue
            candidate = options[0]
            record = VerifiedMetadata(
                title=title, verified=True, reason=self._verified_reason("Wikidata", "exact", 1.0),
                entity_id=candidate["entity_id"], release_year=candidate["release_year"],
                source_description=candidate["source_description"], media_types=tuple(candidate["media_types"]),
                countries=tuple(candidate["countries"]), directors=tuple(candidate["directors"]),
                studios=tuple(candidate["studios"]), genres=tuple(candidate["genres"]),
                franchises=tuple(candidate["franchises"]), cast=tuple(candidate["cast"]),
                source="Wikidata", source_id=candidate["entity_id"],
                source_url_value=f"{WIKIDATA_PAGE}{candidate['entity_id']}", match_method="exact", match_confidence=1.0,
            )
            results.append(_ProviderResult.resolved(record))
        return results

    def _search(self, title: str) -> list[dict[str, Any]]:
        base_title = strip_release_year(title) or title
        terms = [base_title]
        season = re.match(r"^(?P<series>.+?)\s*(?:,\s*)?season\s+\d+$", base_title, re.IGNORECASE)
        if season:
            terms.append(season.group("series").strip())
        rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for term in terms:
            payload = self._request({"action": "wbsearchentities", "search": term, "language": "en", "type": "item", "limit": "20" if term != base_title else "8"})
            for row in payload.get("search", []):
                item_id = row.get("id") if isinstance(row, dict) else None
                if isinstance(item_id, str) and item_id not in seen_ids:
                    rows.append(row)
                    seen_ids.add(item_id)
        return rows

    def _entities(self, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        unique_ids = list(dict.fromkeys(item for item in ids if item))
        entities: dict[str, dict[str, Any]] = {}
        for start in range(0, len(unique_ids), MAX_ENTITY_BATCH_SIZE):
            payload = self._request({
                "action": "wbgetentities", "ids": "|".join(unique_ids[start:start + MAX_ENTITY_BATCH_SIZE]),
                "props": "labels|aliases|descriptions|claims", "languages": "en",
            })
            rows = payload.get("entities")
            if isinstance(rows, dict):
                entities.update({key: value for key, value in rows.items() if isinstance(value, dict)})
        return entities

    @staticmethod
    def _entity_label_values(entity: dict[str, Any]) -> tuple[str, ...]:
        values: list[str] = []
        label = entity.get("labels", {}).get("en", {}).get("value")
        if isinstance(label, str) and label.strip():
            values.append(label)
        aliases = entity.get("aliases", {}).get("en", [])
        if isinstance(aliases, list):
            for alias in aliases:
                value = alias.get("value") if isinstance(alias, dict) else None
                if isinstance(value, str) and value.strip() and value not in values:
                    values.append(value)
        return tuple(values)

    @staticmethod
    def _entity_labels(entity: dict[str, Any]) -> set[str]:
        return {normalized_title_key(label) for label in WikidataMetadataVerifier._entity_label_values(entity)}

    @staticmethod
    def _description(entity: dict[str, Any]) -> str:
        value = entity.get("descriptions", {}).get("en", {}).get("value", "")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _claim_ids(entity: dict[str, Any], property_id: str) -> tuple[str, ...]:
        ids: list[str] = []
        claims = entity.get("claims", {}).get(property_id, [])
        if not isinstance(claims, list):
            return ()
        for claim in claims:
            try:
                value = claim["mainsnak"]["datavalue"]["value"]
                item_id = value.get("id") if isinstance(value, dict) else None
            except (KeyError, TypeError):
                item_id = None
            if isinstance(item_id, str) and item_id not in ids:
                ids.append(item_id)
        return tuple(ids)

    @staticmethod
    def _release_year(entity: dict[str, Any]) -> int | None:
        for property_id in ("P577", "P580"):
            claims = entity.get("claims", {}).get(property_id, [])
            if not isinstance(claims, list):
                continue
            for claim in claims:
                try:
                    value = claim["mainsnak"]["datavalue"]["value"]
                    time_value = value.get("time") if isinstance(value, dict) else None
                except (KeyError, TypeError):
                    time_value = None
                year = WikidataMetadataVerifier._year_from_text(time_value)
                if year is not None:
                    return year
        return None

    @staticmethod
    def _label(entity: dict[str, Any] | None) -> str | None:
        label = entity.get("labels", {}).get("en", {}).get("value") if entity else None
        return label if isinstance(label, str) and label.strip() else None

    def _labels_by_property(self, entity: dict[str, Any], refs: dict[str, dict[str, Any]]) -> dict[str, tuple[str, ...]]:
        return {
            field: tuple(label for item_id in self._claim_ids(entity, property) if (label := self._label(refs.get(item_id))))
            for property, field in _CLAIM_PROPERTIES.items()
        }

    def _wikidata_record(self, title: str, entity_id: str, entity: dict[str, Any], refs: dict[str, dict[str, Any]], method: str, confidence: float) -> VerifiedMetadata | None:
        properties = self._labels_by_property(entity, refs)
        if not self._screen_work(properties["types"]):
            return None
        return VerifiedMetadata(
            title=title, verified=True, reason=self._verified_reason("Wikidata", method, confidence),
            entity_id=entity_id, release_year=self._release_year(entity), source_description=self._description(entity),
            media_types=properties["types"], countries=properties["countries"], directors=properties["directors"],
            studios=properties["studios"], genres=properties["genres"], franchises=properties["franchises"],
            cast=properties["cast"],
            source="Wikidata", source_id=entity_id, source_url_value=f"{WIKIDATA_PAGE}{entity_id}",
            match_method=method, match_confidence=confidence, needs_review=method == "fuzzy",
        )

    def _resolve_wikidata_rest(self, title: str) -> _ProviderResult:
        try:
            search_rows = self._search(title)
            candidate_ids = [row.get("id") for row in search_rows if isinstance(row.get("id"), str)]
            if not candidate_ids:
                return self._no_match()
            primary = self._entities(candidate_ids)
            referenced_ids = [
                item_id for entity_id in candidate_ids for property in _CLAIM_PROPERTIES
                for item_id in self._claim_ids(primary.get(entity_id, {}), property)
            ]
            refs = self._entities(referenced_ids)
        except Exception as exc:
            if not isinstance(exc, MetadataProviderError):
                _log_failure("Wikidata REST lookup failed", exc)
            return _failure("Wikidata", exc)

        requested_year = extract_title_year(title)
        candidates: list[_Candidate] = []
        for entity_id in candidate_ids:
            entity = primary.get(entity_id)
            if not entity:
                continue
            year = self._release_year(entity)
            if requested_year is not None and year != requested_year:
                continue
            if not self._screen_work(self._labels_by_property(entity, refs)["types"]):
                continue
            labels = self._entity_label_values(entity)
            if labels:
                candidates.append(_Candidate(entity_id, labels, year, entity))
        chosen = self._choose_candidate(title, candidates)
        if chosen is None:
            return self._no_match()
        candidate, method, confidence = chosen
        record = self._wikidata_record(title, candidate.identifier, candidate.payload, refs, method, confidence)
        return _ProviderResult.resolved(record) if record is not None else self._no_match()

    @staticmethod
    def _tmdb_labels(row: dict[str, Any], kind: str) -> tuple[str, ...]:
        keys = ("title", "original_title") if kind == "movie" else ("name", "original_name")
        values: list[str] = []
        for key in keys:
            value = row.get(key)
            if isinstance(value, str) and value.strip() and value not in values:
                values.append(value)
        return tuple(values)

    @staticmethod
    def _tmdb_year(row: dict[str, Any], kind: str) -> int | None:
        return WikidataMetadataVerifier._year_from_text(row.get("release_date" if kind == "movie" else "first_air_date"))

    def _tmdb_get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        payload = self._json_request(
            f"{TMDB_API}{path}", {"api_key": self.tmdb_api_key, **(params or {})}, provider="TMDb", timeout=TMDB_TIMEOUT_SECONDS,
        )
        if payload.get("success") is False or payload.get("status_code"):
            detail = payload.get("status_message", "TMDb returned an error")
            exc = RuntimeError(str(detail))
            _log_failure("TMDb metadata request failed", exc)
            raise MetadataProviderError("TMDb", "unreachable", f"TMDb request failed: {detail}")
        return payload

    @staticmethod
    def _named_values(rows: object, key: str = "name") -> tuple[str, ...]:
        values: list[str] = []
        if isinstance(rows, list):
            for row in rows:
                value = row.get(key) if isinstance(row, dict) else None
                if isinstance(value, str) and value.strip() and value not in values:
                    values.append(value)
        return tuple(values)

    def _tmdb_record(self, title: str, candidate: _Candidate, method: str, confidence: float) -> _ProviderResult:
        kind = candidate.payload["kind"]
        try:
            details = self._tmdb_get(f"/{kind}/{candidate.identifier}", {"append_to_response": "credits"})
        except Exception as exc:
            if not isinstance(exc, MetadataProviderError):
                _log_failure("TMDb detail lookup failed", exc)
            return _failure("TMDb", exc)
        requested_year = extract_title_year(title)
        release_year = self._tmdb_year(details, kind)
        if requested_year is not None and release_year != requested_year:
            return self._no_match()
        credits = details.get("credits", {})
        crew = credits.get("crew", []) if isinstance(credits, dict) else []
        directors = self._named_values([item for item in crew if isinstance(item, dict) and item.get("job") == "Director"])
        if not directors and kind == "tv":
            directors = self._named_values(details.get("created_by"))
        collection = details.get("belongs_to_collection")
        record = VerifiedMetadata(
            title=title, verified=True, reason=self._verified_reason("TMDb", method, confidence), release_year=release_year,
            source_description=details.get("overview") if isinstance(details.get("overview"), str) else "",
            media_types=("film",) if kind == "movie" else ("television series",),
            countries=self._named_values(details.get("production_countries")), directors=directors,
            studios=self._named_values(details.get("production_companies")), genres=self._named_values(details.get("genres")),
            franchises=self._named_values([collection]) if isinstance(collection, dict) else (),
            source="TMDb", source_id=f"{kind}:{candidate.identifier}",
            source_url_value=f"https://www.themoviedb.org/{kind}/{candidate.identifier}",
            match_method=method, match_confidence=confidence, needs_review=method == "fuzzy",
        )
        return _ProviderResult.resolved(record)

    def _resolve_tmdb(self, title: str) -> _ProviderResult:
        if not self.tmdb_api_key:
            return _ProviderResult(status="not_configured")
        requested_year, base_title = extract_title_year(title), strip_release_year(title) or title
        candidates: list[_Candidate] = []
        failures: list[_ProviderResult] = []
        reachable = False
        for kind in ("movie", "tv"):
            params = {"query": base_title}
            if kind == "movie" and requested_year is not None:
                params["year"] = str(requested_year)
            try:
                payload = self._tmdb_get(f"/search/{kind}", params)
                reachable = True
            except Exception as exc:
                if not isinstance(exc, MetadataProviderError):
                    _log_failure("TMDb search failed", exc)
                failures.append(_failure("TMDb", exc))
                continue
            rows = payload.get("results", [])
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                identifier, labels, year = row.get("id"), self._tmdb_labels(row, kind), self._tmdb_year(row, kind)
                if not isinstance(identifier, (int, str)) or not labels or (requested_year is not None and year != requested_year):
                    continue
                candidates.append(_Candidate(str(identifier), labels, year, {"kind": kind}))
        chosen = self._choose_candidate(title, candidates)
        if chosen is not None:
            candidate, method, confidence = chosen
            return self._tmdb_record(title, candidate, method, confidence)
        if reachable:
            return self._no_match()
        return self._most_actionable(failures) if failures else self._no_match()

    @staticmethod
    def _omdb_year(value: object) -> int | None:
        match = re.search(r"(\d{4})", value) if isinstance(value, str) else None
        return int(match.group(1)) if match else None

    @staticmethod
    def _omdb_values(value: object) -> tuple[str, ...]:
        if not isinstance(value, str) or value.strip().casefold() == "n/a":
            return ()
        return tuple(part.strip() for part in value.split(",") if part.strip())

    # TODO: Populate ``cast`` from OMDb's comma-separated ``Actors`` field.
    def _resolve_omdb(self, title: str) -> _ProviderResult:
        if not self.omdb_api_key:
            return _ProviderResult(status="not_configured")
        requested_year = extract_title_year(title)
        params = {"apikey": self.omdb_api_key, "t": strip_release_year(title) or title, "r": "json"}
        if requested_year is not None:
            params["y"] = str(requested_year)
        try:
            payload = self._json_request(OMDB_API, params, provider="OMDb", timeout=OMDB_TIMEOUT_SECONDS)
        except Exception as exc:
            if not isinstance(exc, MetadataProviderError):
                _log_failure("OMDb lookup failed", exc)
            return _failure("OMDb", exc)
        if str(payload.get("Response", "True")).casefold() == "false":
            return self._no_match()
        source_title, source_type = payload.get("Title"), payload.get("Type")
        release_year = self._omdb_year(payload.get("Year"))
        if not isinstance(source_title, str) or source_type not in {"movie", "series", "episode"} or (requested_year is not None and release_year != requested_year):
            return self._no_match()
        chosen = self._choose_candidate(title, [_Candidate("omdb", (source_title,), release_year, payload)])
        if chosen is None:
            return self._no_match()
        _candidate, method, confidence = chosen
        imdb_id = payload.get("imdbID") if isinstance(payload.get("imdbID"), str) else None
        media_type = "film" if source_type == "movie" else "television series" if source_type == "series" else "television episode"
        return _ProviderResult.resolved(VerifiedMetadata(
            title=title, verified=True, reason=self._verified_reason("OMDb", method, confidence), release_year=release_year,
            source_description=payload.get("Plot") if isinstance(payload.get("Plot"), str) and payload.get("Plot") != "N/A" else "",
            media_types=(media_type,), countries=self._omdb_values(payload.get("Country")),
            directors=self._omdb_values(payload.get("Director")), studios=self._omdb_values(payload.get("Production")),
            genres=self._omdb_values(payload.get("Genre")), source="OMDb", source_id=imdb_id,
            source_url_value=f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else None,
            match_method=method, match_confidence=confidence, needs_review=method == "fuzzy",
        ))

    def _resolve_parallel(self, titles: Sequence[str], resolver: Callable[[str], _ProviderResult], provider: str) -> list[_ProviderResult]:
        """Bound only independent per-title fallback work, preserving input order."""
        if not titles:
            return []
        if len(titles) == 1:
            try:
                return [resolver(titles[0])]
            except Exception as exc:
                _log_failure(f"{provider} lookup failed", exc)
                return [_failure(provider, exc)]
        results: list[_ProviderResult | None] = [None] * len(titles)
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(titles))) as executor:
            futures = {executor.submit(resolver, title): index for index, title in enumerate(titles)}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception as exc:
                    _log_failure(f"{provider} lookup failed", exc)
                    results[index] = _failure(provider, exc)
        return [result or _ProviderResult(status="unreachable") for result in results]

    @staticmethod
    def _most_actionable(results: Sequence[_ProviderResult]) -> _ProviderResult:
        for status in ("rate_limited", "unreachable", "no_match"):
            for result in results:
                if result.status == status:
                    return result
        return _ProviderResult(status="unreachable")

    def _final_review(self, title: str, attempts: Sequence[_ProviderResult]) -> VerifiedMetadata:
        statuses = {attempt.status for attempt in attempts}
        if "no_match" in statuses:
            year = extract_title_year(title)
            suffix = f" for supplied year ({year})" if year is not None else ""
            return VerifiedMetadata.review(
                title,
                f"No unique metadata match was found{suffix}; check the title spelling or year.",
                status="no_match",
            )
        if "rate_limited" in statuses:
            return VerifiedMetadata.review(title, "Metadata sources were rate-limited; retry shortly.", status="rate_limited")
        return VerifiedMetadata.review(title, "No cache and all configured metadata sources were unreachable; check your connection.", status="unreachable")

    @staticmethod
    def _cache_hit(record: VerifiedMetadata, title: str) -> VerifiedMetadata:
        provider, method = record.source or "metadata source", record.match_method or "exact"
        confidence = record.match_confidence if record.match_confidence is not None else 1.0
        reason = (
            f"Verified from local cache ({provider} fuzzy match, {confidence:.2f} confidence — review recommended)."
            if method == "fuzzy" else f"Verified from local cache ({provider} exact match)."
        )
        return replace(record, title=title, reason=reason, from_cache=True)

    def verify(self, titles: Sequence[str], *, refresh_metadata: bool = False) -> list[VerifiedMetadata]:
        """Resolve cache -> Wikidata -> optional TMDb -> optional OMDb for each title."""
        clean_titles = [clean_display_title(title) for title in titles]
        output: list[VerifiedMetadata | None] = [None] * len(clean_titles)
        unique: dict[tuple[str, int | None], str] = {}
        indexes: dict[tuple[str, int | None], list[int]] = {}
        for index, title in enumerate(clean_titles):
            key_title = normalized_title_key(title)
            if not key_title:
                output[index] = VerifiedMetadata.review(title, "The title is empty after parsing.", status="invalid")
                continue
            key = (key_title, extract_title_year(title))
            unique.setdefault(key, title)
            indexes.setdefault(key, []).append(index)

        resolved: dict[tuple[str, int | None], VerifiedMetadata] = {}
        attempts: dict[tuple[str, int | None], list[_ProviderResult]] = {key: [] for key in unique}
        pending: list[tuple[str, int | None]] = []
        for key, title in unique.items():
            cached = None if refresh_metadata else self._cache.get(key[0], key[1])
            if cached is None:
                pending.append(key)
            else:
                resolved[key] = self._cache_hit(cached, title)

        def accept(key: tuple[str, int | None], result: _ProviderResult) -> None:
            attempts[key].append(result)
            if result.record is not None:
                resolved[key] = result.record
                self._cache.put(key[0], key[1], result.record)

        if len(pending) >= BATCH_QUERY_THRESHOLD:
            for key, result in zip(pending, self._verify_batch_query([unique[key] for key in pending])):
                accept(key, result)

        rest_keys = [key for key in pending if key not in resolved]
        for key, result in zip(rest_keys, self._resolve_parallel([unique[key] for key in rest_keys], self._resolve_wikidata_rest, "Wikidata")):
            accept(key, result)

        tmdb_keys = [key for key in pending if key not in resolved]
        if self.tmdb_api_key:
            for key, result in zip(tmdb_keys, self._resolve_parallel([unique[key] for key in tmdb_keys], self._resolve_tmdb, "TMDb")):
                accept(key, result)

        omdb_keys = [key for key in pending if key not in resolved]
        if self.omdb_api_key:
            for key, result in zip(omdb_keys, self._resolve_parallel([unique[key] for key in omdb_keys], self._resolve_omdb, "OMDb")):
                accept(key, result)

        for key, title in unique.items():
            record = resolved.get(key) or self._final_review(title, attempts[key])
            for index in indexes[key]:
                output[index] = replace(record, title=clean_titles[index])
        return [record or VerifiedMetadata.review("", "Metadata verification failed.", status="unreachable") for record in output]


def verify_film_metadata(
    titles: Sequence[str], *, tmdb_api_key: str | None = None, omdb_api_key: str | None = None,
    refresh_metadata: bool = False,
) -> list[VerifiedMetadata]:
    """Verify title metadata without caching or routing a genre decision."""
    return WikidataMetadataVerifier(tmdb_api_key=tmdb_api_key, omdb_api_key=omdb_api_key).verify(
        titles, refresh_metadata=refresh_metadata,
    )
