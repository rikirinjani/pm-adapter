# File Selection Patterns (Seed Data for ArtifactResolver)

Proven file selection patterns from the morse/Pro Memoria pilot (2026-08-14).

## Pattern: Task → Relevant Files

| Task Type | Files Selected | Why |
|-----------|---------------|-----|
| "Count tests in X" | `test_X.py` | Test file contains the answers |
| "What does X do?" | `X.py` or `core.py` | Source code defines behavior |
| "What's the version?" | `pyproject.toml` | Config file has metadata |
| "What's in PLANS.md?" | `PLANS.md` | Plan file has the answers |
| "Compression ratio?" | `README.md` | README has the claims |
| "Fix bug in X" | `X.py` + related tests | Need to see code + verify |
| "Update paper section" | `paper/*.md` + `PLANS.md` | Need current state + plan |

## Selection Heuristics

1. **Explicit file mentions** — task says "in test_handshake.py" → include it
2. **Module/function mentions** — task says "bits_to_morse" → find source file
3. **Question type** — "what/where/how" → source code; "why" → decisions + source
4. **Config queries** — "version/dependencies" → pyproject.toml, setup.py
5. **Plan queries** — "pending/todo" → PLANS.md, TODO comments

## Token Budget Reality

- Small file (<250 lines): include full content
- Large file (>250 lines): first 200 + last 50 lines
- Typical packet: 200-2000 tokens (state + decisions + 1-3 files)
- Max budget: 8K tokens (leave room for reasoning + output)

## When to Build ArtifactResolver

When you find yourself saying "I wish the assembler knew to include X" three times in one day.
