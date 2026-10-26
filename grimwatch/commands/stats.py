"""grimwatch stats — progress, genre breakdown, coverage gaps, decade heatmap."""

from datetime import date

from rich import box
from rich.panel import Panel
from rich.table import Table

from ..config import get_config, get_backend
from ..genre_coverage import compute_coverage_gaps
from ..parser import MovieList
from ..ui import console, progress_bar, short_genre, header, stat_card, stat_cards, empty_state, error, warn


DECADE_COLORS = {
    "1900s": "dim", "1910s": "dim", "1920s": "white", "1930s": "white",
    "1940s": "bright_white", "1950s": "bright_white", "1960s": "cyan",
    "1970s": "bright_cyan", "1980s": "green", "1990s": "bright_green",
    "2000s": "yellow", "2010s": "bright_yellow", "2020s": "bright_magenta",
}

_TIER_COLOR = {
    "critical": "reelq.accent",
    "significant": "reelq.warning",
    "minor": "reelq.muted",
}


def _coverage_gap_panel(ml: MovieList) -> None:
    gaps = compute_coverage_gaps(ml.genres)
    actionable = [g for g in gaps if g.tier in ("critical", "significant")]
    if not actionable:
        return

    lines: list[str] = []
    for gap in actionable[:6]:
        color = _TIER_COLOR[gap.tier]
        bar = progress_bar(gap.watched, gap.total, width=18)
        label = short_genre(gap.genre)[:38]
        lines.append(
            f"  [{color}]{gap.tier.upper():11}[/]  "
            f"[reelq.bone]{label}[/]\n"
            f"              {bar}  "
            f"[reelq.muted]{gap.watched}/{gap.total}  {gap.display_pct}%[/]"
        )

    console.print(Panel(
        "\n".join(lines),
        title="[bold #e5b567]COVERAGE GAPS[/]",
        border_style="#e5b567",
        padding=(0, 1),
    ))
    console.print()


def run_stats(ai: bool = False) -> None:
    cfg = get_config()
    ml = MovieList(cfg["list_path"])

    total = ml.total_count()
    watched_n = ml.watched_count()
    pct = (watched_n / total * 100) if total else 0
    today_date = date.today()
    today = today_date.isoformat()
    month_start = today_date.replace(day=1).isoformat()
    watched_this_month = sum(
        1
        for movie in ml.watched()
        if movie.watched_on and month_start <= movie.watched_on <= today
    )

    if not total:
        header("ARCHIVE TELEMETRY", "nothing logged yet")
        empty_state("THE REELS ARE EMPTY", 'Start with grimwatch add "Stalker (1979)".')
        return

    bar = progress_bar(watched_n, total, width=30)
    header("ARCHIVE TELEMETRY", "progress, patterns, coverage gaps")
    stat_cards(
        stat_card("WATCHED", str(watched_n), f"of {total} films"),
        stat_card("REMAINING", str(total - watched_n), "still waiting"),
        stat_card("COMPLETION", f"{pct:.1f}%", "archive progress"),
        stat_card("THIS MONTH", str(watched_this_month), f"since {month_start}"),
    )
    console.print()
    console.print(Panel(
        f"{bar}\n\n"
        f"  [bold white]{watched_n}[/] watched  [dim]·[/]  "
        f"[dim]{total - watched_n} remaining[/]  [dim]·[/]  "
        f"[cyan]{pct:.1f}%[/] complete",
        title="[bold #f4d6a0]PROGRESS BAR[/]",
        border_style="#d33b57",
        padding=(1, 2),
    ))
    console.print()

    # Coverage gaps — deterministic, shown before decade heatmap.
    _coverage_gap_panel(ml)

    # Genre breakdown table.
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1), expand=True)
    t.add_column("Shelf", style="reelq.bone", ratio=3)
    t.add_column("Bar", ratio=3, no_wrap=True)
    t.add_column("n", justify="right", style="reelq.muted", width=6)

    genre_stats: list[dict] = []
    for block in sorted(ml.genres, key=lambda b: b.watched_count, reverse=True):
        if block.total == 0:
            continue
        bar = progress_bar(block.watched_count, block.total, width=20)
        t.add_row(
            short_genre(block.header)[:45],
            bar,
            f"[green]{block.watched_count}[/][dim]/{block.total}[/]",
        )
        genre_stats.append({"genre": block.header, "watched": block.watched_count, "total": block.total})

    console.print(Panel(t, title="[bold #b58cff]SHELVES[/]", border_style="#b58cff", padding=(0, 1)))
    console.print()

    # Decade heatmap.
    decades = ml.decade_breakdown()
    if decades:
        lines = []
        for decade, data in decades.items():
            if decade == "Unknown":
                continue
            color = DECADE_COLORS.get(decade, "white")
            w, tot = data["watched"], data["total"]
            bar = progress_bar(w, tot, width=25)
            lines.append(f"  [{color}]{decade}[/]  {bar}  [dim]{w}/{tot}[/]")
        console.print(Panel(
            "\n".join(lines),
            title="[bold #b58cff]DECADE HEATMAP[/]",
            border_style="#b58cff",
            padding=(0, 1),
        ))
        console.print()

    # AI blind-spot analysis: opt-in only.
    if not ai:
        console.print(
            "  [reelq.dim]Run [bold]grimwatch stats --ai[/] for AI-powered blind-spot analysis "
            "with canonical film suggestions.[/]"
        )
        return

    try:
        backend = get_backend(cfg)
    except ValueError as exc:
        warn(f"AI unavailable: {exc}")
        return

    if watched_n < 5:
        console.print(f"  [reelq.dim]Blind-spot analysis unlocks after 5 watched films ({watched_n}/5).[/]")
        return

    from ..ai import find_blind_spots
    watched = ml.watched()
    try:
        with console.status("  [reelq.violet]Identifying blind spots…[/]"):
            gaps = find_blind_spots(backend, cfg["groq_model"], watched, genre_stats)
    except Exception as exc:
        error(f"Blind-spot analysis unavailable: {exc}")
        return

    if not gaps:
        console.print("  [reelq.dim]No blind spots identified from this archive yet.[/]")
        return

    stat_cards(*(
        stat_card(
            f"GAP {i}",
            f"{gap['coverage_percent']:.0f}%",
            f"{short_genre(gap['genre'])} — {gap['watched']}/{gap['total']} watched",
        )
        for i, gap in enumerate(gaps, 1)
    ))
    console.print()
    analysis = "\n".join(
        f"[reelq.warning]{short_genre(gap['genre'])}[/]  [reelq.muted]{gap['reason']}[/]"
        for gap in gaps if gap["reason"]
    )
    if analysis:
        console.print(Panel(
            analysis,
            title="[bold #e5b567]BLIND SPOTS[/]",
            border_style="#e5b567",
            padding=(1, 2),
        ))
        console.print()
