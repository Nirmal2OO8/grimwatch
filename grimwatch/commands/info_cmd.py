"""grimwatch info — display archive and verified metadata for one film."""

from urllib.parse import quote_plus

from rich import box
from rich.markup import escape
from rich.panel import Panel

from ..config import get_config
from ..metadata import MetadataCache, VerifiedMetadata, verify_film_metadata
from ..parser import Movie, MovieList, normalized_title_key, strip_release_year, titles_equivalent
from ..ui import console, error, header, short_genre, warn


def _metadata_line(label: str, values: tuple[str, ...]) -> str:
    text = ", ".join(value for value in values if value.strip()) or "Unknown"
    return f"[reelq.bone]{label}:[/] [reelq.violet]{escape(text)}[/]"


def _related_director_titles(movie: Movie, metadata: VerifiedMetadata, ml: MovieList) -> list[str]:
    """Find archive titles sharing a cached director with the selected film."""
    directors = {director.casefold() for director in metadata.directors if director.strip()}
    if not directors:
        return []

    related: list[str] = []
    cache = MetadataCache()
    for candidate in ml.all_movies():
        if titles_equivalent(candidate.title, movie.title):
            continue
        try:
            cached = cache.get(normalized_title_key(candidate.title), candidate.year)
        except Exception:
            continue
        if cached and directors.intersection(director.casefold() for director in cached.directors):
            related.append(candidate.title)
    return related


def _render_info_panel(
    movie: Movie,
    metadata: VerifiedMetadata | None,
    related_titles: list[str] | None = None,
) -> None:
    """Render the archive record and any verified metadata for one film."""
    status = "Watched" if movie.watched else "Unwatched"
    watched_on = movie.watched_on or "—"
    lines = [
        f"[reelq.bone]Title:[/] [reelq.title]{escape(movie.title)}[/]",
        f"[reelq.bone]Year:[/] [reelq.violet]{movie.year or 'Unknown'}[/]",
        f"[reelq.bone]Shelf:[/] [reelq.violet]{escape(short_genre(movie.genre))}[/]",
        f"[reelq.bone]Status:[/] [reelq.violet]{status}[/]",
        f"[reelq.bone]Watched on:[/] [reelq.violet]{escape(watched_on)}[/]",
    ]
    if movie.note:
        lines.append(f"[reelq.bone]Your note:[/] [reelq.muted]{escape(movie.note)}[/]")

    if metadata and metadata.verified:
        lines.extend(
            [
                "",
                _metadata_line("Director(s)", metadata.directors),
                _metadata_line("Countries", metadata.countries),
                _metadata_line("Source genres", metadata.genres),
            ]
        )
        if metadata.cast:
            lines.append(_metadata_line("Cast", metadata.cast[:12]))
        source_url = metadata.source_url
        if source_url and "imdb.com/title/" in source_url:
            lines.append(f"[reelq.bone]IMDb:[/] [link={source_url}][reelq.violet]Open film page[/][/link]")
        else:
            lines.append("[reelq.bone]IMDb:[/] [reelq.muted]Not available from verified metadata.[/]")
        if metadata.source_description:
            lines.extend(
                [
                    "",
                    f"[reelq.bone]Source description:[/]\n[reelq.muted]{escape(metadata.source_description)}[/]",
                ]
            )
        if related_titles:
            lines.extend(
                [
                    "",
                    f"[reelq.bone]More from this director:[/] [reelq.muted]{escape(', '.join(related_titles[:8]))}[/]",
                ]
            )
    console.print(Panel(
        "\n".join(lines),
        title="[reelq.title]ARCHIVE RECORD[/]",
        border_style="reelq.violet",
        box=box.ROUNDED,
        padding=(1, 2),
    ))
    if not metadata or not metadata.verified:
        warn("Metadata lookup found no verified match for this title. Archive data only.")
        bare_title = strip_release_year(movie.title)
        search_url = f"https://www.imdb.com/find/?q={quote_plus(bare_title)}"
        console.print(
            f"  [reelq.muted]Search IMDb:[/] "
            f"[link={search_url}][reelq.violet]imdb.com/find/?q={escape(bare_title)}[/][/link]"
        )


def _metadata_for(title: str) -> VerifiedMetadata | None:
    try:
        metadata_rows = verify_film_metadata([title])
    except Exception:
        return None
    return metadata_rows[0] if metadata_rows else None


def run_info(title: str) -> None:
    cfg = get_config()
    ml = MovieList(cfg["list_path"])
    movie = ml.find_one(title)
    if movie is None:
        error(f'No film in the archive matched "{title}".')
        return

    metadata = _metadata_for(movie.title)
    related_titles = _related_director_titles(movie, metadata, ml) if metadata and metadata.verified else []
    header("FILM INFO", movie.title)
    _render_info_panel(movie, metadata, related_titles)


def show_inline_info(title: str, ml: MovieList) -> None:
    """Render film info in-place after an archive selection."""
    movie = ml.find_one(title)
    if movie is None:
        return

    metadata = _metadata_for(movie.title)
    related_titles = _related_director_titles(movie, metadata, ml) if metadata and metadata.verified else []
    _render_info_panel(movie, metadata, related_titles)
