"""grimwatch — your markdown movie archive, with a brain."""

import random as _random
import shlex
import typer
from typing import Optional
from rich.table import Table
from rich.prompt import Confirm, Prompt
from rich import box
from . import __version__
from .ui import console, brand_banner, stat_cards, stat_card, unquote

app = typer.Typer(
    add_completion=False,
    pretty_exceptions_enable=False,
    invoke_without_command=True,
)

COMMANDS = [
    ("1",  "add",      "Add a film or batch-import from a .txt/.md file"),
    ("2",  "import",   "Import a list, checklist, or grimwatch export"),
    ("3",  "watch",    "Mark a film watched (or unwatched with --undo)"),
    ("4",  "suggest",  "AI picks from your unwatched list"),
    ("5",  "tonight",  "One pick for tonight, right now"),
    ("6",  "random",   "Completely random unwatched film"),
    ("7",  "search",   "Search by title, keyword, or year"),
    ("8",  "list",     "Browse your list genre by genre"),
    ("9",  "stats",    "Progress, decade heatmap, under-covered shelves"),
    ("10", "classify", "Classify a separate import/list file"),
    ("11", "merge",    "Merge a second list into yours"),
    ("12", "dedup",    "Remove duplicates"),
    ("13", "setup",    "Change list path or Groq key"),
    ("14", "about",    "grimwatch — who made this and why"),
    ("15", "export",   "Export a Markdown backup; restore it with import"),
    ("16", "info",     "Show metadata and your notes for a film"),
]

CMD_MAP = {row[0]: row[1] for row in COMMANDS}
CMD_NAMES = {row[1] for row in COMMANDS}


def _split_command(line: str) -> list[str]:
    """Split a Windows-friendly command line without eating backslashes."""
    parts = shlex.split(line, posix=False)
    return [
        part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'" else part
        for part in parts
    ]


def _required_input(prompt: str) -> str:
    while True:
        value = unquote(Prompt.ask(prompt, console=console))
        if value:
            return value
        console.print("  [reelq.warning]◆[/] This value is required.")


def _guided_command(command: str) -> list[str]:
    """Turn a numbered menu choice into a complete command with prompts."""
    if command in {"add", "import"}:
        label = "Film title or .txt/.md path" if command == "add" else "Path to import"
        value = _required_input(f"  [reelq.accent]▸[/] {label}")
        return [command, value]
    if command in {"watch", "info"}:
        return [command, _required_input("  [reelq.accent]▸[/] Film title")]
    if command == "search":
        return [command, _required_input("  [reelq.accent]▸[/] Search query")]
    if command == "merge":
        return [command, _required_input("  [reelq.accent]▸[/] Markdown file to merge")]
    if command == "suggest":
        mood = Prompt.ask("  [reelq.accent]▸[/] Mood [reelq.dim](optional)[/]", console=console).strip()
        contrast_title = Prompt.ask(
            "  [reelq.accent]▸[/] Contrast against [reelq.dim](title or leave blank)[/]",
            console=console,
        ).strip()
        count = Prompt.ask("  [reelq.accent]▸[/] Suggestions [reelq.dim](1–8)[/]", choices=[str(i) for i in range(1, 9)], default="3", console=console)
        args = [command, "--count", count]
        if mood:
            args.extend(["--mood", mood])
        if contrast_title:
            args.extend(["--contrast-against", contrast_title])
        return args
    if command == "list":
        genre = Prompt.ask("  [reelq.accent]▸[/] Shelf [reelq.dim](optional)[/]", console=console).strip()
        args = [command, "--genre", genre] if genre else [command]
        if Confirm.ask("  [reelq.accent]▸[/] Unwatched only?", default=False, console=console):
            args.append("--unwatched")
        return args
    if command == "classify":
        source = _required_input("  [reelq.accent]▸[/] Import/list file to classify")
        args = [command, source]
        if Confirm.ask("  Write a classified companion file?", default=True, console=console):
            args.append("--write")
        return args
    return [command]


def _run_interactive_command(command_line: str | list[str]) -> bool:
    """Run one command and keep the parent REPL alive."""
    from .ui import error

    try:
        args = command_line if isinstance(command_line, list) else _split_command(command_line)
        if not args:
            return True
        app(args=args, prog_name="grimwatch", standalone_mode=False)
    except KeyboardInterrupt:
        console.print("\n  [reelq.dim]Interrupted. Still in the archive.[/]")
    except EOFError:
        return False
    except typer.Exit:
        pass
    except SystemExit:
        # Click uses SystemExit for --help/--version in some versions.
        pass
    except Exception as exc:
        message = str(exc).strip() or exc.__class__.__name__
        error(message)
    return True


def show_menu():
    # First-run setup belongs before the archive UI. Otherwise a user can pick
    # an AI command only to be interrupted by configuration questions.
    from .config import get_config
    cfg = get_config()

    console.print()
    console.print(brand_banner(f"v{__version__}  //  your markdown movie archive, with a brain"))
    console.print()

    _LORE = [
        "[reelq.dim]// The archive stirs. The reels remember.[/]",
        "[reelq.dim]// What will you summon from the dark tonight?[/]",
        "[reelq.dim]// Every film unwatched is a ghost waiting to be seen.[/]",
        "[reelq.dim]// The projector awakens. Choose wisely.[/]",
        "[reelq.dim]// Your vault. Your ritual. Your obsession.[/]",
    ]
    console.print(f"  {_random.choice(_LORE)}")
    console.print()

    # The configuration is ready before rendering choices or dispatching a command.
    from .parser import MovieList
    if cfg.get("list_path"):
        archive = MovieList(cfg["list_path"])
        total = archive.total_count()
        watched = archive.watched_count()
        stat_cards(
            stat_card("ARCHIVE", str(total), "films logged"),
            stat_card("WATCHED", str(watched), "completed"),
            stat_card("NEXT UP", str(total - watched), "unwatched"),
        )
        console.print()

    t = Table(box=box.SIMPLE_HEAD, show_header=False, padding=(0, 1), expand=True)
    t.add_column("KEY", style="reelq.accent", width=8, justify="right")
    t.add_column("COMMAND", style="reelq.title", width=14)
    t.add_column("WHAT IT DOES", style="reelq.dim")

    for num, cmd, desc in COMMANDS:
        t.add_row(f"⟨{num}⟩", cmd, desc)

    console.print(t)
    console.print("\n  [reelq.dim]// type a command by name, e.g.[/] [reelq.violet]grimwatch tonight[/]  [reelq.dim]or press[/] [reelq.accent]q[/] [reelq.dim]to seal the archive[/]")
    console.print()

    while True:
        try:
            pick = Prompt.ask(
                "  [reelq.accent]▸[/] Command or number [reelq.dim](q to quit)[/]",
                console=console,
                default="q",
            ).strip()
        except KeyboardInterrupt:
            console.print("\n  [reelq.dim]Still here — type q when you want to leave.[/]")
            continue
        except EOFError:
            console.print()
            return

        if pick.casefold() in {"q", "quit", "exit"}:
            console.print("  [reelq.dim]Archive closed.[/]")
            return

        command = CMD_MAP.get(pick)
        command_line = _guided_command(command) if command else pick
        if command is None:
            typed = _split_command(pick)
            if typed and typed[0].casefold() == "grimwatch":
                typed = typed[1:]
                command_line = typed
            if not typed or typed[0].casefold() not in CMD_NAMES:
                if len(typed) == 1 and typed[0].casefold().endswith((".md", ".txt", ".mdd")):
                    console.print(
                        "  [reelq.error]×[/] A file path needs an action. "
                        "Use [reelq.violet]merge \"path\\to\\list.md\"[/] or choose [reelq.accent]11[/]."
                    )
                else:
                    console.print(f"  [reelq.error]×[/] Unknown command: {pick}")
                continue

        if not _run_interactive_command(command_line):
            return
        console.print("\n  [reelq.dim]Back at the archive. Choose another command, or q to quit.[/]\n")


def version_callback(value: bool):
    if value:
        console.print(f"[reelq.title]grimwatch[/] [reelq.muted]v{__version__}[/]")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True,
        help="Show version."
    ),
):
    if ctx.invoked_subcommand is None:
        show_menu()


# ── Commands ───────────────────────────────────────────────────────────────────

@app.command()
def setup():
    """Change list path or Groq key."""
    from .config import run_setup
    run_setup()


@app.command()
def add(
    input: str = typer.Argument(..., help="Title or path to a .txt/.md import file"),
    genre: Optional[str] = typer.Option(None, "--genre", "-g", help="Force genre"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmations"),
):
    """Add a film or batch-import. AI classifies and fills in missing years."""
    from .commands.add import run_add
    run_add(input, genre, yes)


@app.command(name="import")
def import_cmd(
    file: str = typer.Argument(..., help="Raw list, checklist, or grimwatch export"),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Import a list, checklist, or a Markdown backup made by grimwatch export."""
    from .commands.add import run_add
    run_add(file, None, yes)


@app.command()
def watch(
    title: str = typer.Argument(..., help="Title to mark watched"),
    undo: bool = typer.Option(False, "--undo", help="Mark as unwatched instead"),
):
    """Mark a film watched (or unwatched with --undo)."""
    from .commands.watch import run_watch
    run_watch(title, undo)


@app.command()
def suggest(
    mood: Optional[str] = typer.Option(None, "--mood", "-m", help="What you feel like"),
    similar: Optional[str] = typer.Option(None, "--similar", "-s", help="Similar to this title"),
    contrast: bool = typer.Option(False, "--contrast", "-c", help="Different from recent watches"),
    contrast_against: Optional[str] = typer.Option(None, "--contrast-against", "-C", help="Pick films contrasting with this title"),
    count: int = typer.Option(3, "--count", "-n", help="How many suggestions"),
):
    """AI-powered suggestions from your unwatched list."""
    from .commands.suggest import run_suggest
    run_suggest(mood, similar, contrast_against, contrast, count)


@app.command()
def tonight(
    runtime: Optional[int] = typer.Option(None, "--runtime", "-r", help="Runtime preference in minutes (not verified from title-only data)"),
    genre: Optional[str] = typer.Option(None, "--genre", "-g", help="Prefer a genre or shelf"),
):
    """One pick for tonight. No arguments needed."""
    from .commands.suggest import run_tonight
    run_tonight(runtime, genre)


@app.command()
def random(
    genre: Optional[str] = typer.Option(None, "--genre", "-g", help="Limit to a genre"),
):
    """Pick a completely random unwatched film."""
    from .commands.random_cmd import run_random
    run_random(genre)


@app.command()
def search(
    query: str = typer.Argument(..., help="Title, keyword, or year"),
    unwatched: bool = typer.Option(False, "--unwatched", "-u"),
    watched: bool = typer.Option(False, "--watched", "-w"),
):
    """Search your list. Pick a result to toggle watched."""
    from .commands.search import run_search
    run_search(query, unwatched, watched)


@app.command()
def info(
    title: str = typer.Argument(..., help="Film title to look up"),
):
    """Show metadata, director, IMDb link, and your notes for a film."""
    from .commands.info_cmd import run_info
    run_info(title)


@app.command()
def stats():
    """Progress, decade heatmap, and under-covered shelf analysis."""
    from .commands.stats import run_stats
    run_stats()


@app.command()
def classify(
    file: str = typer.Argument(..., help="Raw or checkbox import/list file to classify"),
    write: bool = typer.Option(False, "--write", "-w", help="Write a .classified.md companion file"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Companion output path (never the main archive)"),
    force: bool = typer.Option(
        False, "--force", "-f",
        help="Write the companion file even if some titles could not be classified after retries",
    ),
    refresh_metadata: bool = typer.Option(
        False,
        "--refresh-metadata",
        help="Bypass the local metadata cache for this classification run",
    ),
):
    """Classify a list/import file; never uses movies.md as its source."""
    from .commands.classify import run_classify
    run_classify(file, write, output, force, refresh_metadata)


@app.command()
def merge(
    file: str = typer.Argument(..., help="Second list to merge in"),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Merge a second markdown list into yours."""
    from .commands.merge import run_merge
    run_merge(file, yes)


@app.command()
def dedup(
    yes: bool = typer.Option(False, "--yes", "-y"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show duplicate groups without changing the archive"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write deduped result to a different file instead of overwriting the archive"),
):
    """Find and remove duplicates."""
    from .commands.dedup import run_dedup
    run_dedup(yes, dry_run, output)


@app.command(name="list")
def list_cmd(
    genre: Optional[str] = typer.Option(None, "--genre", "-g"),
    unwatched: bool = typer.Option(False, "--unwatched", "-u"),
    watched: bool = typer.Option(False, "--watched", "-w"),
):
    """Browse by genre. Pick a number to toggle watched."""
    from .commands.list_cmd import run_list
    run_list(genre, unwatched, watched)


@app.command(name="export")
def export_cmd(
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Markdown export path (defaults to export.md beside the archive)"),
):
    """Export a Markdown backup that grimwatch import can restore."""
    from .commands.export import run_export
    run_export(output)


@app.command()
def about():
    """Who made grimwatch and why."""
    from rich.panel import Panel
    from rich import box as _box

    console.print()
    console.print(Panel(
        "[reelq.title]GRIMWATCH[/]  [reelq.dim]v{ver}[/]\n\n"
        "[reelq.bone]A terminal archive for the films you mean to watch\n"
        "and the ones you'll never forget.[/]\n\n"
        "[reelq.dim]——————————————————————————————[/]\n\n"
        "[reelq.muted]Built by[/] [reelq.title]Nirmal[/]\n"
        "[reelq.dim]github.com/Nirmal2OO8/grimwatch[/]\n\n"
        "[reelq.muted]License:[/] [reelq.bone]MIT[/]  [reelq.dim]//[/]  "
        "[reelq.muted]Issues & PRs welcome[/]\n\n"
        "[reelq.dim]——————————————————————————————[/]".format(ver=__version__),
        border_style="reelq.accent",
        box=_box.DOUBLE,
        padding=(1, 3),
    ))
    console.print()


if __name__ == "__main__":
    app()
