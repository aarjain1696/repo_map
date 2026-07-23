
**Notebook coverage (via generated shadow files).** graphify can't parse
`.ipynb` directly, so `tools/notebook_graphify_shadow.py` mirrors each
notebook's code cells into a hidden, auto-regenerated `.py` file under
`notebooks/.graphify_shadow/` (gitignored — regenerable build output, never
committed). A `PreToolUse` hook re-syncs these before any Bash command that
invokes `graphify`, so `graphify update .` always sees fresh shadows with no
manual step. Every shadow file starts with a `DO NOT EDIT — GENERATED` banner
and carries a `# --- cell[index=<i>] id=<cell_id> notebook=<path> ---` marker
above each translated cell — so if a `graphify` result's `source_file` is under
`notebooks/.graphify_shadow/`, that's a shadow copy, not the real file. Trace it
back with:
```
python tools/notebook_graphify_shadow.py resolve <shadow_file> <line>
```
Then edit the NOTEBOOK, never the shadow (it is overwritten on every sync).
