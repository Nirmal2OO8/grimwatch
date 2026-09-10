"""grimwatch list — browse genres, pick to mark watched."""

from typing import Optional
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich import box
from ..config import get_config
from ..parser import MovieList
from ..ui import console, success, warn, short_genre, WATCHED_ICON, UNWATCHED_ICON, progress_bar, header, empty_state


def run_list(genre: Optional[str], unwatched_only: bool, watched_only: bool):
    cfg = get_config()
    ml = MovieList(cfg["list_path"])

    blocks = ml.genres

    if genre:
        block = ml.find_genre_fuzzy(genre)
        if not block:
            warn(f"No genre matching: {genre}")
            return
        blocks = [block]

    all_shown = []
    header("YOUR SHELVES", short_genre(blocks[0].header) if genre and blocks else "browse, pick, watch")

    for block in blocks:
        entries = list(block.entries)
        if unwatched_only:
            entries = [m for m in entries if not m.watched]
        elif watched_only:
            entries = [m for m in entries if m.watched]
        if not entries:
            continue

        bar = progress_bar(block.watched_count, block.total, width=16)
        t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1), expand=True)
        t.add_column("#", style="reelq.dim", width=4, justify="right")
        t.add_column("", width=2)
        t.add_column("Title")

        offset = len(all_shown)
        for i, m in enumerate(entries, offset + 1):
            dot = WATCHED_ICON if m.watched else UNWATCHED_ICON
            style = "reelq.dim" if m.watched else "reelq.bone"
            t.add_row(str(i), dot, f"[{style}]{m.title}[/]")
            all_shown.append(m)

        console.print(Panel(
            t,
            title=f"[bold #b58cff]{short_genre(block.header)}[/]  {bar}  [reelq.muted]{block.watched_count}/{block.total}[/]",
            border_style="#b58cff",
            padding=(0, 1),
        ))
        console.print()

    if not all_shown:
        empty_state("NO FILMS HERE", "Try removing a filter or add a title with grimwatch add.")
        return

    # Numbered pick to toggle watched
    choices = [str(i) for i in range(1, len(all_shown) + 1)] + ["0"]
    pick = Prompt.ask(
        "  Pick a number to toggle watched [dim](0 to exit)[/]",
        console=console,
        choices=choices,
        default="0",
    )

    if pick == "0":
        return

    movie = all_shown[int(pick) - 1]
    new_state = not movie.watched
    label = "watched" if new_state else "unwatched"
    ml.mark_watched(movie, new_state)
    ml.save()
    success(f"Marked {label}: [bold]{movie.title}[/]")
    console.print()
