from __future__ import annotations
from typing import Optional
from rich import box
from rich.prompt import Confirm, Prompt
from rich.table import Table

from ..config import get_config
from ..parser import Movie, MovieList, duplicate_groups
from ..ui import console, empty_state, header, short_genre, success


def _keep_movie(movies: list[Movie]) -> Movie:
    """Use a deterministic choice only for --yes and --dry-run."""
    return next((movie for movie in movies if movie.watched), movies[0])


def _choose_keep_movies(groups: list[list[Movie]]) -> list[Movie | None] | None:
    """Let the user select one kept row per group, or explicitly skip it."""
    keepers: list[Movie | None] = []
    console.print("  [reelq.dim]Choose the copy to keep in each group. Skip leaves that group unchanged.[/]")
    for group_number, group in enumerate(groups, 1):
        console.print()
        table = Table(
            title=f"Duplicate group {group_number}",
            box=box.SIMPLE_HEAD,
            show_header=True,
            header_style="reelq.accent",
            padding=(0, 1),
        )
        table.add_column("#", justify="right", style="reelq.accent", width=4)
        table.add_column("FILM", style="reelq.title")
        table.add_column("SHELF", style="reelq.violet")
        table.add_column("WATCHED", style="reelq.success")
        for index, movie in enumerate(group, 1):
            table.add_row(str(index), movie.title, short_genre(movie.genre), "✓" if movie.watched else "")
        console.print(table)
        choices = [str(index) for index in range(1, len(group) + 1)] + ["s", "q"]
        choice = Prompt.ask(
            "  Keep which copy? [reelq.dim](s = skip group, q = cancel)[/]",
            console=console,
            choices=choices,
            default="s",
        ).casefold()
        if choice == "q":
            return None
        keepers.append(None if choice == "s" else group[int(choice) - 1])
    return keepers


def _render_plan(groups: list[list[Movie]], keepers: list[Movie | None]) -> tuple[Table, list[Movie]]:
    """Render the selected removal plan and return exactly its deletion targets."""
    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="reelq.accent", padding=(0, 1))
    table.add_column("FILM", style="reelq.title")
    table.add_column("SHELF", style="reelq.violet")
    table.add_column("WATCHED", style="reelq.success")
    table.add_column("ACTION", style="reelq.muted")
    to_remove: list[Movie] = []
    for group, keep in zip(groups, keepers):
        for movie in group:
            if keep is None:
                action = "[dim]SKIP[/]"
            elif movie is keep:
                action = "[green]KEEP[/]"
            else:
                action = "[red]REMOVE[/]"
                to_remove.append(movie)
            table.add_row(movie.title, short_genre(movie.genre), "✓" if movie.watched else "", action)
        table.add_row("", "", "", "")
    return table, to_remove


def run_dedup(yes: bool, dry_run: bool = False, output_path: Optional[str] = None) -> None:
    cfg = get_config()
    archive_path = cfg["list_path"]

    if output_path is None and not yes and not dry_run:
        use_default = Confirm.ask(
            f"  Write result to the default archive file?",
            console=console,
            default=True,
        )
        if not use_default:
            custom = Prompt.ask("  [reelq.accent]▸[/] Output path", console=console).strip().strip("\"'")
            if custom:
                output_path = custom

    archive = MovieList(archive_path)
    groups = duplicate_groups(archive.all_movies())
    if not groups:
        empty_state("NO DUPLICATES", "Your archive has no safely equivalent duplicate entries.")
        return

    header("DUPLICATE HUNT", f"{len(groups)} safe duplicate group(s) found")
    if yes or dry_run:
        keepers = [_keep_movie(group) for group in groups]
    else:
        keepers = _choose_keep_movies(groups)
        if keepers is None:
            console.print("[dim]Cancelled. No files were changed.[/]")
            return

    table, to_remove = _render_plan(groups, keepers)
    console.print(table)

    if dry_run:
        console.print(f"\n  [dim]Dry run: {len(to_remove)} duplicate(s) would be removed; no file was changed.[/]")
        return
    if not to_remove:
        console.print("\n  [dim]No duplicate rows selected for removal.[/]")
        return
    if not yes and not Confirm.ask(f"  Remove {len(to_remove)} duplicate(s)?", default=True, console=console):
        console.print("[dim]Cancelled.[/]")
        return
    for movie in to_remove:
        archive.remove_movie(movie)
    if output_path:
        from pathlib import Path
        dest = Path(output_path).expanduser()
        archive.path = dest
    archive.save()
    success(f"Removed {len(to_remove)} duplicate(s).")
