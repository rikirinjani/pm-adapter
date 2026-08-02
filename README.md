# PM-Adapter

**Schema-driven PM-1 frame expansion.** Takes Pro Memoria (PM-1) Morse frames and expands them into human-readable English, structured JSON, RAG-optimized descriptions, and Hermes-compatible state change events.

Zero LLM calls. Pure deterministic schema lookup. No part of PM-1 — consumes it.

## Install

```
pip install pm-adapter
```

Requires `pro-memoria`.

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

# RAG-optimized description
print(adapter.to_rag(frame))
# "Agent state: agent type set to fixer, phase set to act..."

# Hermes event bus events
print(adapter.to_events(frame))
# [{"event": "state_init", "field": "agent_type", "value": "fixer"}, ...]
```

## Modes

| Mode | Output | Use |
|------|--------|-----|
| `to_english()` | Human-readable sentence | Debugging, human-facing dashboards |
| `to_json()` | Structured dict | Programmatic consumption |
| `to_rag()` | Dense searchable description | Vector embedding for semantic memory |
| `to_events()` | State change event list | Hermes event bus, deterministic scheduling |

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

Create domain-specific packages (`pm-adapter-hospital`, `pm-adapter-game`, etc.) with their own schemas.

## Architecture

PM-1 is the substrate (compact, deterministic transport). The adapter is the interpreter (schema-driven expansion). They are separate projects by design.

```
PM-1 Frame (128 chars)
     │
     ▼
Adapter.decode(frame, schema)
     │
     ├── to_english()
     ├── to_json()
     ├── to_rag()
     └── to_events()
```

## License

- Code: Apache-2.0
- Schemas: CC-BY-4.0
