# repo_map

A drop-in setup that wires **graphify** into any repo the way I run it:
**offline, private, code-only, with hooks** — and a one-line switch to turn on
semantic (LLM) extraction later if you ever want it.

> **What this is / isn't.** graphify itself is a third-party tool
> (`graphifyy`, by *safishamsi* — https://github.com/safishamsi/graphify). This
> kit does **not** rebrand or vendor it; it installs and *configures* it. What's
> mine (and shareable) is the config, the hooks, the conventions, and the
> `notebook-shadow/` tool. Please keep graphify's own attribution intact.

---

## Why you'd want it

- **Offline & private by default.** The scan is **code-only** — `data/`, `.env`,
  secrets are excluded — and graphify reads no API keys, so it makes **zero
  network calls**. Nothing about your code leaves your machine.
- **Hooks that keep the graph honest.** Optional auto-refresh after each turn,
  and (for notebook repos) automatic notebook→graph syncing.
- **Semantic extraction stays OFF** until you opt in — then it's one env var +
  one ignore-file edit.

## Quick start

```bash
# 1) Clone this kit once (anywhere):
git clone https://github.com/aarjain1696/repo_map.git

# 2) For EACH repo you want mapped, run setup.sh from that repo's root:
cd /path/to/your-repo
bash /path/to/repo_map/setup.sh

# 3) Query the graph:
graphify query "how does the auth flow work?"
```

`setup.sh` keys off your **current git repo** (`git rev-parse --show-toplevel`),
so it configures whatever repo you run it from — not this kit's own folder. It's
**idempotent** (safe to re-run): it backs up an existing `.graphifyignore` and
*merges* hooks into `.claude/settings.json` rather than clobbering them.

## What `setup.sh` does

1. **Installs graphify** (`graphifyy`) if missing — asks first, lets you pick
   `uv` / `pipx` / `pip`.
2. Writes a **code-only `.graphifyignore`**, auto-detecting common source dirs
   (`src`, `lib`, `app`, `notebooks`, …) and hard-excluding `data/`, `.env`,
   secrets.
3. **Notebook module (auto):** if the repo has `.ipynb` files, installs
   `notebook-shadow/` to `tools/` and wires its sync hook.
4. **Merges hooks** into `.claude/settings.json` (see below).
5. Appends a **graphify conventions block** to `CLAUDE.md`.
6. Gitignores `graphify-out/` (and notebook shadows).
7. Runs one **offline build** to prove it works.
8. Prints your **privacy posture**.

## The hooks

| Hook | Event | When installed | What it does |
|---|---|---|---|
| notebook-shadow sync | `PreToolUse` (Bash) | repo has notebooks | Regenerates notebook→`.py` shadows before any `graphify` command, so the graph always sees fresh notebook code. |
| graphify auto-refresh | `Stop` | opt-in (setup asks) | Runs `graphify update .` (offline, AST-only) when the agent finishes a turn, so the graph never goes stale. |

Both are offline. The canonical JSON is in `templates/settings.hook.json` if you
prefer to merge by hand.

## Privacy posture (the whole point)

- **Code-only corpus** → graphify skips semantic extraction entirely (that step
  only runs for docs/papers/images).
- **No keys read** (`GEMINI_API_KEY` / `GOOGLE_API_KEY` / `ANTHROPIC_API_KEY` /
  `OPENAI_API_KEY`) → no outbound calls from graphify.
- **Community naming** is the only possible LLM touch. With no key set it runs
  via your host agent (e.g. your Claude Code session, on structural names only).
  For a **fully air-gapped** graph, build with `--no-label`:
  ```bash
  graphify update . && graphify cluster-only . --no-label
  ```
  (keeps `Community N` placeholders, no LLM at all.)

## Turning semantic extraction ON (later, optional)

Semantic extraction enriches the graph using the *content* of docs/papers/images
(not just code structure). To enable it:

1. **Widen the scan** — in `.graphifyignore`, re-include the doc dirs you want
   (e.g. `!docs/`, `!docs/**`). Leave `data/`/secrets excluded.
2. **Pick the LLM path:**
   - **Google Gemini** — `export GEMINI_API_KEY=...` (content goes to Google).
     Install extra once: `pip install 'graphifyy[gemini]'`.
   - **Host agent** — run the `/graphify` skill and let your coding agent do the
     extraction (content goes to that agent's provider).
3. Rebuild: `graphify update .` (or `/graphify`).

> Understand the trade-off: semantic extraction **sends the included file
> contents to an LLM provider.** Keep `data/` and secrets out of the allowlist.

## Files

```
repo_map/
  README.md                     ← you are here
  LICENSE                       ← MIT
  setup.sh                      ← idempotent bootstrap
  templates/
    graphifyignore              ← code-only scope (hand-editable fallback)
    settings.hook.json          ← the hooks, for manual merge
    CLAUDE.graphify.md          ← conventions block appended to CLAUDE.md
    CLAUDE.graphify.notebook.md ← notebook addendum (appended if notebooks exist)
  notebook-shadow/
    notebook_graphify_shadow.py ← notebook → graph mirror (optional module)
    README.md
```

## Credits & license

- **graphify** — the engine — is by *safishamsi*:
  https://github.com/safishamsi/graphify (sponsor:
  https://github.com/sponsors/safishamsi). All credit for the graph tech is theirs.
- **This kit** (config, hooks, conventions, `notebook-shadow/`) is MIT-licensed —
  see `LICENSE`.
