"""PM-1 encoder — compress structured records to morse frames.

Takes structured data (tool calls, handoffs, errors, cost logs) and encodes
them into PM-1 morse frames using schema-driven byte packing. The inverse
of adapter.py's decode path.

Usage:
    from pm_adapter.encoder import encode_tool_call, encode_handoff

    frame = encode_tool_call(
        agent="fixer", tool="write", outcome="ok",
        duration_bucket="1-5s", size_bucket="10-100KB",
        tokens_in=120, tokens_out=45, flags="none"
    )
    # frame is a morse string: "··· −−− ·−· ..."
"""

import json
from pathlib import Path
from typing import Any

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

# Reverse lookup: label -> byte value for each schema field
_schema_cache: dict[str, dict] = {}


def _load_schema(name: str) -> dict:
    if name not in _schema_cache:
        path = _SCHEMA_DIR / f"{name}.json"
        with path.open(encoding="utf-8") as fh:
            _schema_cache[name] = json.load(fh)
    return _schema_cache[name]


def _resolve_byte(field: dict, value: Any) -> int:
    """Map a label or raw value to its byte encoding."""
    values = field.get("values", {})
    if values:
        # Reverse lookup: label -> byte
        reverse = {v: int(k) for k, v in values.items()}
        if isinstance(value, str) and value in reverse:
            return reverse[value]
        # Try as raw int
        try:
            raw = int(value)
            if 0 <= raw <= 255:
                return raw
        except (ValueError, TypeError):
            pass
        raise ValueError(f"Cannot encode {value!r} for field {field['name']}")
    else:
        # Raw byte field — clamp to 0-255
        raw = int(value)
        return max(0, min(255, raw))


def _pack_frame(schema: dict, values: dict) -> bytes:
    """Pack field values into a fixed-width byte frame."""
    width = schema.get("state_width", 8)
    frame = bytearray(width)
    for field in schema["fields"]:
        byte_idx = field["byte"]
        if byte_idx >= width:
            continue
        name = field["name"]
        if name in values:
            frame[byte_idx] = _resolve_byte(field, values[name])
    return bytes(frame)


def _bytes_to_morse(data: bytes) -> str:
    """Convert bytes to PM-1 morse encoding.

    Uses pro_memoria's encode_bytes if available, falls back to simple hex.
    """
    try:
        from pro_memoria import encode_bytes
        return encode_bytes(data)
    except ImportError:
        # Fallback: hex encoding (not true morse, but functional)
        return data.hex()


def encode_record(schema_name: str, values: dict) -> str:
    """Encode a structured record to a PM-1 morse frame.

    Args:
        schema_name: Name of schema file (without .json)
        values: Dict of field_name -> value

    Returns:
        Morse-encoded frame string
    """
    schema = _load_schema(schema_name)
    frame_bytes = _pack_frame(schema, values)
    return _bytes_to_morse(frame_bytes)


# Convenience functions for each schema type

AGENT_MAP = {
    "orchestrator": 0, "fixer": 1, "oracle": 2,
    "explorer": 3, "librarian": 4, "designer": 5, "council": 6,
}


def encode_tool_call(
    agent: str, tool: str, outcome: str,
    duration_bucket: str = "<1s", size_bucket: str = "<1KB",
    tokens_in: int = 0, tokens_out: int = 0, flags: str = "none"
) -> str:
    """Encode a tool call result to PM-1 frame."""
    return encode_record("tool_call", {
        "agent": agent, "tool": tool, "outcome": outcome,
        "duration_bucket": duration_bucket, "size_bucket": size_bucket,
        "tokens_in": tokens_in, "tokens_out": tokens_out, "flags": flags,
    })


def encode_handoff(
    from_agent: str, to_agent: str, reason: str,
    confidence: str = "medium", files_touched: int = 0,
    tokens_used: int = 0, duration_bucket: str = "30s-2m",
    flags: str = "none"
) -> str:
    """Encode a handoff message to PM-1 frame."""
    return encode_record("handoff", {
        "from_agent": from_agent, "to_agent": to_agent, "reason": reason,
        "confidence": confidence, "files_touched": files_touched,
        "tokens_used": tokens_used, "duration_bucket": duration_bucket,
        "flags": flags,
    })


def encode_error(
    agent: str, error_type: str, severity: str = "error",
    retry_count: int = 0, http_code: int = 0,
    duration_bucket: str = "<1s", recovery: str = "none",
    flags: str = "none"
) -> str:
    """Encode an error pattern to PM-1 frame."""
    return encode_record("error_pattern", {
        "agent": agent, "error_type": error_type, "severity": severity,
        "retry_count": retry_count, "http_code": http_code,
        "duration_bucket": duration_bucket, "recovery": recovery,
        "flags": flags,
    })


def encode_cost(
    agent: str, model_tier: str, input_bucket: str, output_bucket: str,
    cost_bucket: str, duration_bucket: str = "<5s",
    task_count: int = 1, flags: str = "none"
) -> str:
    """Encode a cost log entry to PM-1 frame."""
    return encode_record("cost_log", {
        "agent": agent, "model_tier": model_tier,
        "input_bucket": input_bucket, "output_bucket": output_bucket,
        "cost_bucket": cost_bucket, "duration_bucket": duration_bucket,
        "task_count": task_count, "flags": flags,
    })


# Token bucket helpers

def bucket_tokens(n: int) -> str:
    """Map token count to bucket label."""
    if n < 1000: return "<1K"
    if n < 5000: return "1-5K"
    if n < 20000: return "5-20K"
    if n < 100000: return "20-100K"
    return ">100K"


def bucket_duration(seconds: float) -> str:
    """Map duration to bucket label."""
    if seconds < 1: return "<1s"
    if seconds < 5: return "1-5s"
    if seconds < 30: return "5-30s"
    if seconds < 120: return "30-120s"
    return ">120s"


def bucket_cost(cents: float) -> str:
    """Map cost in cents to bucket label."""
    if cents < 1: return "lt_1c"
    if cents < 5: return "1c_5c"
    if cents < 20: return "5c_20c"
    if cents < 100: return "20c_1d"
    return "gt_1d"


def bucket_size(bytes_count: int) -> str:
    """Map file size to bucket label."""
    if bytes_count < 1024: return "<1KB"
    if bytes_count < 10240: return "1-10KB"
    if bytes_count < 102400: return "10-100KB"
    if bytes_count < 1048576: return "100KB-1MB"
    return ">1MB"
