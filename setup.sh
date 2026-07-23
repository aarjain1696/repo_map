#!/usr/bin/env bash
# repo_map_kit — offline, private, code-only graphify setup + hooks.
#
# Configures the third-party tool `graphifyy` (https://github.com/safishamsi/graphify,
# by safishamsi) for the CURRENT git repo, with a privacy-first, code-only scan
# and no semantic extraction. Idempotent: safe to re-run. See README.md.
set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }

say "repo_map_kit → configuring repo: $ROOT"

# --- 1. Ensure the graphify CLI (pip/uv package: 'graphifyy') --------------
if ! command -v graphify >/dev/null 2>&1; then
  echo "graphify CLI not found (package name: 'graphifyy')."
  printf "Install now? pick installer [uv/pipx/pip/N]: "
  read -r ans || true
  case "${ans:-N}" in
    uv)   uv tool install graphifyy ;;
    pipx) pipx install graphifyy ;;
    pip)  pip install graphifyy ;;
    *)    echo "→ Skipped. Install 'graphifyy' yourself, then re-run setup.sh."; exit 1 ;;
  esac
else
  note "✓ graphify present: $(command -v graphify)"
fi

# --- 2. Detect notebooks (drives the optional module + its hook) -----------
HAS_NB=0
if find . -path ./.git -prune -o -name '*.ipynb' -print 2>/dev/null | grep -q .; then HAS_NB=1; fi
HAS_NB_MODULE=0
[ -f "$KIT_DIR/notebook-shadow/notebook_graphify_shadow.py" ] && HAS_NB_MODULE=1

# --- 3. Optional notebook-shadow module ------------------------------------
INSTALL_NB=0
if [ "$HAS_NB" = 1 ] && [ "$HAS_NB_MODULE" = 1 ]; then
  say "Notebooks detected → installing notebook-shadow module"
  mkdir -p tools
  cp "$KIT_DIR/notebook-shadow/notebook_graphify_shadow.py" tools/
  note "→ tools/notebook_graphify_shadow.py"
  INSTALL_NB=1
elif [ "$HAS_NB" = 1 ]; then
  note "(notebooks present, but module not bundled — skipping)"
else
  note "(no notebooks — skipping notebook module)"
fi

# --- 4. Code-only, privacy-first .graphifyignore ---------------------------
say "Writing .graphifyignore (code-only scan scope)"
[ -f .graphifyignore ] && cp .graphifyignore .graphifyignore.bak && note "backed up existing → .graphifyignore.bak"

CANDIDATES="src lib app notebooks packages pkg cmd internal source"
FOUND=""
for d in $CANDIDATES; do [ -d "$d" ] && FOUND="$FOUND $d"; done

{
  echo "# repo_map_kit: code-only, privacy-first scan scope."
  echo "# Ignore everything, then re-include only opted-in source dirs."
  echo "# Nothing is indexed (or sent anywhere) unless allowed below."
  echo ""
  echo "*"
  echo ""
  if [ -n "$FOUND" ]; then
    echo "# source directories detected in this repo:"
    for d in $FOUND; do echo "!$d/"; echo "!$d/**"; done
  else
    echo "# No common source dir auto-detected — add yours here, e.g.:"
    echo "# !src/"
    echo "# !src/**"
  fi
  echo ""
  echo "# Belt-and-suspenders: never index secrets / private data,"
  echo "# even if nested inside an allowed dir above."
  echo "data/"
  echo "**/data/"
  echo ".env*"
  echo "**/*secret*"
  echo "**/*.key"
  echo "**/*.pem"
  [ "$INSTALL_NB" = 1 ] && echo "notebooks/.graphify_shadow/manifest.json"
} > .graphifyignore
note "scoped to:${FOUND:- (none detected — edit .graphifyignore)}"

# --- 5. Merge hooks into .claude/settings.json -----------------------------
say "Wiring hooks into .claude/settings.json"
printf "Also add an offline auto-refresh (Stop) hook that runs 'graphify update .' after each turn? [y/N]: "
read -r fresh || true
FRESH=0; case "${fresh:-N}" in y|Y) FRESH=1;; esac

mkdir -p .claude
INSTALL_NB="$INSTALL_NB" FRESH="$FRESH" python3 - <<'PY'
import json, os, pathlib
p = pathlib.Path(".claude/settings.json")
cfg = {}
if p.exists():
    try:
        cfg = json.loads(p.read_text() or "{}")
    except Exception:
        p.rename(".claude/settings.json.bak"); cfg = {}
hooks = cfg.setdefault("hooks", {})

def ensure(event, matcher, command):
    arr = hooks.setdefault(event, [])
    for entry in arr:
        for h in entry.get("hooks", []):
            if h.get("command") == command:
                return False
    entry = {"hooks": [{"type": "command", "command": command}]}
    if matcher:
        entry["matcher"] = matcher
    arr.append(entry)
    return True

changed = []
if os.environ.get("INSTALL_NB") == "1":
    cmd = 'cd "$CLAUDE_PROJECT_DIR" && python3 tools/notebook_graphify_shadow.py hook 2>/dev/null || true'
    if ensure("PreToolUse", "Bash", cmd): changed.append("PreToolUse:notebook-shadow-sync")
if os.environ.get("FRESH") == "1":
    cmd = 'cd "$CLAUDE_PROJECT_DIR" && graphify update . >/dev/null 2>&1 || true'
    if ensure("Stop", None, cmd): changed.append("Stop:graphify-update")

p.write_text(json.dumps(cfg, indent=2) + "\n")
print("  hooks added:", ", ".join(changed) if changed else "(none new — already present)")
PY

# --- 6. Append graphify conventions to CLAUDE.md ---------------------------
say "Adding graphify conventions to CLAUDE.md"
touch CLAUDE.md
if grep -q '^## graphify' CLAUDE.md; then
  note "(CLAUDE.md already has a ## graphify section — leaving it)"
else
  printf '\n' >> CLAUDE.md
  cat "$KIT_DIR/templates/CLAUDE.graphify.md" >> CLAUDE.md
  if [ "$INSTALL_NB" = 1 ] && [ -f "$KIT_DIR/templates/CLAUDE.graphify.notebook.md" ]; then
    cat "$KIT_DIR/templates/CLAUDE.graphify.notebook.md" >> CLAUDE.md
  fi
  note "→ appended to CLAUDE.md"
fi

# --- 7. gitignore the generated outputs ------------------------------------
touch .gitignore
grep -q '^graphify-out/' .gitignore 2>/dev/null || printf '\n# graphify output (regenerable)\ngraphify-out/\n' >> .gitignore
if [ "$INSTALL_NB" = 1 ]; then
  grep -q 'notebooks/.graphify_shadow/' .gitignore 2>/dev/null || printf '# graphify notebook shadows (generated)\nnotebooks/.graphify_shadow/\n' >> .gitignore
fi

# --- 8. First offline build ------------------------------------------------
say "Building the graph (offline, AST-only)…"
[ "$INSTALL_NB" = 1 ] && { python3 tools/notebook_graphify_shadow.py sync 2>/dev/null || true; }
if graphify update . 2>/dev/null; then
  note "✓ graph at graphify-out/graph.json"
else
  note "! 'graphify update .' needs an initial graph — run the /graphify skill once,"
  note "  or 'graphify update . --no-label' for a fully offline first build."
fi

# --- 9. Summary ------------------------------------------------------------
say "Done. Privacy posture:"
cat <<'EOF'
  • Scan is CODE-ONLY (.graphifyignore) — data/, secrets, .env excluded.
  • graphify reads no API keys here → no network calls; nothing leaves this machine.
  • Community naming (if used) runs via your host agent; add --no-label to skip it.
  • Enable semantic extraction LATER: widen .graphifyignore to include docs and set
    GEMINI_API_KEY. See README.md → "Turning semantic extraction on".

  Query:   graphify query "how does X work?"
  Refresh: graphify update .        (offline, free)
EOF
