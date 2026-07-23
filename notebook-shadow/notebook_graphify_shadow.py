#!/usr/bin/env python3
"""
notebook_graphify_shadow.py — notebook -> graphify shadow file generator
=========================================================================
graphify's extractor only understands `.py`/`.pyi` files parsed with the
`ast` module; it has zero awareness of Jupyter's `.ipynb` JSON format, so
notebook code is invisible to the knowledge graph today. This script closes
that gap by generating one hidden, **derived, never hand-edited** shadow
`.py` file per notebook under `notebooks/.graphify_shadow/`, containing
each code cell's source translated into something `ast.parse()`-clean,
with a banner comment above every cell recording its notebook path, cell
`id`, and 0-based cell index. graphify indexes these shadow files like any
other `.py` file; the `resolve` subcommand below maps a graph hit's shadow
line back to the real notebook cell it came from.

Shadow files and their manifest are gitignored — they are build output,
regenerated from the notebooks on disk, and never contain hand-written code.

Three subcommands:

    python tools/notebook_graphify_shadow.py sync
        Regenerate any shadow file whose notebook's code has changed since
        the last sync, prune shadows for deleted/renamed notebooks, and
        print a one-line created/updated/unchanged/pruned summary. This is
        the default action if no subcommand is given. A repeat run with
        nothing changed is a fast no-op: it touches no file on disk.

    python tools/notebook_graphify_shadow.py resolve <shadow_file> <line>
        Given a 1-based line number inside a shadow file, print the
        notebook path, cell id, cell index, and 1-based line-within-cell
        that line came from.

    python tools/notebook_graphify_shadow.py hook
        Intended to run as a Claude Code PreToolUse hook (Bash matcher).
        Reads the hook's tool-input JSON from stdin; if the Bash command
        does not mention `graphify` as a whole word, exits immediately
        with no output (this runs before every Bash call once wired, so
        the non-matching path must be cheap). If the command does mention
        `graphify`, it runs the same in-process logic as `sync` so the
        graph is always indexing fresh notebook content. Never shells out
        to `graphify` itself (no recursion), and never exits non-zero, so
        it can never block the Bash call it is guarding.

Line-mapping formula (how `resolve` works): every code cell is preceded by
a one-line banner `# --- cell[index=<i>] id=<cell_id> notebook=<path> ---`.
For a target shadow-file line `L`, scan upward for the nearest banner at
line `B`; `line_within_cell = L - B` (the cell's first source line sits at
`B + 1`, i.e. `line_within_cell == 1`). The banner lives in the same file
as the content it describes, so this mapping can never drift out of sync
independently of a regeneration.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# [AGENT: coder] [2026-07-18] shadow-file paths, banner/placeholder constants
REPO_ROOT = Path(__file__).resolve().parent.parent
NOTEBOOKS_DIR = REPO_ROOT / "notebooks"
SHADOW_DIR = NOTEBOOKS_DIR / ".graphify_shadow"
MANIFEST_PATH = SHADOW_DIR / "manifest.json"

MAGIC_PLACEHOLDER = "# [ipython magic removed]"
UNPARSEABLE_PLACEHOLDER = "# [unparseable notebook cell — skipped]"

# Matches a `%magic` or `!shell` line after stripping leading whitespace.
MAGIC_LINE_RE = re.compile(r"^\s*[%!]")

# Matches a banner this script writes: "# --- cell[index=0] id=abc notebook=x.ipynb ---"
CELL_BANNER_RE = re.compile(
    r"^# --- cell\[index=(?P<index>\d+)\] id=(?P<id>\S+) notebook=(?P<notebook>.+) ---$"
)

# Word-boundary match so "graphify" inside a longer token (e.g. a path
# component) still counts, but this stays a simple, cheap regex check.
GRAPHIFY_WORD_RE = re.compile(r"\bgraphify\b")


# [AGENT: coder] [2026-07-18] recursive notebook discovery, skipping Jupyter checkpoints
def find_notebooks(notebooks_dir: Path) -> list[Path]:
    """Find every ``.ipynb`` file under ``notebooks_dir``, recursively.

    Parameters
    ----------
    notebooks_dir : Path
        Root directory to search (normally ``notebooks/``).

    Returns
    -------
    list[Path]
        Absolute paths, sorted for deterministic output. Files inside any
        ``.ipynb_checkpoints`` directory are excluded — those are Jupyter's
        own autosave copies, not real notebooks, and would otherwise be
        double-counted as separate "notebooks" to shadow.
    """
    return sorted(
        p
        for p in notebooks_dir.rglob("*.ipynb")
        if ".ipynb_checkpoints" not in p.parts
    )


# [AGENT: coder] [2026-07-18] read a notebook's raw cell list
def read_notebook_cells(nb_path: Path) -> list[dict]:
    """Read a notebook file and return its full ``cells`` list.

    Parameters
    ----------
    nb_path : Path
        Path to a ``.ipynb`` file.

    Returns
    -------
    list[dict]
        The notebook's ``cells`` array exactly as stored in the notebook
        JSON (code and markdown cells together, in document order). Never
        mutated and never written back — this script only reads notebooks.
    """
    with nb_path.open("r", encoding="utf-8") as f:
        notebook = json.load(f)
    return notebook.get("cells", [])


# [AGENT: coder] [2026-07-18] join a cell's source into one string
def cell_source_text(cell: dict) -> str:
    """Join a cell's ``source`` field into a single string.

    Parameters
    ----------
    cell : dict
        One notebook cell dict.

    Returns
    -------
    str
        The cell's source text. nbformat usually stores ``source`` as a
        list of line strings (each already ending in ``\\n`` except
        possibly the last), but some tools write it as a single string —
        both are handled.
    """
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(source)
    return source


# [AGENT: coder] [2026-07-18] neutralize magics, quarantine unparseable cells
def transform_code_lines(source_text: str) -> str:
    """Translate one code cell's raw source into shadow-safe Python.

    Two line-count-preserving transformations are applied, in order:

    1. Any line that is a magic/shell line (starts with ``%`` or ``!``
       after stripping leading whitespace) is replaced with
       ``# [ipython magic removed]`` in the same position.
    2. If the result still fails to parse as a standalone module (some
       other genuine syntax error — e.g. an incomplete scratch cell),
       *every* line is replaced with ``# [unparseable notebook cell —
       skipped]`` instead, so one broken cell can never make the whole
       shadow file fail to parse.

    Both substitutions keep the exact original line count, which is what
    lets ``resolve`` map a shadow line back to a cell line with simple
    subtraction.

    Parameters
    ----------
    source_text : str
        A single code cell's joined source text.

    Returns
    -------
    str
        Shadow-safe text with the same number of lines as ``source_text``
        (``""`` if the cell had no lines at all).

    Notes
    -----
    The magic-line check is a simple per-line prefix heuristic, not a full
    tokenizer — a continuation line like ``    % b`` inside a multi-line
    expression, or a line inside a triple-quoted string that happens to
    start with ``%``/``!``, could be misidentified as a magic line. In the
    rare case that neutralizing it breaks the cell's syntax, step 2 above
    catches it and the whole cell falls back to the "unparseable"
    placeholder rather than corrupting the shadow file. This tradeoff
    matches the spec's literal instruction and keeps the converter simple;
    a real fix would require tokenizing rather than line-scanning.
    """
    lines = source_text.splitlines()
    if not lines:
        return ""

    neutralized = [
        MAGIC_PLACEHOLDER if MAGIC_LINE_RE.match(line) else line for line in lines
    ]
    candidate = "\n".join(neutralized)

    try:
        ast.parse(candidate)
        return candidate
    except Exception:
        return "\n".join(UNPARSEABLE_PLACEHOLDER for _ in lines)


# [AGENT: coder] [2026-07-18] sha256 of concatenated code-cell sources for staleness checks
def compute_code_hash(cells: list[dict]) -> str:
    """Hash a notebook's code-cell content for staleness detection.

    Parameters
    ----------
    cells : list[dict]
        A notebook's full cell list (code and markdown together).

    Returns
    -------
    str
        Hex SHA-256 digest of every code cell's raw source, in document
        order, joined by newlines. Deliberately excludes markdown cells
        and the whole-file bytes (outputs, execution counts, metadata) —
        re-running a cell only changes those, so it must not trigger a
        shadow rewrite.
    """
    code_texts = [
        cell_source_text(cell) for cell in cells if cell.get("cell_type") == "code"
    ]
    concatenated = "\n".join(code_texts)
    return hashlib.sha256(concatenated.encode("utf-8")).hexdigest()


# [AGENT: coder] [2026-07-18] assemble one notebook's full shadow file text
def build_shadow_content(nb_path: Path, notebook_rel_path: str) -> str:
    """Build the complete shadow ``.py`` text for one notebook.

    Parameters
    ----------
    nb_path : Path
        Absolute path to the notebook on disk.
    notebook_rel_path : str
        The notebook's path relative to ``notebooks/``, forward-slashed
        (e.g. ``"archive/quarterly_analysis.ipynb"``), used in the
        header and in every cell banner.

    Returns
    -------
    str
        The full shadow file text, ending in a trailing newline. Markdown
        cells consume a cell index but contribute no banner or content;
        a notebook with zero code cells produces a header-only file; a
        code cell whose source is empty/whitespace-only gets a banner but
        contributes zero content lines.
    """
    cells = read_notebook_cells(nb_path)
    lines = [
        "# === GENERATED FILE — DO NOT EDIT ===",
        f"# Source: notebooks/{notebook_rel_path}",
        "# Regenerate: python tools/notebook_graphify_shadow.py sync",
        "# Resolve a graph hit back to its cell:",
        "#   python tools/notebook_graphify_shadow.py resolve <this file> <line>",
    ]
    for i, cell in enumerate(cells):
        if cell.get("cell_type") != "code":
            continue
        cell_id = cell.get("id", "unknown")
        lines.append(f"# --- cell[index={i}] id={cell_id} notebook={notebook_rel_path} ---")
        transformed = transform_code_lines(cell_source_text(cell))
        if transformed:
            lines.append(transformed)
    return "\n".join(lines) + "\n"


# [AGENT: coder] [2026-07-18] manifest load/save helpers
def load_manifest() -> dict:
    """Load ``manifest.json``, or an empty dict if it doesn't exist yet."""
    if MANIFEST_PATH.exists():
        with MANIFEST_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_manifest(manifest: dict) -> None:
    """Write ``manifest.json``, creating the shadow directory if needed."""
    SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")


# [AGENT: coder] [2026-07-18] core sync: regenerate stale shadows, prune orphans
def sync() -> dict[str, int]:
    """Regenerate stale shadow files, prune orphans, report counts.

    For every notebook under ``notebooks/``, compares its current code
    hash against the manifest's stored hash. A shadow file is (re)written
    only if the hash differs from the manifest (or the shadow file is
    missing) — everything else is left untouched, including its mtime.
    Manifest entries whose notebook no longer exists are pruned along with
    their shadow file. If nothing changed at all (no create/update/prune),
    ``manifest.json`` itself is never opened for writing either, so a
    repeat run is a true no-op on disk.

    Returns
    -------
    dict[str, int]
        ``{"created": ..., "updated": ..., "unchanged": ..., "pruned": ...}``

    Notes
    -----
    Also prints a one-line human-readable summary of the same counts.
    """
    manifest = load_manifest()
    notebooks = find_notebooks(NOTEBOOKS_DIR)
    seen_rel_paths: set[str] = set()

    created = updated = unchanged = pruned = 0
    manifest_changed = False

    for nb_path in notebooks:
        rel_path = nb_path.relative_to(NOTEBOOKS_DIR).as_posix()
        seen_rel_paths.add(rel_path)

        cells = read_notebook_cells(nb_path)
        code_hash = compute_code_hash(cells)

        shadow_path = SHADOW_DIR / Path(rel_path).with_suffix(".py")
        existing_entry = manifest.get(rel_path)
        is_unchanged = (
            existing_entry is not None
            and existing_entry.get("code_hash") == code_hash
            and shadow_path.exists()
        )
        if is_unchanged:
            unchanged += 1
            continue

        content = build_shadow_content(nb_path, rel_path)
        shadow_path.parent.mkdir(parents=True, exist_ok=True)
        shadow_path.write_text(content, encoding="utf-8")

        manifest[rel_path] = {
            "code_hash": code_hash,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        manifest_changed = True
        created += 1 if existing_entry is None else 0
        updated += 1 if existing_entry is not None else 0

    for rel_path in list(manifest.keys()):
        if rel_path in seen_rel_paths:
            continue
        orphan_shadow_path = SHADOW_DIR / Path(rel_path).with_suffix(".py")
        if orphan_shadow_path.exists():
            orphan_shadow_path.unlink()
        del manifest[rel_path]
        manifest_changed = True
        pruned += 1

    if manifest_changed:
        save_manifest(manifest)

    print(
        f"graphify shadow sync: created={created} updated={updated} "
        f"unchanged={unchanged} pruned={pruned}"
    )
    return {"created": created, "updated": updated, "unchanged": unchanged, "pruned": pruned}


# [AGENT: coder] [2026-07-18] resolve a shadow-file line back to its notebook cell
def resolve(shadow_path: Path, line: int) -> None:
    """Print the notebook cell a shadow-file line number came from.

    Parameters
    ----------
    shadow_path : Path
        Path to a shadow ``.py`` file, as it would appear in a graphify
        graph node's ``source_file`` (e.g.
        ``notebooks/.graphify_shadow/quarterly_analysis.py``).
        Resolved against the repo root if not found relative to the
        current working directory.
    line : int
        1-based line number within that shadow file.

    Raises
    ------
    FileNotFoundError
        If ``shadow_path`` cannot be found either as given or relative to
        the repo root.
    ValueError
        If ``line`` is outside the file's actual line range.
    """
    if not shadow_path.exists():
        candidate = REPO_ROOT / shadow_path
        if candidate.exists():
            shadow_path = candidate
        else:
            raise FileNotFoundError(f'shadow file not found: "{shadow_path}"')

    text_lines = shadow_path.read_text(encoding="utf-8").splitlines()
    if line < 1 or line > len(text_lines):
        raise ValueError(f"line {line} is out of range for \"{shadow_path}\" (1-{len(text_lines)})")

    banner_line_no = None
    match = None
    for candidate_line_no in range(line, 0, -1):
        m = CELL_BANNER_RE.match(text_lines[candidate_line_no - 1])
        if m:
            banner_line_no = candidate_line_no
            match = m
            break

    print(f'shadow_file  : "{shadow_path}"')
    print(f"shadow_line  : {line}")
    if match is None:
        print("No cell banner found above this line (header-only file or no code cells).")
        return

    notebook_rel = match.group("notebook")
    print(f'notebook     : "notebooks/{notebook_rel}"')
    print(f"cell_index   : {int(match.group('index'))}")
    print(f"cell_id      : {match.group('id')}")
    print(f"line_in_cell : {line - banner_line_no}")


# [AGENT: coder] [2026-07-18] defensive command extraction from a hook payload
def extract_command(payload: dict) -> str | None:
    """Best-effort extraction of a shell command string from a hook payload.

    Parameters
    ----------
    payload : dict
        Parsed PreToolUse hook stdin JSON.

    Returns
    -------
    str | None
        The command string if one can be found, else ``None``. Checks the
        documented location first (``tool_input.command``), then falls
        back to a recursive scan for any ``"command"`` key, since hook
        payload shapes can vary across Claude Code versions.
    """
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            return command

    def _search(obj: object) -> str | None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "command" and isinstance(value, str):
                    return value
                found = _search(value)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = _search(item)
                if found is not None:
                    return found
        return None

    return _search(payload)


# [AGENT: coder] [2026-07-18] PreToolUse hook entry point (fail-open, never blocks)
def hook() -> None:
    """PreToolUse hook entry point: sync shadows only if the command uses graphify.

    Reads the hook's tool-input JSON from stdin. If the Bash command does
    not mention ``graphify`` as a whole word, returns immediately with no
    output — this runs before every Bash call once wired, so that path
    must be cheap. Otherwise runs the same in-process logic as `sync`.

    This function never raises and never causes a non-zero process exit:
    any failure (malformed stdin, a notebook that can't be read, a disk
    error) is swallowed so the hook can never block the Bash call it is
    guarding. It also never shells out to ``graphify`` itself, so there is
    no risk of the hook recursively re-triggering itself.
    """
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return

    command = extract_command(payload)
    if not command or not GRAPHIFY_WORD_RE.search(command):
        return

    try:
        sync()
    except Exception as exc:
        print(f"notebook_graphify_shadow hook: sync failed ({exc}); continuing anyway", file=sys.stderr)


# [AGENT: coder] [2026-07-18] argparse CLI dispatch
def main(argv: list[str] | None = None) -> int:
    """CLI entry point: dispatch to ``sync`` (default), ``resolve``, or ``hook``.

    Parameters
    ----------
    argv : list[str] | None
        Argument vector to parse; defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        Process exit code (0 on success; 1 on a reportable ``resolve``
        error). ``hook`` always returns 0 by design — see `hook`.
    """
    parser = argparse.ArgumentParser(
        description="Generate graphify-indexable shadow .py files from Jupyter notebooks."
    )
    subparsers = parser.add_subparsers(dest="action")

    subparsers.add_parser("sync", help="Regenerate stale shadow files for all notebooks (default).")

    resolve_parser = subparsers.add_parser(
        "resolve", help="Map a shadow-file line back to its notebook cell."
    )
    resolve_parser.add_argument("shadow_path", type=Path, help="path to a shadow .py file")
    resolve_parser.add_argument("line", type=int, help="1-based line number in that file")

    subparsers.add_parser("hook", help="PreToolUse hook entry point (reads JSON from stdin).")

    args = parser.parse_args(argv)
    action = args.action or "sync"

    if action == "sync":
        sync()
        return 0
    if action == "resolve":
        try:
            resolve(args.shadow_path, args.line)
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0
    if action == "hook":
        hook()
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
