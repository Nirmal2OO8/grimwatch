"""grimwatch random — instant random pick from unwatched."""

import random as _random
from typing import Optional
from rich.prompt import Confirm
from ..config import get_config
from ..parser import MovieList
from ..ui import console, warn, success, suggestion_card, short_genre, header, empty_state


def run_random(genre: Optional[str]):
    cfg = get_config()
    ml = MovieList(cfg["list_path"])

    pool = ml.unwatched()

    if genre:
        block = ml.find_genre_fuzzy(genre)
        if not block:
            warn(f"No genre matching: {genre}")
            return
        pool = [m for m in pool if m.genre == block.header]

    if not pool:
        empty_state(
            "NO UNWATCHED FILMS",
            f"Nothing remains on the {genre} shelf." if genre else "Your archive has no unwatched films.",
        )
        return

    pick = _random.choice(pool)
    header("THE LOTTERY", "one random unwatched film")
    suggestion_card(
        {"title": pick.title, "genre": short_genre(pick.genre), "reason": "randomly selected"},
        accent="reelq.accent",
    )

    console.print()
    go = Confirm.ask("  Mark as watched?", default=False, console=console)
    if go:
        ml.mark_watched(pick, True)
        ml.save()
        success(f"Marked watched: {pick.title}")
    console.print()
