"""Classify an import/list file without reading or changing the main archive."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich import box
from rich.table import Table

from ..ai import GENRES, UNCATEGORIZED_IMPORTS, classify_movies
from ..config import get_config
from ..parser import coalesce_import_entries, read_import_source, write_classified_markdown
from ..ui import console, error, header, short_genre, success, unquote, warn


def _confidence_cell(value: object) -> str:
    """Render a compact confidence signal without treating it as a failure."""
    try:
        confidence = int(value)
    except (TypeError, ValueError):
        return "[reelq.dim]—[/]"
    if not 1 <= confidence <= 5:
        return "[reelq.dim]—[/]"
    style = "reelq.error" if confidence <= 2 else "reelq.warning" if confidence == 3 else "reelq.success"
    return f"[{style}]{confidence}/5[/]"


def _runner_up_cell(value: object) -> str:
    """Convert a validated genre number into its compact shelf label."""
    try:
        genre_number = int(value)
    except (TypeError, ValueError):
        return "[reelq.dim]—[/]"
    if not 1 <= genre_number <= len(GENRES):
        return "[reelq.dim]—[/]"
    return short_genre(GENRES[genre_number - 1])


def run_classify(
    file: str,
    write: bool = False,
    output: Optional[str] = None,
    force: bool = False,
    refresh_metadata: bool = False,
) -> None:
    """Classify only the supplied import/list source into a companion file."""
    source_value = unquote(file)
    source_path = Path(source_value).expanduser()
    if not source_path.exists() or not source_path.is_file():
        error(f"File not found: {source_value}")
        return

    cfg = get_config()
    archive_path = cfg.get("list_path")
    try:
        resolved_source = source_path.resolve()
    except OSError as exc:
        error(f"Could not resolve source path: {exc}")
        return
    if archive_path and resolved_source == Path(archive_path).expanduser().resolve():
        error("Classification only accepts an import/list file; the configured movies archive is never a classification source.")
        return
    try:
        source = read_import_source(resolved_source)
    except OSError as exc:
        error(f"Could not read source: {exc}")
        return
    # Classification is a read-only, source-preserving operation. A repeated
    # title may be intentional evidence for a later archive deduplication
    # review, so never silently remove it here. We only count equivalent rows
    # to explain what the preview contains.
    entries = source.entries
    _unique_entries, duplicate_rows = coalesce_import_entries(entries)
    if not entries:
        error("No film entries were found in that source file.")
        return
    if not cfg.get("groq_api_key"):
        error("No Groq API key. Run grimwatch setup before classifying an import file.")
        return

    header("CLASSIFY IMPORT", f"source: {source.path.name}  //  {source.format} {source.encoding}")
    if duplicate_rows:
        warn(
            f"Found {duplicate_rows} equivalent source row(s); preserving every row for preview/output. "
            "Archive deduplication remains a separate operation."
        )
    try:
        with console.status(f"  [cyan]Classifying {len(entries)} source film(s)…[/]"):
            results = classify_movies(
                cfg["groq_api_key"],
                cfg["groq_model"],
                [entry.title for entry in entries],
                tmdb_api_key=cfg.get("tmdb_api_key"),
                omdb_api_key=cfg.get("omdb_api_key"),
                refresh_metadata=refresh_metadata,
            )
    except Exception as exc:
        error(f"Could not classify the source file: {exc}")
        return
    table = Table(box=box.ROUNDED, border_style="#b58cff", padding=(0, 1), expand=True)
    table.add_column("#", style="reelq.dim", width=3, justify="right")
    table.add_column("FILM", style="reelq.title", ratio=2)
    table.add_column("CLASSIFIED SHELF", style="reelq.violet", ratio=2)
    table.add_column("CONF.", justify="center", width=6)
    table.add_column("RUNNER-UP", style="reelq.muted", min_width=9, no_wrap=True)
    table.add_column("WHY", style="reelq.muted", ratio=3)
    for index, result in enumerate(results, 1):
        table.add_row(
            str(index),
            result["title"],
            short_genre(result["genre_header"]),
            _confidence_cell(result.get("confidence")),
            _runner_up_cell(result.get("runner_up_genre_number")),
            result.get("reason", ""),
        )
    console.print(table)

    review_count = sum(bool(result.get("needs_review")) for result in results)
    if review_count:
        review_titles = [result["title"] for result in results if result.get("needs_review")]
        warn(f"{review_count} of {len(results)} film(s) need manual review:")
        for title in review_titles[:10]:
            console.print(f"    [reelq.dim]-[/] {title}")
        if len(review_titles) > 10:
            console.print(f"    [reelq.dim]… and {len(review_titles) - 10} more[/]")

    unresolved = [result["title"] for result in results if result.get("genre_header") == UNCATEGORIZED_IMPORTS]
    if unresolved:
        warn(
            f"{len(unresolved)} title(s) could not be classified."
        )
        for title in unresolved[:10]:
            console.print(f"    [reelq.dim]-[/] {title}")
        if len(unresolved) > 10:
            console.print(f"    [reelq.dim]… and {len(unresolved) - 10} more[/]")
        if write and not force:
            warn("No companion file was created. Re-run with --force to write unresolved titles under UNCATEGORIZED IMPORTS.")
            return

    low_confidence = [
        result["title"]
        for result in results
        if isinstance(result.get("confidence"), int) and result["confidence"] <= 2
    ]
    if low_confidence:
        warn(
            f"{len(low_confidence)} classification(s) have low model confidence; "
            "review their runner-up shelves before relying on them."
        )

    if not write:
        console.print("\n  [dim]Preview only. Add --write to create a classified companion Markdown file.[/]")
        return

    try:
        output_path = write_classified_markdown(source.path, entries, results, output)
    except OSError as exc:
        error(f"Could not write classified copy: {exc}")
        return
    success(f"Wrote classified import copy: {output_path}")
