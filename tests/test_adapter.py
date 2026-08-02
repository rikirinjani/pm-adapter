"""PM-Adapter test suite — schema loading, mode expansion, real trace decoding, edge cases."""
import json, sys, pytest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pm_adapter import Adapter, load_schema

# Setup — run once per module
schema = load_schema("default")
adapter = Adapter(schema)

# Sample PM-1 frame: fixer (1), act (2), high conf (3), pass (1)
# bytes: [1, 2, 3, 0, 0, 0, 1, 0]
from pro_memoria import encode_bytes
VALID_FRAME = encode_bytes(bytes([1, 2, 3, 0, 0, 0, 1, 0]))
VALID_FRAME2 = encode_bytes(bytes([1, 2, 2, 0, 0, 0, 1, 0]))  # conf dropped to medium
INVALID_WIDTH = ".-"  # too short
NOT_MULTIPLE = ".-....."  # 7 chars, not multiple of 8

def test_schema_loads():
    """Schema loads with expected fields."""
    assert schema["state_width"] == 8
    assert len(schema["fields"]) == 8
    assert schema["fields"][0]["name"] == "agent_type"

def test_to_english():
    result = adapter.to_english(VALID_FRAME)
    assert "fixer" in result
    assert "act" in result
    assert "high" in result
    assert "pass" in result

def test_to_json():
    result = adapter.to_json(VALID_FRAME)
    assert result["agent_type"] == "fixer"
    assert result["phase"] == "act"
    assert result["confidence"] == "high"
    assert result["outcome"] == "pass"

def test_to_rag():
    result = adapter.to_rag(VALID_FRAME)
    assert "Agent state" in result
    assert "agent type" in result
    assert "fixer" in result

def test_to_events_initial():
    """Initial state (no previous) emits state_init events."""
    events = adapter.to_events(VALID_FRAME)
    assert len(events) == 8
    assert all(e["event"] == "state_init" for e in events)

def test_to_events_transition():
    """State change emits value_change events."""
    events = adapter.to_events(VALID_FRAME2, previous_frame=VALID_FRAME)
    # Only confidence changed from 3→2 (high→medium)
    changes = [e for e in events if e["event"] == "value_change"]
    assert len(changes) == 1
    assert changes[0]["field"] == "confidence"
    assert changes[0]["old"] == "high"
    assert changes[0]["new"] == "medium"

def test_unknown_byte_labels():
    """Bytes without value mappings fall back to string representation."""
    raw_frame = encode_bytes(bytes([99, 0, 0, 0, 0, 0, 0, 0]))
    result = adapter.to_json(raw_frame)
    # Byte 99 has no mapping in default schema
    assert str(99) in result["agent_type"]  # falls back to "99"

def test_decode_raw_bytes():
    """decode() returns raw byte values alongside labels."""
    result = adapter.decode(VALID_FRAME)
    assert result["agent_type"]["raw"] == 1
    assert result["agent_type"]["label"] == "fixer"

def test_real_trace_decoding():
    """Real .pm1 files from production should decode without errors."""
    traces_dir = Path.home() / "self-harness" / "traces"
    pm1_files = sorted(traces_dir.glob("*.pm1"))[:10]
    decoded = 0
    for f in pm1_files:
        trace = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        frame = trace.get("pm1", "")
        if not frame or len(frame) % 8 != 0:
            continue
        try:
            result = adapter.decode(frame)
            decoded += 1
        except Exception as e:
            pytest.fail(f"Failed to decode {f.stem}: {e}")
    assert decoded >= 1

def test_all_four_modes_no_crash():
    """All four modes produce output without errors for every valid real trace."""
    traces_dir = Path.home() / "self-harness" / "traces"
    for f in sorted(traces_dir.glob("*.pm1"))[:5]:
        trace = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        frame = trace.get("pm1", "")
        if not frame or len(frame) % 8 != 0:
            continue
        eng = adapter.to_english(frame)
        js = adapter.to_json(frame)
        rag = adapter.to_rag(frame)
        evt = adapter.to_events(frame)
        assert isinstance(eng, str) and len(eng) > 0
        assert isinstance(js, dict)
        assert isinstance(rag, str) and len(rag) > 0
        assert isinstance(evt, list)

def test_custom_schema_loading():
    """load_schema returns a dict for a valid schema name."""
    s = load_schema("default")
    assert isinstance(s, dict)
    assert "fields" in s
    assert "state_width" in s

def test_adapter_raises_on_no_schema():
    """Adapter instantiated without schema should be usable with explicit schema passed to decode."""
    # Adapter requires schema at init
    try:
        Adapter(None)
        pytest.fail("Should have raised")
    except (TypeError, AttributeError, KeyError):
        pass  # expected — schema is required
