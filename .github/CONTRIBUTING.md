# Contributing

If you're here because you actually use `grimwatch` and want to improve it — great, welcome.

A few things to know before you start:

## The core principle

The list file is the source of truth. Everything `grimwatch` does should be legible as a change to a plain markdown file. A local, derived cache of verified bibliographic metadata is an allowed exception when it improves reliability: it must be rebuildable, must never become the canonical movie list or a store of genre decisions, and must not replace the Markdown archive. Other features that introduce a second canonical file format or database are probably the wrong approach.

## Setting up

```bash
git clone https://github.com/Nirmal2OO8/grimwatch
cd grimwatch
pip install -e .
grimwatch setup
```

That's it. No build step, no Docker, no environment variables beyond the config file.

## What's worth contributing

- Bug fixes, especially around the markdown parser handling edge cases
- New `suggest` modes or smarter recommendation logic  
- Better handling of non-English titles and alternate title formats
- Performance improvements for large lists (500+ films)
- Windows path handling edge cases

## What's probably not worth contributing

- A web UI or Electron wrapper — that's a different project
- Switching the AI backend to something other than Groq — open an issue first
- Adding a second canonical database for lists or classifications — the Markdown archive is the database; a derived metadata cache is different

## Pull requests

Keep them focused. One thing per PR. If you're fixing a bug, just fix the bug. If you want to add a feature, open an issue first so we can agree it's a good idea before you spend time on it.

Write a clear commit message. "Fixed bug" tells me nothing. "Fix parser skipping films with parentheses in the title" tells me everything.
