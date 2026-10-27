# GRIMWATCH

> *Your markdown movie archive, with a brain.*

A terminal CLI for the films you mean to watch and the ones you'll never forget.
Grimwatch keeps your list in plain markdown — no database, no account, no cloud.
AI handles classification and recommendations. You handle the watching.

---

## Install

```
pip install grimwatch
```

Requires Python 3.10+. Works on Windows, macOS, Linux.

**With Groq cloud AI:**
```
pip install "grimwatch[groq]"
```

**With a local model (Ollama, LM Studio):** base install is enough — no extra packages needed.

---

## Quickstart

```
grimwatch                          # open the interactive archive
grimwatch setup                    # connect your list file + AI backend
grimwatch add "Stalker (1979)"     # add a single film
grimwatch add my-list.txt          # batch import from a text file
grimwatch suggest --mood "bleak"   # AI recommendation from your backlog
grimwatch tonight                  # one pick, right now
grimwatch stats                    # progress, coverage gaps, decade heatmap
grimwatch stats --ai               # same + AI blind-spot analysis
```

---

## Commands

| Key | Command    | What it does                                        |
| --- | ---------- | --------------------------------------------------- |
| 1   | `add`      | Add a film or batch-import from a `.txt`/`.md` file |
| 2   | `import`   | Import a raw list — no genres or years needed       |
| 3   | `watch`    | Mark a film watched (or unwatched with `--undo`)    |
| 4   | `suggest`  | AI picks from your unwatched list                   |
| 5   | `tonight`  | One pick for tonight, right now                     |
| 6   | `random`   | Completely random unwatched film                    |
| 7   | `search`   | Search by title, keyword, or year                   |
| 8   | `list`     | Browse your list genre by genre                     |
| 9   | `stats`    | Progress, coverage gaps, decade heatmap             |
| 10  | `classify` | Classify a separate import/list file                |
| 11  | `merge`    | Merge a second list into yours                      |
| 12  | `dedup`    | Remove duplicates                                   |
| 13  | `setup`    | Change list path, AI backend, or API keys           |
| 14  | `about`    | Who made this and why                               |

---

## How the list works

Grimwatch reads and writes a plain markdown file. Example format:

```
## Horror

- [ ] Suspiria (1977)
- [x] The Wicker Man (1973)

## Crime

- [ ] Memories of Murder (2003)
```

Your list file, your format. Grimwatch stays out of the way.

---

## AI features

AI is optional. Every non-AI command works without any key configured.

### Backends

Grimwatch supports three AI backends, configured via `grimwatch setup`:

**Groq (cloud)** — free tier available at [console.groq.com](https://console.groq.com). Fast, no local setup.

```
pip install "grimwatch[groq]"
```

**Local model** — any runtime with an OpenAI-compatible API: Ollama, LM Studio, or similar. Uses stdlib only, no extra packages.

```
# Ollama example — start your model first:
ollama run llama3

# Then in grimwatch setup, choose option 2 and enter:
# Base URL:   http://localhost:11434/v1
# Model name: llama3
```

**None** — classification and suggestions are disabled; everything else works.

### What AI does

- **Classification** — adds genres and years when you import a raw list
- **suggest** — picks from your backlog based on mood, similarity, or contrast
- **tonight** — one confident pick, no arguments needed
- **stats --ai** — identifies shelves you've been avoiding and why

---

## Coverage gap analysis

`grimwatch stats` shows a COVERAGE GAPS panel automatically — no AI call, no API key needed. It ranks your shelves by how far behind you are relative to their size, so a large neglected shelf surfaces above a small one with the same raw coverage ratio.

```
grimwatch stats        # instant, offline
grimwatch stats --ai   # adds AI commentary at the end
```

---

## Configuration

Config is stored at `~/.grimwatch/config.json`. Run `grimwatch setup` to change anything.

Environment variable override: `GRIMWATCH_CONFIG_DIR`

---

## Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](.github/CONTRIBUTING.md).

---

## License

MIT — see [LICENSE](LICENSE).

Built by [Nirmal](https://github.com/Nirmal2OO8).
