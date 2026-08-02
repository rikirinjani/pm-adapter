"""Core PM-1 adapter — schema-driven frame expansion.

Consumes Pro Memoria (PM-1) frames: decodes the raw Morse bit-string to
bytes, then expands each byte through a user-supplied schema into four
output modes (english, json, rag, events). Pure schema lookup, no LLM.
"""

import json
from pathlib import Path

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


def load_schema(name: str) -> dict:
    """Load a bundled JSON schema by name (e.g. "default")."""
    path = _SCHEMA_DIR / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"schema not found: {name!r} ({path})")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


class Adapter:
    def __init__(self, schema: dict):
        if schema is None:
            raise TypeError("schema is required")
        self.schema = schema  # {"state_width": 8, "fields": [...]}

    def decode(self, frame: str) -> dict:
        """Decode PM-1 frame to raw byte dict using pro_memoria."""
        from pro_memoria import morse_to_bits, decode_bytes
        data = decode_bytes(frame)
        result = {}
        for field in self.schema["fields"]:
            byte_idx = field["byte"]
            raw = data[byte_idx]
            field_name = field["name"]
            values = field.get("values", {})
            result[field_name] = {
                "raw": raw,
                "label": values.get(str(raw), str(raw))
            }
        return result

    def to_english(self, frame: str) -> str:
        """Expand PM-1 frame to human-readable English sentence."""
        d = self.decode(frame)
        parts = []
        for field in self.schema["fields"]:
            name = field["name"]
            label = d[name]["label"]
            parts.append(f"{name}: {label}")
        return ", ".join(parts)

    def to_json(self, frame: str) -> dict:
        """Expand to structured JSON dict."""
        d = self.decode(frame)
        return {field["name"]: d[field["name"]]["label"] for field in self.schema["fields"]}

    def to_rag(self, frame: str) -> str:
        """Generate RAG-optimized description."""
        return to_rag(self.decode(frame), self.schema)

    def to_events(self, frame: str, previous_frame: str | None = None) -> list[dict]:
        """Detect state transitions and emit multi-agent event bus events."""
        return to_events(self.decode(frame),
                        self.decode(previous_frame) if previous_frame else None,
                        self.schema)


from .rag import to_rag  # noqa: E402  (local import after class for readability)
from .events import to_events  # noqa: E402
