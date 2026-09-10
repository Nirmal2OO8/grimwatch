"""grimwatch stats — progress, genre breakdown, decade heatmap, blind spots."""

from datetime import date

from rich.panel import Panel
from rich.table import Table
from rich import box
from ..config import get_config
from ..parser import MovieList
from ..ai import find_blind_spots
from ..ui import console, progress_bar, short_genre, header, stat_card, stat_cards, empty_state, error

DECADE_COLORS = {
    "1900s": "dim", "1910s": "dim", "1920s": "white", "1930s": "white",
    "1940s": "bright_white", "1950s": "bright_white", "1960s": "cyan",
    "1970s": "bright_cyan", "1980s": "green", "1990s": "bright_green",
    "2000s": "yellow", "2010s": "bright_yellow", "2020s": "bright_magenta",
}


def run_stats():
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
        empty_state("THE REELS ARE EMPTY", "Start your archive with grimwatch add \"Stalker (1979)\".")
        return

    # ── Header ─────────────────────────────────────────────────────────────────
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

    # ── Genre breakdown ────────────────────────────────────────────────────────
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1), expand=True)
    t.add_column("Shelf", style="reelq.bone", ratio=3)
    t.add_column("Bar", ratio=3, no_wrap=True)
    t.add_column("n", justify="right", style="reelq.muted", width=6)

    shelf_counts = []
    for block in ml.genres:
        shelf_total = block.total
        if shelf_total == 0:
            continue
        shelf_counts.append((block, block.watched_count, shelf_total))

    genre_stats = []
    for block, shelf_watched, shelf_total in sorted(
        shelf_counts, key=lambda shelf: shelf[1], reverse=True
    ):
        bar = progress_bar(shelf_watched, shelf_total, width=20)
        t.add_row(
            short_genre(block.header)[:45],
            bar,
            f"[green]{shelf_watched}[/][dim]/{shelf_total}[/]",
        )
        genre_stats.append({
            "genre": block.header,
            "watched": shelf_watched,
            "total": shelf_total,
        })

    console.print(Panel(t, title="[bold #b58cff]SHELVES[/]", border_style="#b58cff", padding=(0, 1)))
    console.print()

    # ── Decade heatmap ─────────────────────────────────────────────────────────
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

    # ── Under-covered shelves ──────────────────────────────────────────────────
    key = cfg.get("groq_api_key", "")
    if not key or key.startswith("dummy"):
        console.print("  [reelq.dim]Under-covered shelves: add a Groq key with grimwatch setup to analyze them.[/]")
        return
    if watched_n < 5:
        console.print(f"  [reelq.dim]Under-covered shelves unlock after 5 watched films ({watched_n}/5).[/]")
        return

    watched = ml.watched()
    try:
        with console.status("  [reelq.violet]Reading your under-covered shelves…[/]"):
            gaps = find_blind_spots(key, cfg["groq_model"], watched, genre_stats)
    except Exception as exc:
        error(f"Under-covered shelf analysis unavailable: {exc}")
        return
    if not gaps:
        console.print("  [reelq.dim]No shelf gaps could be identified from this archive yet.[/]")
        return
    stat_cards(
        *(
            stat_card(
                f"GAP {index}",
                f"{gap['coverage_percent']:.0f}%",
                f"{short_genre(gap['genre'])} — {gap['watched']}/{gap['total']} watched",
            )
            for index, gap in enumerate(gaps, 1)
        )
    )
    console.print()
    analysis = "\n".join(
        f"[reelq.warning]{short_genre(gap['genre'])}[/]  [reelq.muted]{gap['reason']}[/]"
        for gap in gaps
        if gap["reason"]
    )
    if analysis:
        console.print(Panel(
            analysis,
            title="[bold #e5b567]UNDER-COVERED SHELVES[/]",
            border_style="#e5b567",
            padding=(1, 2),
        ))
        console.print()
