"""Shared terminal UI for grimwatch.

The visual language is intentionally small and consistent: bone text on an
ink-black terminal, crimson for actions, violet for the archive, and chunky
retro glyphs that still render well in a plain Windows terminal.
"""

import io
import sys

from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text
from rich.theme import Theme


THEME = Theme({
    "reelq.title": "bold #e8c97a",
    "reelq.accent": "bold #c0152a",
    "reelq.violet": "#b58cff",
    "reelq.bone": "#ede0c8",
    "reelq.muted": "#8a7d8e",
    "reelq.dim": "#524960",
    "reelq.success": "#9fce78",
    "reelq.warning": "#e5b567",
    "reelq.error": "#ef6b73",
})

def _console_stream():
    """Avoid a Windows code-page crash when output is redirected or captured."""
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        "▸▣◆×□╔═█░".encode(encoding)
        return stream
    except (LookupError, UnicodeEncodeError):
        # Retain a usable CLI in legacy code pages. Rich's default Windows
        # renderer otherwise raises UnicodeEncodeError before it can print an
        # error message or complete a non-interactive command.
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            return io.TextIOWrapper(buffer, encoding=encoding, errors="replace", write_through=True)
        return stream


console = Console(theme=THEME, highlight=False, file=_console_stream(), legacy_windows=False)

WATCHED_COLOR = "reelq.success"
UNWATCHED_COLOR = "reelq.bone"
GENRE_COLOR = "reelq.violet"
DIM = "reelq.dim"
ACCENT = "reelq.accent"
WARN = "reelq.warning"
ERR = "reelq.error"

WATCHED_ICON = "[reelq.success]▣[/]"
UNWATCHED_ICON = "[reelq.dim]□[/]"


def logo() -> Text:
    """Return a BBS-era ASCII block logo for grimwatch."""
    return Text.from_markup(
        "[reelq.accent] ██████╗ ██████╗ ██╗███╗   ███╗\n"
        "[reelq.accent]██╔════╝ ██╔══██╗██║████╗ ████║\n"
        "[reelq.accent]██║  ███╗██████╔╝██║██╔████╔██║\n"
        "[reelq.accent]██║   ██║██╔══██╗██║██║╚██╔╝██║\n"
        "[reelq.accent]╚██████╔╝██║  ██║██║██║ ╚═╝ ██║\n"
        "[reelq.accent] ╚═════╝ ╚═╝  ╚═╝╚═╝╚═╝     ╚═╝[/]"
    )


def brand_banner(subtitle: str = "your markdown movie archive") -> Panel:
    content = Group(logo(), Text.from_markup(f"[reelq.muted]{subtitle}[/]"))
    return Panel(content, border_style="reelq.accent", box=box.DOUBLE, padding=(1, 2), expand=False)


def header(title: str, subtitle: str = ""):
    """Print a consistent section header."""
    label = Text.from_markup(f"[reelq.accent]▸[/] [reelq.title]{title}[/]")
    if subtitle:
        label.append(f"  {subtitle}", style="reelq.muted")
    console.print()
    console.print(Panel(label, border_style="reelq.violet", box=box.ROUNDED, padding=(0, 2)))
    console.print()


def success(msg: str):
    console.print(f"  [reelq.success]▣[/]  {msg}")


def warn(msg: str):
    console.print(f"  [reelq.warning]◆[/]  {msg}")


def error(msg: str):
    console.print(f"  [reelq.error]×[/]  {msg}")


def unquote(value: str) -> str:
    """Remove wrapping quotes commonly included when pasting Windows paths."""
    value = str(value).strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def empty_state(title: str, detail: str = ""):
    """Render a useful, quiet empty state instead of a bare warning."""
    body = f"[reelq.title]{escape(title)}[/]"
    if detail:
        body += f"\n[reelq.muted]{escape(detail)}[/]"
    console.print(Panel(body, border_style="reelq.dim", box=box.ROUNDED, padding=(1, 2)))


def short_genre(genre_header: str) -> str:
    """Return the concise name before a descriptive em dash or parenthesis."""
    import re
    s = re.split(r"\s*[—(]", genre_header)[0].strip()
    return s or genre_header


def movie_table(movies: list, title: str = "", show_genre: bool = True) -> Table:
    """Build a numbered, readable film table."""
    t = Table(
        title=title or None,
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="reelq.accent",
        padding=(0, 1),
        expand=True,
    )
    t.add_column("#", style=DIM, width=4, justify="right")
    t.add_column("", width=2, justify="center")
    t.add_column("Film", style=UNWATCHED_COLOR, ratio=3)
    if show_genre:
        t.add_column("Shelf", style=GENRE_COLOR, ratio=2)

    for i, movie in enumerate(movies, 1):
        dot = WATCHED_ICON if movie.watched else UNWATCHED_ICON
        title_style = DIM if movie.watched else "reelq.bone"
        row = [str(i), dot, f"[{title_style}]{escape(movie.title)}[/]"]
        if show_genre:
            row.append(f"[{GENRE_COLOR}]{escape(short_genre(movie.genre))}[/]")
        t.add_row(*row)
    return t


def pick_from(movies: list, prompt: str = "Pick a number") -> object:
    """Show a numbered list and return the selected movie."""
    if not movies:
        return None
    if len(movies) == 1:
        return movies[0]

    console.print(movie_table(movies))
    choice = Prompt.ask(
        f"\n  [reelq.accent]▸[/] {prompt}",
        console=console,
        choices=[str(i) for i in range(1, len(movies) + 1)],
    )
    return movies[int(choice) - 1]


def suggestion_card(result: dict, index: int = 0, accent: str = GENRE_COLOR):
    """Render a recommendation as a compact retro title card."""
    genre = short_genre(result.get("genre", "Unknown shelf"))
    reason = result.get("reason", "")
    num = f"{index:02d}  " if index else ""
    title = escape(str(result.get("title", "Unknown film")))
    body = f"[reelq.violet]{escape(genre)}[/]"
    if reason:
        body += f"\n[reelq.muted italic]{escape(str(reason))}[/]"
    console.print(Panel(
        body,
        title=f"[reelq.title]{num}{title}[/]",
        border_style=accent,
        box=box.ROUNDED,
        padding=(0, 2),
    ))


def progress_bar(watched: int, total: int, width: int = 24) -> str:
    """Return a terminal-safe segmented progress bar."""
    if total <= 0:
        return f"[reelq.dim]{'·' * width}[/]"
    filled = min(width, max(0, int((watched / total) * width)))
    pct = watched / total
    color = "reelq.success" if pct >= 0.75 else "reelq.violet" if pct >= 0.4 else "reelq.warning"
    return f"[{color}]{'█' * filled}[/][reelq.dim]{'░' * (width - filled)}[/]"


def stat_card(label: str, value: str, detail: str = "") -> Panel:
    body = f"[reelq.title]{value}[/]"
    if detail:
        body += f"\n[reelq.muted]{detail}[/]"
    return Panel(body, title=f"[reelq.muted]{label}[/]", border_style="reelq.dim", box=box.ROUNDED, padding=(0, 1))


def stat_cards(*cards: Panel):
    """Print responsive stat cards for dashboards."""
    console.print(Columns(list(cards), equal=True, expand=True, padding=(0, 1)))
