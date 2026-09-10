"""The markdown data model used by every grimwatch command.

There are deliberately two readers in this module:

* :class:`MovieList` reads the configured archive, which is a categorized
  checkbox markdown file.
* :func:`read_import_source` reads an incoming file. It also understands a
  plain one-title-per-line list, so commands must never try to load an import
  file as ``MovieList`` just to discover its entries.

Keeping those jobs separate prevents a raw import list from silently becoming
an archive with zero movies.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Optional, Sequence


_CHECKBOX_RE = re.compile(
    r"^\s*(?:[-*+]\s*)?\[(?P<mark>[ xX])\]\s*(?P<title>.*?)\s*$"
)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(?P<title>.*?)\s*$")
_MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(?P<title>.*?)\s*#*\s*$")
_YEAR_RE = re.compile(r"\s*\((?P<year>(?:18|19|20)\d{2})\)\s*$")
_FENCE_RE = re.compile(r"^\s*```")
_WATCHED_ON_RE = re.compile(
    r"\s*<!--\s*watched_on:\s*(?P<date>\d{4}-\d{2}-\d{2})\s*-->\s*$",
    re.IGNORECASE,
)
_NOTE_RE = re.compile(r"<!--\s*note:\s*(?P<note>[^-]*?)\s*-->", re.IGNORECASE)


def _read_text(path: Path) -> tuple[str, str]:
    """Read common markdown encodings without silently dropping characters."""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    # cp1252 accepts every byte, so this branch only documents the invariant.
    return data.decode("cp1252"), "cp1252"


def clean_display_title(title: str) -> str:
    """Normalize whitespace and harmless markdown wrappers for display/storage."""
    value = str(title or "").strip()
    value = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", value)
    value = value.strip().strip("`\"'")
    # A model often returns **Title** or *Title*. Keep punctuation that is
    # genuinely part of the movie title, but remove simple outer emphasis.
    value = re.sub(r"^(?:\*\*|__)(.*?)(?:\*\*|__)$", r"\1", value).strip()
    value = re.sub(r"^\*(.*?)\*$", r"\1", value).strip()
    return " ".join(value.split())


def _extract_watched_on(title: str) -> tuple[str, Optional[str]]:
    """Separate a valid watched-date annotation from a checklist title."""
    match = _WATCHED_ON_RE.search(title)
    if not match:
        return title, None
    clean_title = title[:match.start()].rstrip()
    try:
        return clean_title, date.fromisoformat(match.group("date")).isoformat()
    except ValueError:
        return clean_title, None


def _extract_note(title: str) -> tuple[str, Optional[str]]:
    """Separate a personal-note annotation from a checklist title."""
    match = _NOTE_RE.search(title)
    if not match:
        return title, None
    clean_title = (title[:match.start()] + title[match.end():]).strip()
    note = match.group("note").strip()
    return clean_title, note or None


def extract_title_year(title: str) -> Optional[int]:
    match = _YEAR_RE.search(clean_display_title(title))
    return int(match.group("year")) if match else None


def strip_release_year(title: str) -> str:
    return _YEAR_RE.sub("", clean_display_title(title)).strip()




def _title_segments(title: str) -> list[str]:
    """Split a bilingual slash title into individual matchable segments.

    'Bicycle Thieves / Ladri di biciclette (1948)' becomes:
      ['Bicycle Thieves / Ladri di biciclette (1948)',
       'Bicycle Thieves (1948)',
       'Ladri di biciclette (1948)']

    A plain title with no slash returns a single-element list.
    """
    base = strip_release_year(title)
    year = extract_title_year(title)
    year_suffix = f" ({year})" if year else ""
    parts = [p.strip() for p in re.split(r"\s*/\s*", base) if p.strip()]
    if len(parts) <= 1:
        return [title]
    return [title] + [f"{part}{year_suffix}" for part in parts]

def normalized_title_key(title: str) -> str:
    """Return a punctuation/diacritic-insensitive movie-title key.

    The year is intentionally excluded. Code comparing two titles must also
    use :func:`titles_equivalent`, which keeps distinct remakes apart when both
    release years are present.
    """
    value = strip_release_year(title)
    value = value.replace("&", " and ")
    value = value.replace("’", "'").replace("‘", "'")
    value = value.replace("–", "-").replace("—", "-")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold()
    # Initialisms and contractions are normally written with punctuation in
    # one source and without it in another: ``L.A.`` == ``LA`` and
    # ``Who's`` == ``Whos`` for identity purposes.
    value = value.replace(".", "").replace("'", "")
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def title_identity(title: str) -> tuple[str, Optional[int]]:
    return normalized_title_key(title), extract_title_year(title)


def titles_equivalent(left: str, right: str) -> bool:
    """Compare titles safely across case, punctuation, accents, and years."""
    left_key, left_year = title_identity(left)
    right_key, right_year = title_identity(right)
    if not left_key or left_key != right_key:
        return False
    # ``Suspiria (1977)`` and ``Suspiria (2018)`` are not duplicates. A bare
    # title can match a dated title unless the caller has multiple remakes to
    # disambiguate.
    return not (left_year is not None and right_year is not None and left_year != right_year)


def _reference_candidates(reference: str) -> list[str]:
    """Generate conservative title candidates from imperfect AI/user text."""
    value = clean_display_title(reference)
    candidates = [value]
    for prefix in ("title:", "film:", "movie:", "pick:", "choice:"):
        if value.casefold().startswith(prefix):
            candidates.append(clean_display_title(value[len(prefix):]))
    # Numbered markdown responses such as ``1. **Persona (1966)**``.
    candidates.append(clean_display_title(re.sub(r"^\s*(?:#?\d+\s*[.)\]:-]|[-*])\s*", "", value)))
    # A response may include a title followed by a dash and explanation.
    if " — " in value:
        candidates.append(clean_display_title(value.split(" — ", 1)[0]))
    if " - " in value:
        candidates.append(clean_display_title(value.split(" - ", 1)[0]))
    return [candidate for candidate in candidates if candidate]


def resolve_movie_reference(reference: object, movies: Sequence["Movie"]) -> Optional["Movie"]:
    """Resolve a model/user title to one unambiguous local movie.

    This never invents a movie. It only returns an item from ``movies`` and
    refuses fuzzy matches when two candidates are equally plausible.
    """
    if not isinstance(reference, str) or not reference.strip():
        return None

    for candidate in _reference_candidates(reference):
        exact = [movie for movie in movies if titles_equivalent(candidate, movie.title)]
        if len(exact) == 1:
            return exact[0]

        key = normalized_title_key(candidate)
        if len(key) < 4:
            continue
        partial = [
            movie
            for movie in movies
            if key in normalized_title_key(movie.title)
            or normalized_title_key(movie.title) in key
        ]
        if len(partial) == 1:
            return partial[0]
    return None


@dataclass
class Movie:
    title: str
    watched: bool
    genre: str
    raw_line: str
    watched_on: Optional[str] = None
    note: Optional[str] = None

    @property
    def clean_title(self) -> str:
        return strip_release_year(self.title)

    @property
    def year(self) -> Optional[int]:
        return extract_title_year(self.title)

    @property
    def sort_key(self) -> str:
        return normalized_title_key(self.title)

    def to_line(self) -> str:
        marker = "x" if self.watched else " "
        annotation = f" <!-- watched_on: {self.watched_on} -->" if self.watched and self.watched_on else ""
        note = f" <!-- note: {self.note} -->" if self.note else ""
        return f"- [{marker}] {self.title}{annotation}{note}"


@dataclass
class GenreBlock:
    header: str
    entries: list[Movie] = field(default_factory=list)

    @property
    def name(self) -> str:
        short = re.split(r"\s*[—(]", self.header)[0].strip()
        return short or self.header

    @property
    def watched_count(self) -> int:
        return sum(1 for movie in self.entries if movie.watched)

    @property
    def total(self) -> int:
        return len(self.entries)


@dataclass
class ImportEntry:
    """One entry from a source file that is not necessarily an archive."""

    title: str
    watched: bool = False
    genre: Optional[str] = None
    line_number: int = 0
    raw_line: str = ""
    watched_on: Optional[str] = None


@dataclass
class ImportSource:
    path: Path
    entries: list[ImportEntry]
    format: str
    encoding: str


def _markdown_table_cells(line: str) -> Optional[list[str]]:
    """Read a simple Markdown-table row while preserving escaped pipes."""
    stripped = line.strip()
    if "|" not in stripped:
        return None
    cells = re.split(r"(?<!\\)\|", stripped.strip("|"))
    return [cell.strip().replace(r"\|", "|") for cell in cells]


def _export_entries(lines: Sequence[str]) -> Optional[list[ImportEntry]]:
    """Parse the Markdown table produced by ``grimwatch export``."""
    headers: Optional[list[str]] = None
    header_index = -1
    for index, line in enumerate(lines):
        cells = _markdown_table_cells(line)
        if cells is None:
            continue
        normalized = [cell.casefold() for cell in cells]
        if normalized in (
            ["status", "title", "shelf", "year"],
            ["status", "title", "shelf", "year", "watched on"],
        ):
            headers = normalized
            header_index = index
            break
    if headers is None:
        return None

    entries: list[ImportEntry] = []
    for line_number, line in enumerate(lines[header_index + 1 :], header_index + 2):
        cells = _markdown_table_cells(line)
        if cells is None:
            if entries:
                break
            continue
        if len(cells) != len(headers) or all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        status, title, genre, year, *metadata = cells
        if status.casefold() not in {"watched", "unwatched"}:
            continue
        title = clean_display_title(title)
        if not title:
            continue
        if extract_title_year(title) is None and re.fullmatch(r"(?:18|19|20)\d{2}", year):
            title = f"{title} ({year})"
        watched = status.casefold() == "watched"
        watched_on: Optional[str] = None
        if watched and metadata and metadata[0]:
            try:
                watched_on = date.fromisoformat(metadata[0]).isoformat()
            except ValueError:
                pass
        entries.append(
            ImportEntry(
                title=title,
                watched=watched,
                genre=clean_display_title(genre) or None,
                line_number=line_number,
                raw_line=line,
                watched_on=watched_on,
            )
        )
    return entries


def _header_from_line(line: str) -> Optional[str]:
    stripped = line.strip()
    if not stripped or stripped == "---" or stripped.startswith("<!--") or _FENCE_RE.match(stripped):
        return None
    heading = _MARKDOWN_HEADING_RE.match(stripped)
    return clean_display_title(heading.group("title") if heading else stripped) or None


def _plain_entry_from_line(line: str) -> Optional[str]:
    stripped = line.strip()
    if not stripped or stripped == "---" or stripped.startswith(("#", "<!--", "```")):
        return None
    match = _BULLET_RE.match(stripped)
    title = clean_display_title(match.group("title") if match else stripped)
    # A trailing colon is overwhelmingly a section label in practical import
    # files, not a title.
    if not title or title.endswith(":"):
        return None
    return title


def _bare_section_label(lines: Sequence[str], index: int) -> bool:
    """Recognize the common ``HEADER`` + ``- Film (Year)`` import shape.

    A raw list is intentionally permissive, so a bare line normally remains a
    film title.  The narrow exception below catches a section label that has
    no Markdown marker or trailing colon but is immediately followed by a
    conventional bullet entry.  It does not apply to a bullet/checkbox line
    itself, and therefore does not reinterpret ordinary one-title-per-line
    imports as headings.
    """
    line = lines[index]
    if _BULLET_RE.match(line) or _CHECKBOX_RE.match(line):
        return False
    title = _plain_entry_from_line(line)
    if not title or extract_title_year(title) is not None or index + 1 >= len(lines):
        return False
    return _BULLET_RE.match(lines[index + 1]) is not None


def read_import_source(path: str | Path) -> ImportSource:
    """Read categorized checklists and plain title lists from one code path."""
    resolved = Path(path).expanduser()
    if not resolved.exists() or not resolved.is_file():
        raise FileNotFoundError(f"File not found: {resolved}")
    text, encoding = _read_text(resolved)
    lines = text.splitlines()
    exported_entries = _export_entries(lines)
    if exported_entries is not None:
        return ImportSource(
            path=resolved.resolve(), entries=exported_entries, format="grimwatch-export", encoding=encoding
        )
    has_checkboxes = any(_CHECKBOX_RE.match(line) for line in lines)
    entries: list[ImportEntry] = []

    if has_checkboxes:
        current_header: Optional[str] = None
        for line_number, line in enumerate(lines, 1):
            checkbox = _CHECKBOX_RE.match(line)
            if checkbox:
                raw_title, _note = _extract_note(checkbox.group("title"))
                raw_title, watched_on = _extract_watched_on(raw_title)
                title = clean_display_title(raw_title)
                if title:
                    entries.append(
                        ImportEntry(
                            title=title,
                            watched=checkbox.group("mark").casefold() == "x",
                            genre=current_header,
                            line_number=line_number,
                            raw_line=line,
                            watched_on=watched_on if checkbox.group("mark").casefold() == "x" else None,
                        )
                    )
                continue
            header = _header_from_line(line)
            if header:
                current_header = header
        format_name = "checklist"
    else:
        for line_number, line in enumerate(lines, 1):
            if _bare_section_label(lines, line_number - 1):
                continue
            title = _plain_entry_from_line(line)
            if title:
                entries.append(ImportEntry(title=title, line_number=line_number, raw_line=line))
        format_name = "plain"

    return ImportSource(path=resolved.resolve(), entries=entries, format=format_name, encoding=encoding)


def coalesce_import_entries(entries: Iterable[ImportEntry]) -> tuple[list[ImportEntry], int]:
    """Deduplicate a source while preserving a watched flag and useful shelf."""
    unique: list[ImportEntry] = []
    duplicate_count = 0
    for entry in entries:
        match = next(
            (
                item for item in unique
                if any(
                    titles_equivalent(seg_a, seg_b)
                    for seg_a in _title_segments(item.title)
                    for seg_b in _title_segments(entry.title)
                )
            ),
            None,
        )
        if match is None:
            unique.append(
                ImportEntry(
                    title=clean_display_title(entry.title),
                    watched=bool(entry.watched),
                    genre=entry.genre.strip() if entry.genre else None,
                    line_number=entry.line_number,
                    raw_line=entry.raw_line,
                    watched_on=entry.watched_on if entry.watched else None,
                )
            )
            continue
        duplicate_count += 1
        match.watched = match.watched or entry.watched
        if entry.watched_on and (not match.watched_on or entry.watched_on > match.watched_on):
            match.watched_on = entry.watched_on
        if not match.genre and entry.genre:
            match.genre = entry.genre.strip()
    return unique, duplicate_count


def find_equivalent_movie(title: str, movies: Sequence[Movie]) -> Optional[Movie]:
    """Find an unambiguous archive match for an incoming title.

    Checks every slash-separated segment of each archive title so that
    'Bicycle Thieves (1948)' matches 'Bicycle Thieves / Ladri di biciclette (1948)'.
    """
    matches = [
        movie for movie in movies
        if any(titles_equivalent(title, seg) for seg in _title_segments(movie.title))
    ]
    if len(matches) == 1:
        return matches[0]

    requested_year = extract_title_year(title)
    if requested_year is not None:
        dated = [movie for movie in matches if movie.year == requested_year]
        if len(dated) == 1:
            return dated[0]
    return None


def duplicate_groups(movies: Sequence[Movie]) -> list[list[Movie]]:
    """Return only safe duplicate groups; remakes with different years stay apart.

    A movie stored as 'La Bataille d'Alger / The Battle of Algiers (1966)' is
    indexed under every slash-segment key, so it collides with a plain
    'The Battle of Algiers (1966)' entry stored elsewhere in the archive.
    """
    # Map each canonical segment key → list of movies that contain that segment.
    by_key: dict[str, list[Movie]] = {}
    for movie in movies:
        seen_keys: set[str] = set()
        for seg in _title_segments(movie.title):
            key = normalized_title_key(seg)
            if key and key not in seen_keys:
                seen_keys.add(key)
                by_key.setdefault(key, []).append(movie)

    # Union-find: merge movies that share any segment key into one component.
    # We use id(movie) as the node identifier to avoid title-equality issues.
    parent: dict[int, Movie] = {id(m): m for m in movies}

    def find(m: Movie) -> Movie:
        root = parent[id(m)]
        while id(root) != id(parent[id(root)]):
            root = parent[id(root)]
        # Path compression
        parent[id(m)] = root
        return root

    def union(a: Movie, b: Movie) -> None:
        ra, rb = find(a), find(b)
        if id(ra) != id(rb):
            parent[id(rb)] = ra

    for candidates in by_key.values():
        if len(candidates) < 2:
            continue
        for i in range(1, len(candidates)):
            union(candidates[0], candidates[i])

    # Collect components with more than one member.
    components: dict[int, list[Movie]] = {}
    for movie in movies:
        root = find(movie)
        components.setdefault(id(root), []).append(movie)

    groups: list[list[Movie]] = []
    for component in components.values():
        if len(component) < 2:
            continue

        # Year-safety check: if every movie in the group has an explicit year
        # and not all years are the same, split by year (separate remakes).
        years = [m.year for m in component]
        if all(y is not None for y in years) and len(set(years)) > 1:
            # Different explicit years → treat each year-bucket independently.
            by_year: dict[int, list[Movie]] = {}
            for m in component:
                by_year.setdefault(m.year, []).append(m)  # type: ignore[arg-type]
            for bucket in by_year.values():
                if len(bucket) > 1:
                    groups.append(bucket)
        else:
            groups.append(component)

    return groups


@dataclass
class MergePlan:
    """A fully parsed, non-mutating comparison between an import and archive."""

    source_entries: list[ImportEntry]
    source_duplicates: int
    new_entries: list[ImportEntry]
    status_updates: list[tuple[Movie, ImportEntry]]
    already_present: list[ImportEntry]


def build_merge_plan(entries: Iterable[ImportEntry], archive_movies: Sequence[Movie]) -> MergePlan:
    """Compare parsed input against an archive without changing either file."""
    source_entries, source_duplicates = coalesce_import_entries(entries)
    new_entries: list[ImportEntry] = []
    status_updates: list[tuple[Movie, ImportEntry]] = []
    already_present: list[ImportEntry] = []
    for entry in source_entries:
        existing = find_equivalent_movie(entry.title, archive_movies)
        if existing is None:
            new_entries.append(entry)
        elif entry.watched and not existing.watched:
            status_updates.append((existing, entry))
        else:
            already_present.append(entry)
    return MergePlan(
        source_entries=source_entries,
        source_duplicates=source_duplicates,
        new_entries=new_entries,
        status_updates=status_updates,
        already_present=already_present,
    )


def write_classified_markdown(
    source_path: str | Path,
    entries: Sequence[ImportEntry],
    classifications: Sequence[dict],
    output_path: str | Path | None = None,
) -> Path:
    """Write a classified companion file; this never writes the main archive."""
    if len(entries) != len(classifications):
        raise ValueError("Each imported entry must have exactly one classification.")
    source = Path(source_path).expanduser().resolve()
    output = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else source.with_name(f"{source.stem}.classified.md")
    )
    if output == source:
        raise ValueError("Classification output must be a new companion file, not the source file.")
    grouped: dict[str, list[tuple[ImportEntry, dict]]] = {}
    for entry, classification in zip(entries, classifications):
        genre = clean_display_title(classification.get("genre_header", "")) or "UNCATEGORIZED IMPORTS"
        grouped.setdefault(genre, []).append((entry, classification))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.grimwatch.tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        for index, (genre, rows) in enumerate(grouped.items()):
            if index:
                handle.write("\n")
            handle.write(f"{genre}\n\n")
            for entry, _classification in rows:
                marker = "x" if entry.watched else " "
                handle.write(f"- [{marker}] {clean_display_title(entry.title)}\n")
    temporary.replace(output)
    return output


class MovieList:
    """The configured categorized archive. Plain lists belong to ImportSource."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.genres: list[GenreBlock] = []
        self._genre_index: dict[str, GenreBlock] = {}
        self._load()

    def _load(self) -> None:
        self.genres = []
        self._genre_index = {}
        if not self.path.exists() or not self.path.is_file():
            return
        text, _encoding = _read_text(self.path)
        current: Optional[GenreBlock] = None
        for line in text.splitlines():
            checkbox = _CHECKBOX_RE.match(line)
            if checkbox:
                if current is None:
                    # A malformed archive entry still remains accessible rather
                    # than being discarded just because its heading is absent.
                    current = self._append_genre("UNCATEGORIZED")
                raw_title, note = _extract_note(checkbox.group("title"))
                raw_title, watched_on = _extract_watched_on(raw_title)
                title = clean_display_title(raw_title)
                if title:
                    current.entries.append(
                        Movie(
                            title=title,
                            watched=checkbox.group("mark").casefold() == "x",
                            genre=current.header,
                            raw_line=line,
                            watched_on=watched_on if checkbox.group("mark").casefold() == "x" else None,
                            note=note,
                        )
                    )
                continue
            header = _header_from_line(line)
            if header:
                current = self._append_genre(header)

    def _append_genre(self, header: str) -> GenreBlock:
        block = GenreBlock(header=header)
        self.genres.append(block)
        self._genre_index.setdefault(header.casefold().strip(), block)
        return block

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f".{self.path.name}.grimwatch.tmp")
        with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
            for index, block in enumerate(self.genres):
                if index:
                    handle.write("\n")
                handle.write(block.header + "\n\n")
                for movie in block.entries:
                    handle.write(movie.to_line() + "\n")
        temp_path.replace(self.path)

    def reload(self) -> None:
        self._load()

    def all_movies(self) -> list[Movie]:
        return [movie for block in self.genres for movie in block.entries]

    def unwatched(self) -> list[Movie]:
        return [movie for movie in self.all_movies() if not movie.watched]

    def watched(self) -> list[Movie]:
        return [movie for movie in self.all_movies() if movie.watched]

    def find(self, query: str) -> list[Movie]:
        query_key = normalized_title_key(query)
        raw_query = " ".join(str(query).casefold().split())
        if not query_key and not raw_query:
            return []
        results: list[Movie] = []
        for movie in self.all_movies():
            fields = (movie.title, movie.clean_title, movie.genre)
            if any(
                (query_key and query_key in normalized_title_key(field))
                or (raw_query and raw_query in " ".join(field.casefold().split()))
                for field in fields
            ):
                results.append(movie)
        return results

    def find_one(self, query: str) -> Optional[Movie]:
        resolved = resolve_movie_reference(query, self.all_movies())
        if resolved is not None:
            return resolved
        hits = self.find(query)
        return hits[0] if hits else None

    def genre_for_header(self, header: str) -> Optional[GenreBlock]:
        if not header:
            return None
        block = self._genre_index.get(header.casefold().strip())
        if block is not None:
            return block
        target = " ".join(header.casefold().split())
        return next(
            (block for block in self.genres if " ".join(block.header.casefold().split()) == target),
            None,
        )

    def find_genre_fuzzy(self, query: str) -> Optional[GenreBlock]:
        query_key = " ".join(str(query).casefold().split())
        if not query_key:
            return None
        best: Optional[GenreBlock] = None
        best_score = 0
        for block in self.genres:
            header = block.header.casefold()
            name = block.name.casefold()
            tokens = query_key.split()
            score = 0
            if query_key == name:
                score = 1000
            elif query_key in name:
                score = 500 + len(query_key)
            elif query_key in header:
                score = 200 + len(query_key)
            elif all(token in header for token in tokens):
                score = 100 + sum(map(len, tokens))
            if score > best_score:
                best_score = score
                best = block
        return best

    def genre_headers(self) -> list[str]:
        return [block.header for block in self.genres]

    def mark_watched(self, movie: Movie, watched: bool = True) -> None:
        if watched:
            movie.watched = True
            movie.watched_on = movie.watched_on or date.today().isoformat()
        else:
            movie.watched = False
            movie.watched_on = None

    def set_note(self, movie: Movie, note: str) -> None:
        """Store a short personal note on a watched movie."""
        movie.note = note.strip()[:200]

    def add_movie(
        self, title: str, genre_header: str, watched: bool = False, watched_on: Optional[str] = None
    ) -> Movie:
        clean_title = clean_display_title(title)
        genre_header = clean_display_title(genre_header) or "UNCATEGORIZED"
        if not clean_title:
            raise ValueError("Movie title cannot be empty.")
        block = self.genre_for_header(genre_header)
        if block is None:
            block = self._append_genre(genre_header)
        if watched and watched_on:
            try:
                watched_on = date.fromisoformat(watched_on).isoformat()
            except ValueError:
                watched_on = None
        else:
            watched_on = None
        movie = Movie(
            title=clean_title,
            watched=watched,
            genre=block.header,
            raw_line=f'- [{"x" if watched else " "}] {clean_title}',
            watched_on=watched_on,
        )
        block.entries.append(movie)
        return movie

    def remove_movie(self, movie: Movie) -> None:
        for block in self.genres:
            if movie in block.entries:
                block.entries.remove(movie)
                return

    def total_count(self) -> int:
        return sum(block.total for block in self.genres)

    def watched_count(self) -> int:
        return sum(block.watched_count for block in self.genres)

    def decade_breakdown(self) -> dict[str, dict[str, int]]:
        decades: dict[str, dict[str, int]] = {}
        for movie in self.all_movies():
            decade = f"{(movie.year // 10) * 10}s" if movie.year else "Unknown"
            data = decades.setdefault(decade, {"total": 0, "watched": 0})
            data["total"] += 1
            if movie.watched:
                data["watched"] += 1
        return dict(sorted(decades.items()))

    def normalize_title(self, line: str) -> Optional[str]:
        checkbox = _CHECKBOX_RE.match(line)
        return normalized_title_key(checkbox.group("title")) if checkbox else None
