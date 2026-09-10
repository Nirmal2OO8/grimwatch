"""Add one title or import a source list into the configured archive."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich import box
from rich.prompt import Confirm, Prompt
from rich.table import Table

from ..ai import GENRES, classify_movies, enrich_years
from ..config import get_config
from ..parser import (
    ImportEntry,
    MovieList,
    coalesce_import_entries,
    extract_title_year,
    find_equivalent_movie,
    read_import_source,
)
from ..ui import console, error, header, short_genre, success, unquote, warn


def _source_entries(input_value: str) -> tuple[list[ImportEntry], Optional[Path], int]:
    """Treat an existing file as an import source; otherwise it is one title."""
    path = Path(input_value).expanduser()
    if path.exists():
        if not path.is_file():
            raise ValueError(f"Not a file: {path}")
        source = read_import_source(path)
        entries, source_duplicates = coalesce_import_entries(source.entries)
        return entries, source.path, source_duplicates
    return [ImportEntry(title=input_value)], None, 0


def _enrich_titles(cfg: dict, entries: list[ImportEntry]) -> list[ImportEntry]:
    missing_years = [entry.title for entry in entries if extract_title_year(entry.title) is None]
    if not missing_years or not cfg.get("groq_api_key"):
        return entries
    try:
        with console.status(f"  [dim]Looking up years for {len(missing_years)} title(s)…[/]"):
            years = enrich_years(cfg["groq_api_key"], cfg["groq_model"], missing_years)
    except Exception as exc:
        warn(f"Year lookup skipped: {exc}")
        return entries

    enriched: list[ImportEntry] = []
    for entry in entries:
        year = years.get(entry.title)
        title = f"{entry.title} ({year})" if year and extract_title_year(entry.title) is None else entry.title
        enriched.append(
            ImportEntry(
                title=title,
                watched=entry.watched,
                genre=entry.genre,
                line_number=entry.line_number,
                raw_line=entry.raw_line,
                watched_on=entry.watched_on,
            )
        )
    return enriched


def _classify_entries(cfg: dict, entries: list[ImportEntry], force_genre: Optional[str], archive: MovieList) -> list[dict]:
    """Classify only source entries. The archive is used solely as a destination."""
    if force_genre:
        block = archive.find_genre_fuzzy(force_genre)
        header_name = block.header if block else force_genre.strip()
        return [
            {
                "title": entry.title,
                "genre_header": header_name,
                "reason": "Manually specified.",
                "watched": entry.watched,
                "watched_on": entry.watched_on,
            }
            for entry in entries
        ]

    categorized = [entry for entry in entries if entry.genre]
    uncategorized = [entry for entry in entries if not entry.genre]
    by_title: dict[str, list[dict]] = {}
    if uncategorized:
        if not cfg.get("groq_api_key"):
            raise ValueError("No Groq API key. Use --genre or run grimwatch setup to classify a raw list.")
        with console.status(f"  [cyan]Classifying {len(uncategorized)} source film(s)…[/]"):
            classifications = classify_movies(
                cfg["groq_api_key"],
                cfg["groq_model"],
                [entry.title for entry in uncategorized],
                tmdb_api_key=cfg.get("tmdb_api_key"),
                omdb_api_key=cfg.get("omdb_api_key"),
            )
        for classification in classifications:
            by_title.setdefault(classification["title"], []).append(classification)

    results: list[dict] = []
    for entry in entries:
        if entry.genre:
            results.append(
                {
                    "title": entry.title,
                    "genre_header": entry.genre,
                    "reason": "Shelf preserved from the imported checklist.",
                    "watched": entry.watched,
                    "watched_on": entry.watched_on,
                }
            )
            continue
        classification = by_title[entry.title].pop(0)
        results.append({**classification, "watched": entry.watched, "watched_on": entry.watched_on})
    return results


def run_add(input: str, force_genre: Optional[str], yes: bool) -> None:
    input_value = unquote(input)
    if not input_value:
        error("Enter a title or a path to an import file.")
        return
    cfg = get_config()
    archive = MovieList(cfg["list_path"])
    header("ADD TO THE ARCHIVE", "classify a new reel")

    try:
        incoming, source_path, source_duplicates = _source_entries(input_value)
    except (OSError, ValueError) as exc:
        error(str(exc))
        return
    if source_path:
        console.print(f"\n  [dim]Read {len(incoming)} unique title(s) from [bold]{source_path.name}[/].[/]")
    if source_duplicates:
        warn(f"Collapsed {source_duplicates} duplicate row(s) inside the source file.")
    if not incoming:
        error("No film entries were found in that file.")
        return

    new_entries: list[ImportEntry] = []
    already_present = 0
    current_movies = archive.all_movies()
    for entry in incoming:
        if find_equivalent_movie(entry.title, current_movies):
            already_present += 1
        else:
            new_entries.append(entry)
    if already_present:
        warn(f"Skipping {already_present} title(s) already in your archive.")
    if not new_entries:
        warn("Nothing new to add.")
        return

    new_entries = _enrich_titles(cfg, new_entries)
    try:
        results = _classify_entries(cfg, new_entries, force_genre, archive)
    except Exception as exc:
        error(f"Could not prepare this import: {exc}")
        console.print("  [dim]Nothing was written.[/]")
        return

    console.print()
    table = Table(box=box.ROUNDED, border_style="#b58cff", padding=(0, 1), expand=True)
    table.add_column("#", style="reelq.dim", width=3, justify="right")
    table.add_column("FILM", style="reelq.title", ratio=2)
    table.add_column("SHELF", style="reelq.violet", ratio=2)
    table.add_column("WHY", style="reelq.muted", ratio=3)
    for index, result in enumerate(results, 1):
        table.add_row(str(index), result["title"], short_genre(result["genre_header"]), result.get("reason", ""))
    console.print(table)
    console.print()

    if not yes and len(results) > 1:
        fix = Confirm.ask("  Fix a classification before adding?", default=False, console=console)
        if fix:
            number = Prompt.ask("  Which number", choices=[str(i) for i in range(1, len(results) + 1)], console=console)
            selected = results[int(number) - 1]
            for index, genre in enumerate(GENRES, 1):
                console.print(f"  [dim]{index:2}.[/] {short_genre(genre)}")
            genre_number = Prompt.ask("  Pick genre number", choices=[str(i) for i in range(1, len(GENRES) + 1)], console=console)
            selected["genre_header"] = GENRES[int(genre_number) - 1]
            selected["reason"] = "Manually corrected."

    if not yes and not Confirm.ask(f"  Add {len(results)} film(s)?", default=True, console=console):
        console.print("  [dim]Cancelled.[/]")
        return

    for result in results:
        archive.add_movie(
            result["title"],
            result["genre_header"],
            bool(result.get("watched")),
            result.get("watched_on"),
        )
    archive.save()
    console.print()
    success(f"Added {len(results)} film(s) to your archive.")
    console.print()
