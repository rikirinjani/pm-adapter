# Contributing to PM-Adapter

## Ways to contribute

- **Bug reports & feature requests** — open a GitHub issue
- **Code changes** — fork, branch, PR to `master`
- **Domain schemas** — submit new schemas for hospital, gaming, finance, coding, etc.
- **Adapter modes** — propose new expansion modes (graph memory, keyword index, etc.)

## Guidelines

- Adapter is pure deterministic schema lookup — no LLM calls
- All expansion modes must be deterministic (same frame + same schema = same output)
- ECC mode (`use_ecc=True`) must correct single-bit errors silently, raise only on unrecoverable corruption
- New features need a test in `tests/test_adapter.py` before merging
- Run full test suite before PR: `pytest tests/test_adapter.py -v` (must pass 12/12)
- By contributing, you agree your contributions are licensed under Apache-2.0 (code) or CC-BY-4.0 (schemas)

## PR process

1. Open an issue describing the change
2. Fork, branch, implement
3. Add or update tests
4. Run full suite (must pass 12/12)
5. PR to `master` with a summary

## Schema contributions

Domain schemas follow the format in `pm_adapter/schemas/default.json`:
```json
{
  "name": "my_domain",
  "state_width": 8,
  "fields": [
    {"byte": 0, "name": "my_field", "values": {"0": "label_a", "1": "label_b"}}
  ]
}
```
Submit domain schemas as PRs to `pm_adapter/schemas/`. Include a test that decodes a sample frame through your schema.
