"""Test adapter against real PM-1 frames from self-harness traces."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pm_adapter import Adapter, load_schema

schema = load_schema("default")
adapter = Adapter(schema)

# Load a real PM-1 frame from production traces
traces_dir = Path.home() / "self-harness" / "traces"
pm1_files = sorted(traces_dir.glob("*.pm1"))[:5]

for f in pm1_files:
    trace = json.loads(f.read_text(encoding="utf-8", errors="replace"))
    frame = trace.get("pm1", "")
    if not frame:
        continue
    print(f"\n--- {f.stem[:40]} ---")
    print(f"  English: {adapter.to_english(frame)}")
    print(f"  JSON: {adapter.to_json(frame)}")
    print(f"  RAG: {adapter.to_rag(frame)}")
    events = adapter.to_events(frame)
    if events:
        print(f"  Events: {events[:2]}")
