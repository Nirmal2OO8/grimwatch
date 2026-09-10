"""Config: stored at ~/.grimwatch/config.json"""

import json
import os
import urllib.request
from pathlib import Path
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from .ui import console, unquote

_CONFIG_OVERRIDE = os.environ.get("GRIMWATCH_CONFIG_DIR")
CONFIG_DIR = Path(_CONFIG_OVERRIDE).expanduser() if _CONFIG_OVERRIDE else Path.home() / ".grimwatch"
CONFIG_FILE = CONFIG_DIR / "config.json"

FALLBACK_MODEL = "openai/gpt-oss-120b"
RECOMMENDATION_HISTORY_LIMIT = 40


def _archive_history_id(path: str) -> str:
    """Keep recommendation cooldowns scoped to the active archive."""
    try:
        return str(Path(path).expanduser().resolve()).casefold()
    except OSError:
        return str(path).casefold()


def recent_recommendation_titles(cfg: dict, archive_path: str, limit: int = 12) -> list[str]:
    """Return the newest shown titles for this archive, without trusting config shape."""
    history = cfg.get("recommendation_history", [])
    if not isinstance(history, list):
        return []
    archive_id = _archive_history_id(archive_path)
    titles = [
        item.get("title", "").strip()
        for item in history
        if isinstance(item, dict)
        and item.get("archive") == archive_id
        and isinstance(item.get("title"), str)
        and item.get("title").strip()
    ]
    return titles[-max(0, limit):]


def record_recommendations(cfg: dict, archive_path: str, titles: list[str]) -> None:
    """Persist shown recommendations so repeated commands do not repeat a pick."""
    archive_id = _archive_history_id(archive_path)
    existing = cfg.get("recommendation_history", [])
    history = [item for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
    normalized_new = {" ".join(title.casefold().split()) for title in titles if isinstance(title, str)}
    history = [
        item
        for item in history
        if not (
            item.get("archive") == archive_id
            and " ".join(str(item.get("title", "")).casefold().split()) in normalized_new
        )
    ]
    history.extend(
        {"archive": archive_id, "title": title.strip()}
        for title in titles
        if isinstance(title, str) and title.strip()
    )
    cfg["recommendation_history"] = history[-RECOMMENDATION_HISTORY_LIMIT:]
    save_config(cfg)


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        console.print(f"[red]Could not read config:[/] {CONFIG_FILE}")
        return {}
    return data if isinstance(data, dict) else {}


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def _fetch_live_models(api_key: str) -> list:
    """Hit Groq's models endpoint and return text-capable model IDs."""
    try:
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
        skip = {"whisper", "guard", "tts", "speech", "orpheus", "vision"}
        models = [
            m["id"] for m in data.get("data", [])
            if not any(s in m["id"].lower() for s in skip)
        ]
        return sorted(models)
    except Exception:
        return []


def get_config() -> dict:
    cfg = load_config()
    cfg = migrate_model(cfg)
    # An API key is optional. Re-running the wizard on every command after a
    # user deliberately skipped AI made the non-AI workflow unusable.
    if not cfg.get("list_path"):
        console.print()
        console.print(Panel(
            "[bold #f4d6a0]WELCOME TO GRIMWATCH[/]\n\n"
            "Your list is the save file. Let's connect it.\n"
            "AI is optional and can be added later with [bold]grimwatch setup[/].",
            border_style="#d33b57",
            padding=(1, 2),
        ))
        cfg = run_setup(cfg)
    return cfg


def run_setup(existing: dict = None):
    cfg = existing or load_config() or {}

    console.print()
    console.print(Panel("[bold #f4d6a0]GRIMWATCH SETUP[/]", border_style="#d33b57", padding=(0, 2)))
    console.print()

    # ── List file ──────────────────────────────────────────────────────────────
    current_list = cfg.get("list_path", "")
    console.print("[bold]Step 1 of 4[/] — Your movie list file")
    console.print(
        "[dim]Path to your markdown list with genres and checkboxes.[/]\n"
        "[dim]Example: C:\\Users\\you\\Documents\\movies.txt[/]\n"
    )
    if current_list:
        console.print(f"[dim]Current: {current_list}[/]")

    while True:
        path = unquote(Prompt.ask("  Path to your list file", default=current_list or "", console=console))
        if not path:
            console.print("  [red]Path cannot be empty.[/]")
            continue
        p = Path(path).expanduser()
        if p.exists() and not p.is_file():
            console.print("  [red]That path is a directory. Choose a file path.[/]")
            continue
        if not p.exists():
            create = Confirm.ask(f"  [yellow]File not found.[/] Create it at {path}?", default=False, console=console)
            if create:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.touch()
                console.print(f"  [green]Created:[/] {path}")
                cfg["list_path"] = str(p.resolve())
                break
        else:
            cfg["list_path"] = str(p.resolve())
            console.print(f"  [green]✓[/] Found: {p}")
            break

    console.print()

    # ── Groq API key ───────────────────────────────────────────────────────────
    current_key = cfg.get("groq_api_key", "")
    console.print("[bold]Step 2 of 4[/] — Groq API key")
    console.print(
        "[dim]Free at [link=https://console.groq.com]console.groq.com[/link][/]\n"
        "[dim]Used for AI classification and suggestions.[/]\n"
    )
    if current_key:
        masked = current_key[:8] + "..." + current_key[-4:]
        console.print(f"[dim]Current: {masked}[/]")

    while True:
        key = Prompt.ask("  Groq API key", default=current_key or "", password=True, console=console).strip()
        if not key:
            skip = Confirm.ask("  Skip AI features for now?", default=False, console=console)
            if skip:
                break
        else:
            cfg["groq_api_key"] = key
            console.print("  [green]✓[/] Key saved.")
            break

    console.print()

    # ── Optional metadata fallbacks ───────────────────────────────────────────
    console.print("[bold]Step 3 of 4[/] — Optional metadata fallback keys")
    console.print(
        "[dim]TMDb and OMDb are only used when local cache and Wikidata cannot "
        "verify a title. Press Enter to skip either one.[/]\n"
    )
    for setting, label, url in (
        ("tmdb_api_key", "TMDb", "https://www.themoviedb.org/settings/api"),
        ("omdb_api_key", "OMDb", "https://www.omdbapi.com/apikey.aspx"),
    ):
        current_fallback_key = str(cfg.get(setting, "")).strip()
        if current_fallback_key:
            masked = current_fallback_key[:4] + "..." + current_fallback_key[-4:]
            console.print(f"[dim]{label} key saved: {masked}[/]")
        console.print(f"[dim]{label} key: [link={url}]{url}[/link][/]")
        fallback_key = Prompt.ask(f"  {label} API key (optional)", default="", password=True, console=console).strip()
        if fallback_key:
            cfg[setting] = fallback_key
            console.print(f"  [green]✓[/] {label} fallback enabled.")

    console.print()

    # ── Model — fetch live from Groq ───────────────────────────────────────────
    if not cfg.get("groq_api_key"):
        cfg.pop("groq_model", None)
        console.print("[bold]Step 4 of 4[/] — AI disabled for now")
        console.print("  [dim]You can add a key and choose a model later with grimwatch setup.[/]")
        cfg["groq_model"] = FALLBACK_MODEL
    else:
        console.print("[bold]Step 4 of 4[/] — Groq model")
        live_models = []
        with console.status("  [dim]Fetching available models from Groq…[/]"):
            live_models = _fetch_live_models(cfg["groq_api_key"])

        if live_models:
            console.print(f"  [dim]Found {len(live_models)} model(s) on your account:[/]\n")
            for i, m in enumerate(live_models, 1):
                console.print(f"  [cyan]{i}.[/] {m}")
            console.print(f"  [cyan]{len(live_models)+1}.[/] Enter custom model ID\n")
            choices = [str(i) for i in range(1, len(live_models) + 2)]
            pick = Prompt.ask("  Pick", choices=choices, default="1", console=console)
            if pick == str(len(live_models) + 1):
                cfg["groq_model"] = Prompt.ask("  Model ID", console=console).strip()
            else:
                cfg["groq_model"] = live_models[int(pick) - 1]
        else:
            console.print("[dim]Could not fetch live models (check key or network).[/]")
            console.print(f"[dim]See current models at console.groq.com/docs/models[/]\n")
            current_model = cfg.get("groq_model", FALLBACK_MODEL)
            cfg["groq_model"] = Prompt.ask("  Model ID", default=current_model, console=console).strip()

    console.print(f"  [green]✓[/] Using: {cfg['groq_model']}")

    save_config(cfg)
    console.print()
    console.print(Panel(
        "[bold #9fce78]▣ SETUP COMPLETE[/]\n\n"
        "Run [bold #f4d6a0]grimwatch[/] to open the archive.\n"
        "Start with [bold #b58cff]grimwatch add \"Stalker (1979)\"[/] to add your first film.",
        border_style="#9fce78",
        padding=(1, 2),
    ))
    console.print()
    return cfg


def migrate_model(cfg: dict) -> dict:
    """Auto-fix known-dead model IDs."""
    dead = {
        "llama-3.1-70b-versatile",
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "llama-3.3-70b-specdec",
        "qwen/qwen3-27b",
    }
    if cfg.get("groq_model") in dead:
        cfg["groq_model"] = FALLBACK_MODEL
        save_config(cfg)
    return cfg
