# Release Notes — 1.1.0

## What's new

### Local model support

Grimwatch now works with any OpenAI-compatible local runtime — Ollama, LM Studio, or anything else that speaks the `/v1/chat/completions` API. Run `grimwatch setup` and choose option 2. The wizard tests the connection before saving, so you know it works before you leave setup.

Groq is still supported and still the default. If you're already configured, nothing changes.

### `groq` is now an optional dependency

```
pip install grimwatch           # local models only, or no AI
pip install grimwatch[groq]     # Groq cloud backend
```

If you already have `groq` installed it continues to work. This change just means new installs don't pull it in automatically.

### Coverage gap analysis in `grimwatch stats`

The stats command now shows a **COVERAGE GAPS** panel automatically, no API call needed. It tells you which shelves you've been ignoring relative to their size — sorted by a weighted score so a 40-film shelf at 10% watched ranks above a 3-film shelf at the same ratio.

Tiers: **CRITICAL** (large shelf, very low coverage), **SIGNIFICANT**, **MINOR**.

The AI blind-spot analysis that used to run every time is now opt-in:

```
grimwatch stats        # fast, no network call
grimwatch stats --ai   # adds model-powered analysis at the end
```

### Smarter recommendations

`grimwatch suggest` now builds its candidate pool with under-covered shelves over-represented, so if you've barely touched your Horror shelf the model actually sees those films rather than sampling uniformly. You don't need to do anything differently — it just works.

### Film quality scoring (internal)

A new quality scoring module scores films on five dimensions and caches results in the existing SQLite database. This is groundwork for a future `grimwatch quality` command. The cache table is created automatically on first use; your existing data is untouched.

---

## Breaking changes

None. All existing configs, archives, and commands work as before.

---

## Upgrade

```
pip install --upgrade grimwatch
```

Or if you use Groq:

```
pip install --upgrade "grimwatch[groq]"
```
