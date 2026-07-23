## graphify

This project has a knowledge graph at `graphify-out/` (gitignored, regenerable),
built by the third-party tool `graphifyy`.

Rules:
- For codebase questions, run `graphify query "<question>"` first when
  `graphify-out/graph.json` exists — it returns a scoped subgraph, usually far
  smaller than raw grep or file browsing. Use `graphify path "<A>" "<B>"` for
  relationships and `graphify explain "<concept>"` for a focused concept.
- Read `graphify-out/GRAPH_REPORT.md` only for broad architecture review.
- After modifying code, run `graphify update .` to keep the graph current
  (AST-only, offline, no API cost).

Privacy: the scan is code-only (see `.graphifyignore`) and graphify makes no
network calls here — no data leaves this machine. Semantic extraction of
docs/images and community naming are the only LLM steps, and both are off by
default (see the repo_map README (https://github.com/aarjain1696/repo_map) to turn them on later).
