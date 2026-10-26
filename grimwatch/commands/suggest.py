"""grimwatch suggest / tonight — AI recommendations with numbered selection."""

from typing import Optional
from rich.prompt import Prompt, Confirm
from ..config import get_config, get_backend, recent_recommendation_titles, record_recommendations
from ..genre_coverage import compute_coverage_gaps
from ..parser import Movie, MovieList, normalized_title_key
from ..ai import suggest_movies, suggest_tonight
from ..ui import console, success, error, suggestion_card, header, empty_state, warn


def _partition_movies(archive: MovieList) -> tuple[list[Movie], list[Movie]]:
    """Return unwatched and watched entries after one archive traversal."""
    unwatched: list[Movie] = []
    watched: list[Movie] = []
    for movie in archive.all_movies():
        (watched if movie.watched else unwatched).append(movie)
    return unwatched, watched


def _recommendation_pool(unwatched: list[Movie], cfg: dict) -> tuple[list[Movie], int]:
    """Avoid recently shown titles without ever producing an empty picker."""
    recent_keys = {
        normalized_title_key(title)
        for title in recent_recommendation_titles(cfg, cfg["list_path"])
        if normalized_title_key(title)
    }
    eligible = [movie for movie in unwatched if normalized_title_key(movie.title) not in recent_keys]
    if not eligible:
        return unwatched, 0
    return eligible, len(unwatched) - len(eligible)


def _record_shown(cfg: dict, titles: list[str]) -> None:
    try:
        record_recommendations(cfg, cfg["list_path"], titles)
    except OSError as exc:
        # Recommendation history is a convenience; a config write problem must
        # not invalidate an otherwise usable recommendation.
        warn(f"Could not save recommendation cooldown history: {exc}")


def _offer_watch(title: str, ml: MovieList, cfg: dict):
    """After a suggestion, offer to mark it watched."""
    movie = ml.find_one(title)
    if movie and not movie.watched:
        go = Confirm.ask(f"\n  Mark [bold]{title}[/] as watched?", default=False, console=console)
        if go:
            ml.mark_watched(movie, True)
            note = Prompt.ask("  [reelq.dim]// Add a note? (optional)[/]", default="", console=console).strip()
            if note:
                ml.set_note(movie, note)
            ml.save()
            success(f"Marked watched: {title}")


def run_suggest(
    mood: Optional[str],
    similar: Optional[str],
    contrast_against: Optional[str],
    contrast: bool,
    count: int,
):
    count = max(1, min(count, 20))
    cfg = get_config()
    ml = MovieList(cfg["list_path"])

    unwatched, watched = _partition_movies(ml)

    if not unwatched:
        empty_state("THE ARCHIVE IS CLEARED", "Every film is marked watched. Add something new with grimwatch add.")
        return

    try:
        backend = get_backend(cfg)
    except ValueError as exc:
        error(str(exc))
        return

    resolved_contrast = contrast_against.strip() if contrast_against else None
    if contrast and not resolved_contrast and watched:
        resolved_contrast = watched[-1].title

    if resolved_contrast:
        heading = f"Contrasting: {resolved_contrast}"
    elif mood:
        heading = f"Mood: \"{mood}\""
    elif similar:
        heading = f"Similar to: {similar}"
    elif contrast:
        heading = "Something different"
    else:
        heading = "What to watch next"

    gaps = compute_coverage_gaps(ml.genres)

    with console.status("") as status:
        status.update("  [reelq.dim]Sampling your archive…[/]")
        pool, excluded_count = _recommendation_pool(unwatched, cfg)
        status.update("  [reelq.violet]Sending to model…[/]")
        try:
            results = suggest_movies(
                backend=backend,
                model=cfg["groq_model"],
                unwatched=pool,
                recently_watched=watched,
                mood=mood,
                similar_to=similar,
                contrast=contrast,
                count=count,
                contrast_against=resolved_contrast,
                coverage_gaps=gaps,
            )
        except Exception as exc:
            error(f"Could not get suggestions: {exc}")
            return
        status.update("  [reelq.success]Mapping results…[/]")

    header("THE ORACLE SPEAKS", heading)
    if excluded_count:
        console.print(f"  [dim]Keeping {excluded_count} recently shown pick(s) off the board.[/]")
    for i, r in enumerate(results, 1):
        suggestion_card(r, index=i)
    _record_shown(cfg, [result["title"] for result in results])

    # Numbered pick
    console.print()
    choices = [str(i) for i in range(1, len(results) + 1)] + ["0"]
    pick = Prompt.ask(
        "  Pick one to mark watched [dim](0 to skip)[/]",
        console=console,
        choices=choices,
        default="0",
    )
    if pick != "0":
        chosen = results[int(pick) - 1]
        if Confirm.ask("  [reelq.dim]// View film info?[/]", default=False, console=console):
            from .info_cmd import show_inline_info

            show_inline_info(chosen["title"], ml)
        _offer_watch(chosen["title"], ml, cfg)

    console.print()
    if Confirm.ask("  [reelq.dim]// Want suggestions from outside your archive?[/]", default=False, console=console):
        _run_external_suggest(mood, similar, ml, cfg, watched)


def _run_external_suggest(
    mood: Optional[str], similar: Optional[str], ml: MovieList, cfg: dict, watched: list[Movie]
) -> None:
    from ..ai import suggest_external_movies
    from .add import run_add

    try:
        backend = get_backend(cfg)
    except ValueError as exc:
        error(str(exc))
        return

    unwatched_titles = [movie.title for movie in ml.all_movies() if not movie.watched]
    try:
        with console.status("  [reelq.violet]Searching beyond the archive…[/]"):
            results = suggest_external_movies(
                backend=backend,
                model=cfg["groq_model"],
                recently_watched=watched,
                unwatched_titles=unwatched_titles,
                mood=mood,
                similar_to=similar,
                count=4,
            )
    except Exception as exc:
        error(f"External suggest failed: {exc}")
        return
    if not results:
        warn("No external suggestions found.")
        return
    header("BEYOND THE ARCHIVE", "films not yet in your list")
    for index, result in enumerate(results, 1):
        suggestion_card(result, index=index, accent="reelq.warning")
    console.print()
    choices = [str(index) for index in range(1, len(results) + 1)] + ["0"]
    pick = Prompt.ask(
        "  Add one to your archive? [dim](0 to skip)[/]",
        console=console,
        choices=choices,
        default="0",
    )
    if pick != "0":
        chosen = results[int(pick) - 1]
        run_add(chosen["title"], chosen.get("genre"), yes=True)


def run_tonight(max_runtime: Optional[int], genre: Optional[str] = None):
    if max_runtime is not None and max_runtime < 1:
        error("Runtime must be a positive number of minutes.")
        return
    cfg = get_config()
    ml = MovieList(cfg["list_path"])

    unwatched, watched = _partition_movies(ml)

    if not unwatched:
        empty_state("THE ARCHIVE IS CLEARED", "There is no unwatched film left for tonight.")
        return

    try:
        backend = get_backend(cfg)
    except ValueError as exc:
        error(str(exc))
        return

    pool, excluded_count = _recommendation_pool(unwatched, cfg)

    try:
        with console.status("  [reelq.accent]Choosing tonight's screening…[/]"):
            result = suggest_tonight(
                backend=backend,
                model=cfg["groq_model"],
                unwatched=pool,
                recently_watched=watched,
                max_runtime=max_runtime,
                genre=genre,
            )
    except Exception as exc:
        error(f"Could not choose tonight's film: {exc}")
        return

    preferences = []
    if max_runtime:
        preferences.append(f"runtime: {max_runtime} min")
    if genre:
        preferences.append(f"genre: {genre}")
    subtitle = "  //  ".join(preferences) or "one pick, right now"
    header("TONIGHT'S SCREENING", subtitle)
    if excluded_count:
        console.print(f"  [dim]Keeping {excluded_count} recently shown pick(s) off the board.[/]")
    suggestion_card(result, accent="reelq.accent")
    _record_shown(cfg, [result["title"]])

    console.print()
    go = Confirm.ask("  Mark as watched?", default=False, console=console)
    if go:
        _offer_watch(result["title"], ml, cfg)
    console.print()
