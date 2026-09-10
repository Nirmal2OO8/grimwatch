"""grimwatch search — fuzzy search with numbered watch action."""

from rich.prompt import Prompt, Confirm
from ..config import get_config
from ..metadata import MetadataCache, verify_film_metadata
from ..parser import Movie, MovieList, normalized_title_key
from ..ui import console, success, warn, movie_table, header, empty_state


def _find_by_field(values: list[str], field: str, archive: MovieList) -> list[Movie]:
    """Return archive films whose cached metadata shares a value in ``field``."""
    target_values = {value.casefold() for value in values if value.strip()}
    if not target_values:
        return []

    cache = MetadataCache()
    cached_movies: list[Movie] = []
    for candidate in archive.all_movies():
        try:
            cached = cache.get(normalized_title_key(candidate.title), candidate.year)
        except Exception:
            continue
        if cached is not None:
            cached_movies.append(candidate)
    if not cached_movies:
        return []

    try:
        metadata_rows = verify_film_metadata([candidate.title for candidate in cached_movies])
    except Exception:
        return []
    return [
        candidate
        for candidate, metadata in zip(cached_movies, metadata_rows)
        if target_values.intersection(value.casefold() for value in getattr(metadata, field))
    ]


def run_search(query: str, unwatched_only: bool, watched_only: bool):
    cfg = get_config()
    ml = MovieList(cfg["list_path"])

    results = ml.find(query)

    if unwatched_only:
        results = [m for m in results if not m.watched]
    elif watched_only:
        results = [m for m in results if m.watched]

    if not results:
        empty_state("NO MATCHES", f'Nothing in the archive matched "{query}".')
        return

    header("SEARCH RESULTS", f'"{query}"  //  {len(results)} found')
    console.print(movie_table(results))
    console.print()

    # Offer to act on a result
    choices = [str(i) for i in range(1, len(results) + 1)] + ["0"]
    pick = Prompt.ask(
        "  Pick a number to mark watched/unwatched [dim](0 to skip)[/]",
        console=console,
        choices=choices,
        default="0",
    )

    movie = results[int(pick) - 1] if pick != "0" else None
    if movie is not None:
        if movie.watched:
            go = Confirm.ask(f"  Mark [bold]{movie.title}[/] as [yellow]unwatched[/]?", default=False, console=console)
            if go:
                ml.mark_watched(movie, False)
                ml.save()
                success(f"Marked unwatched: {movie.title}")
        else:
            go = Confirm.ask(f"  Mark [bold]{movie.title}[/] as [green]watched[/]?", default=True, console=console)
            if go:
                ml.mark_watched(movie, True)
                ml.save()
                success(f"Marked watched: {movie.title}")

    console.print()
    if movie is None:
        return

    follow = Prompt.ask(
        "  [reelq.dim]// Next[/] [reelq.muted]i=info  d=director filmography  a=actor filmography  Enter=skip[/]",
        choices=["i", "d", "a", ""],
        default="",
        console=console,
        show_choices=False,
    )
    if follow == "i":
        from .info_cmd import show_inline_info

        show_inline_info(movie.title, ml)
        return
    if follow not in {"d", "a"}:
        return

    try:
        metadata_rows = verify_film_metadata([movie.title])
    except Exception as exc:
        subject = "director" if follow == "d" else "cast"
        warn(f"Could not fetch {subject} info: {exc}")
        return
    if not metadata_rows:
        warn("No metadata was returned for this title.")
        return

    metadata = metadata_rows[0]
    if follow == "d":
        if not metadata.directors:
            warn(f"No director information found for {movie.title}. Try classifying it first with grimwatch classify.")
            return
        directors = list(metadata.directors)
        related = [candidate for candidate in _find_by_field(directors, "directors", ml) if candidate is not movie]
        if related:
            director_name = directors[0]
            header("FILMOGRAPHY", f"More from {director_name}")
            console.print(movie_table(related))
        else:
            warn(f"No other films by {directors[0]} found in your archive's classified metadata.")
        return

    if not metadata.cast:
        warn(f"No cast information found for {movie.title}. Try classifying it first with grimwatch classify.")
        return
    actors = list(metadata.cast)
    related = [candidate for candidate in _find_by_field(actors, "cast", ml) if candidate is not movie]
    if related:
        actor_name = actors[0]
        header("FILMOGRAPHY", f"More with {actor_name}")
        console.print(movie_table(related))
    else:
        warn(f"No other films with {actors[0]} found in your archive's classified metadata.")
