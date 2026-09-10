"""grimwatch watch — mark watched/unwatched, then optionally suggest next."""

from rich.prompt import Confirm, Prompt
from ..config import get_config
from ..parser import MovieList
from ..ui import console, success, warn, error, pick_from, header, empty_state


def run_watch(title: str, undo: bool):
    cfg = get_config()
    ml = MovieList(cfg["list_path"])

    hits = ml.find(title)

    if not hits:
        empty_state("NO MATCH", f'Nothing matched "{title}". Try grimwatch search "{title}".')
        return

    movie = pick_from(hits, prompt="Multiple matches — pick a number")
    if not movie:
        return

    target = not undo
    header("WATCH LOG", "rewriting the reel")
    if movie.watched == target:
        state = "watched" if target else "unwatched"
        warn(f"Already marked as {state}: {movie.title}")
        return

    ml.mark_watched(movie, target)
    if target:
        note = Prompt.ask("  [reelq.dim]// Add a note? (optional)[/]", default="", console=console).strip()
        if note:
            ml.set_note(movie, note)
    ml.save()

    console.print()
    if target:
        success(f"[bold]{movie.title}[/]  marked watched")
        console.print()

        if Confirm.ask("  [reelq.dim]// View film info?[/]", default=False, console=console):
            from .info_cmd import show_inline_info

            show_inline_info(movie.title, ml)

        # Offer quick suggestion
        if cfg.get("groq_api_key") and ml.unwatched():
            go = Confirm.ask("  Want a suggestion for what's next?", default=False, console=console)
            if go:
                from ..commands.suggest import run_tonight
                run_tonight(None)
    else:
        success(f"[bold]{movie.title}[/]  marked unwatched")
        console.print()
