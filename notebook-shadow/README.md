# notebook-shadow module (optional)

graphify's extractor understands `.py`/`.pyi` (via Python's `ast`), but has no
awareness of Jupyter `.ipynb` JSON — so notebook code is invisible to the graph.
`notebook_graphify_shadow.py` closes that gap: it mirrors each notebook's **code
cells** into a hidden, derived, never-hand-edited `.py` shadow under
`notebooks/.graphify_shadow/`, which graphify then indexes like any other source
file.

**Only code cells are mirrored — never cell outputs** (which could contain data).
The shadows are gitignored regenerable build output.

## What setup.sh does with it
If your repo contains `.ipynb` files, `setup.sh`:
1. copies this script to `tools/notebook_graphify_shadow.py`,
2. installs a `PreToolUse` hook that runs `... shadow.py hook` before any
   `graphify` Bash command (keeps shadows fresh automatically),
3. gitignores `notebooks/.graphify_shadow/`.

## Subcommands
- `python tools/notebook_graphify_shadow.py sync` — regenerate stale shadows for
  all notebooks (run this before a manual `graphify update .` in a terminal).
- `python tools/notebook_graphify_shadow.py resolve <shadow_file> <line>` — map a
  shadow line back to the real notebook path, cell id, and line-within-cell.
- `python tools/notebook_graphify_shadow.py hook` — the PreToolUse entry point
  (reads JSON from stdin; used by the hook, not by hand).

## Assumptions
- Notebooks live in `notebooks/` and the script lives in `tools/` (it resolves
  the repo root as its parent's parent). If your notebooks are elsewhere, adjust
  the script's `REPO_ROOT` / notebook discovery to match.
