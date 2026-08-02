"""Basic adapter usage example."""
from pm_adapter import Adapter, load_schema
from pathlib import Path

schema = load_schema("default")
adapter = Adapter(schema)

# Example PM-1 frame: fixer, act phase, high confidence, pass
# Built with pro_memoria.encode_bytes(bytes([1, 2, 3, 0, 0, 0, 1, 0]))
# (byte 0=fixer=1, byte 1=act=2, byte 2=high=3, byte 6=pass=1)
frame = ".......-......-.......--...............................-........"

print("to_english:", adapter.to_english(frame))
print("to_json:", adapter.to_json(frame))
print("to_rag:", adapter.to_rag(frame))
print("to_events:", adapter.to_events(frame))
