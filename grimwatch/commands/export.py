"""grimwatch export — write a portable Markdown table of the full archive."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..config import get_config
from ..parser import Movie, MovieList
from ..ui import error, success


def _markdown_cell(value: object) -> str:
    """Keep arbitrary archive text inside one Markdown table cell."""
    return " ".join(str(value).replace("|", "\\|").splitlines()).strip()


def _render_export(movies: list[Movie]) -> str:
    lines = [
        "# Grimwatch Export",
        "",
        "| Status | Title | Shelf | Year | Watched On |",
        "| --- | --- | --- | --- | --- |",
    ]
    for movie in movies:
        status = "Watched" if movie.watched else "Unwatched"
        year = str(movie.year) if movie.year else ""
        lines.append(
            f"| {status} | {_markdown_cell(movie.title)} | {_markdown_cell(movie.genre)} | {year} | {movie.watched_on or ''} |"
        )
    return "\n".join(lines) + "\n"


def run_export(output: Optional[str] = None) -> None:
    cfg = get_config()
    archive = MovieList(cfg["list_path"])
    destination = Path(output).expanduser() if output else archive.path.with_name("export.md")
    if destination.resolve() == archive.path.resolve():
        error("Export path cannot overwrite the archive.")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.grimwatch.tmp")
    try:
        temporary.write_text(_render_export(archive.all_movies()), encoding="utf-8", newline="\n")
        temporary.replace(destination)
    except OSError as exc:
        error(f"Could not write export: {exc}")
        return
    success(f"Exported {archive.total_count()} film(s) to {destination}")
