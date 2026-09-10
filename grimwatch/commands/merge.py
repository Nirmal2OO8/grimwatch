"""Merge a parsed import source into the configured markdown archive."""

from __future__ import annotations

from pathlib import Path

from rich import box
from rich.prompt import Confirm
from rich.table import Table

from ..ai import UNCATEGORIZED_IMPORTS, classify_movies
from ..config import get_config
from ..parser import ImportEntry, MovieList, build_merge_plan, read_import_source
from ..ui import console, empty_state, header, short_genre, success, unquote, warn


def _classify_new_entries(cfg: dict, entries: list[ImportEntry]) -> list[dict]:
    """Classify only incoming uncategorized entries, never the main archive."""
    result_by_line: dict[int, dict] = {}
    needs_classification = [entry for entry in entries if not entry.genre]
    if needs_classification:
        if cfg.get("groq_api_key"):
            with console.status(f"  [cyan]Classifying {len(needs_classification)} incoming film(s)…[/]"):
                classified = classify_movies(
                    cfg["groq_api_key"],
                    cfg["groq_model"],
                    [entry.title for entry in needs_classification],
                    tmdb_api_key=cfg.get("tmdb_api_key"),
                    omdb_api_key=cfg.get("omdb_api_key"),
                )
            for entry, result in zip(needs_classification, classified):
                result_by_line[entry.line_number] = result
        else:
            warn("No Groq key: uncategorized incoming films will be kept under UNCATEGORIZED IMPORTS.")
            for entry in needs_classification:
                result_by_line[entry.line_number] = {
                    "title": entry.title,
                    "genre_header": UNCATEGORIZED_IMPORTS,
                    "reason": "No AI key was configured.",
                    "needs_review": True,
                }

    prepared: list[dict] = []
    for entry in entries:
        if entry.genre:
            prepared.append(
                {
                    "title": entry.title,
                    "genre_header": entry.genre,
                    "reason": "Shelf preserved from the imported checklist.",
                    "needs_review": False,
                }
            )
        else:
            prepared.append(result_by_line[entry.line_number])
        prepared[-1]["watched"] = entry.watched
    return prepared


def run_merge(file: str, yes: bool) -> None:
    source_value = unquote(file)
    cfg = get_config()
    source_path = Path(source_value).expanduser()
    if not source_path.exists() or not source_path.is_file():
        console.print(f"[red]File not found:[/] {source_value}")
        return

    try:
        source = read_import_source(source_path)
    except OSError as exc:
        console.print(f"[red]Could not read source:[/] {exc}")
        return
    archive = MovieList(cfg["list_path"])
    if source.path == archive.path.resolve():
        console.print("[red]Cannot merge the archive into itself.[/]")
        return

    plan = build_merge_plan(source.entries, archive.all_movies())
    header("MERGE PREVIEW", f"source: {source.path.name}  //  {source.format} {source.encoding}")
    console.print(f"  Parsed entries:       [cyan]{len(plan.source_entries)}[/]")
    console.print(f"  New films to add:     [green]{len(plan.new_entries)}[/]")
    console.print(f"  Watch status updates: [yellow]{len(plan.status_updates)}[/]")
    console.print(f"  Already present:      [dim]{len(plan.already_present)}[/]")
    if plan.source_duplicates:
        console.print(f"  Duplicate source rows: [dim]{plan.source_duplicates} (coalesced)[/]" )
    console.print()

    if not plan.source_entries:
        empty_state("NO FILM ENTRIES FOUND", "The source file was readable but contained no checklist or plain title entries.")
        return
    if not plan.new_entries and not plan.status_updates:
        empty_state("NOTHING TO MERGE", "Every incoming film is already represented in the archive.")
        return

    if plan.new_entries:
        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="reelq.accent", padding=(0, 1))
        table.add_column("FILM", style="reelq.title")
        table.add_column("SHELF", style="reelq.violet")
        table.add_column("WATCHED", style="reelq.success")
        for entry in plan.new_entries[:20]:
            shelf = short_genre(entry.genre) if entry.genre else "will classify"
            table.add_row(entry.title, shelf, "✓" if entry.watched else "")
        if len(plan.new_entries) > 20:
            table.add_row(f"[dim]… and {len(plan.new_entries) - 20} more[/]", "", "")
        console.print(table)
        console.print()

    if not yes and not Confirm.ask("  Proceed with merge?", default=True, console=console):
        console.print("[dim]Cancelled.[/]")
        return

    prepared_entries = _classify_new_entries(cfg, plan.new_entries)
    for entry in prepared_entries:
        archive.add_movie(entry["title"], entry["genre_header"], entry["watched"])

    updated = 0
    for existing, incoming in plan.status_updates:
        if yes or Confirm.ask(
            f"  Mark [bold]{existing.title}[/] watched because the source marks it watched?", console=console, default=True
        ):
            archive.mark_watched(existing, True)
            updated += 1

    archive.save()
    success(f"Added {len(prepared_entries)} film(s), updated {updated} watch status(es).")
