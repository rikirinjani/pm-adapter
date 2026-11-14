# PM-Adapter

**Schema-driven PM-1 frame expansion.** Takes Pro Memoria (PM-1) Morse frames and expands them into human-readable English, structured JSON, semantic descriptions, and multi-agent coordinator compatible state change events.

Zero LLM calls. Pure deterministic schema lookup. No part of PM-1 — consumes it.

## Install

```
pip install git+https://github.com/rikirinjani/pm-adapter.git
```

Requires `pro-memoria`, installed automatically as a git dependency.

## Usage

```python
from pm_adapter import Adapter, load_schema

schema = load_schema("default")
adapter = Adapter(schema)

frame = ".......-......-.......--...............................-........"

# Expand to English
print(adapter.to_english(frame))
# "agent_type: fixer, phase: act, confidence: high, outcome: pass"

# Expand to structured JSON
print(adapter.to_json(frame))
# {"agent_type": "fixer", "phase": "act", ...}

# Semantic description
print(adapter.to_semantic(frame))
# "Agent state: agent type set to fixer, phase set to act..."

# Multi-agent event bus events
print(adapter.to_events(frame))
# [{"event": "state_init", "field": "agent_type", "value": "fixer"}, ...]
```

## Modes

| Mode | Output | Use |
|------|--------|-----|
| `to_english()` | Human-readable sentence | Debugging, human-facing dashboards |
| `to_json()` | Structured dict | Programmatic consumption |
| `to_semantic()` | Dense searchable description | Vector embedding for semantic memory |
| `to_events()` | State change event list | Multi-agent event bus, deterministic scheduling |

## Why a separate adapter?

The adapter is intentionally NOT part of PM-1. PM-1 transports bytes — it doesn't know what they mean. The adapter knows the schema. This separation keeps PM-1 small (~750 lines, zero deps) while letting every domain define its own interpretation layer.

## Architecture

```
PM-1 Frame (128-char Morse)
     │
     ▼
Adapter.decode(frame, schema)   ← deterministic schema lookup, zero LLM calls
     │
     ├── to_english()           ← human-readable
     ├── to_json()              ← structured dict
     ├── to_semantic()          ← vector-embeddable description
     └── to_events()            ← coordinator-compatible state changes
```

## Domain adapters

The default schema maps 8-byte agent state. For domain-specific use, create separate packages with their own schemas:

| Domain | Package | Schema maps |
|--------|---------|-------------|
| Hospital | pm-adapter-hospital | ICD codes, lab status, pharmacy state |
| Game NPC | pm-adapter-npc | Mood, threat, objective, inventory |
| Coding IDE | pm-adapter-coding | File, task, confidence, branch |

All import `pm_adapter.Adapter` — just swap the schema.

## Related

- [Pro Memoria (PM-1)](https://github.com/rikirinjani/pro-memoria) — the transport protocol
- [AgentRadio](https://arxiv.org/abs/2607.28430) — async message-passing for multi-agent coding
- [BabelTele](https://arxiv.org/abs/2606.19857) — model-centric textual representations

## Comparison to BabelTele

| | PM-1 + Adapter | BabelTele |
|---|---|---|
| Input | Fixed-schema state vector | Arbitrary natural language |
| Compression | Deterministic math (`. -`) | LLM-generated compact text |
| Recovery | Bit-exact (schema lookup) | Semantic (~99.5% fidelity) |
| Adapter role | Expands decoded bytes → text | Recovery IS the LLM call |

BabelTele compresses arbitrary text with LLM recovery. PM-1 compresses structured state with deterministic recovery, and the adapter expands it for LLM consumption. Different inputs, different guarantees, complementary approaches.

## Schemas

Adapters are domain-specific. The default schema maps 8-byte agent state vectors. Custom schemas follow the same format:

```json
{
  "name": "my_domain",
  "state_width": 8,
  "fields": [
    {"byte": 0, "name": "agent", "values": {"0": "planner", "1": "coder"}},
    ...
  ]
}
```

**Bit-level flags.** The adapter maps byte values to labels — it does not decompose individual bits. If your state model uses bit-level flags (e.g., byte 7 bit 1 = completeness, byte 7 bit 2 = needs_review), the domain schema author must either: (a) map the full byte to labels, or (b) split flags into separate boolean fields in the schema. The adapter has no opinion about bit-level encoding — that's a domain concern.

Create domain-specific packages (`pm-adapter-hospital`, `pm-adapter-game`, etc.) with their own schemas.

## Testing

```
pip install -e ".[test]" && pytest
```

## License

- Code: Apache-2.0
- Schemas: CC-BY-4.0
