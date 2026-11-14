"""Core PM-1 adapter — schema-driven frame expansion.

Consumes Pro Memoria (PM-1) frames: decodes the raw Morse bit-string to
bytes, then expands each byte through a user-supplied schema into four
output modes (english, json, semantic, events). Pure schema lookup, no LLM.

ECC note: when use_ecc=False (default), the adapter assumes upstream has
already validated the frame via FailsafePM1. When use_ecc=True, decode()
performs Hamming [8,4,4] error detection and correction.
"""

import json
import logging
from pathlib import Path

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


def load_schema(name: str) -> dict:
    """Load a bundled JSON schema by name (e.g. "default")."""
    path = _SCHEMA_DIR / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"schema not found: {name!r} ({path})")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _import_failsafe():
    """Import FailsafePM1 from the pro-memoria package.

    Modules live inside ``pro_memoria/opencode_plugin/`` — no sys.path
    manipulation or sibling-directory assumptions needed.
    """
    from pro_memoria.opencode_plugin.failsafe import FailsafePM1
    return FailsafePM1


class Adapter:
    def __init__(self, schema: dict, use_ecc: bool = False):
        if schema is None:
            raise TypeError("schema is required")
        self.schema = schema  # {"state_width": 8, "fields": [...]}
        self.use_ecc = use_ecc
        self._failsafe = None

    def _get_failsafe(self):
        """Lazily build a FailsafePM1 instance for ECC decode."""
        if self._failsafe is None:
            self._failsafe = _import_failsafe()(session_id="pm-adapter")
        return self._failsafe

    def _decode_bytes(self, frame: str) -> bytes:
        """Return raw state bytes for a frame, honoring use_ecc.

        With use_ecc=True, Hamming [8,4,4] correction is transparent:
        single-bit errors are corrected and the clean bytes returned (a
        warning is logged). Only genuinely unrecoverable corruption raises.
        """
        if not self.use_ecc:
            from pro_memoria import morse_to_bits, decode_bytes
            return decode_bytes(frame)

        failsafe = self._get_failsafe()
        corrected_before = failsafe.total_corrected
        data = failsafe.decode(frame)
        n_corrected = failsafe.total_corrected - corrected_before

        if data is None:
            # Unrecoverable corruption (double-bit error) or invalid
            # encoding. n_corrected is 0 on this path, but guard the
            # corrected + uncorrectable combination defensively.
            if n_corrected > 0:
                raise ValueError(
                    f"PM-1 ECC: {n_corrected} bit(s) corrected but frame still "
                    f"unrecoverable (session={failsafe.session_id})"
                )
            detail = "unrecoverable corruption (double-bit error)"
            if failsafe.last_error is not None:
                err_type = failsafe.last_error.get("error_type", "unknown")
                reason = failsafe.last_error.get("details", {}).get("reason", "n/a")
                detail = f"{err_type}: {reason}"
            raise ValueError(
                f"PM-1 ECC decode failed — {detail} (session={failsafe.session_id})"
            )

        if n_corrected > 0:
            # Single-bit error corrected transparently — return clean data.
            logging.warning(
                "PM-1 ECC corrected %d bit error(s) in frame (session=%s)",
                n_corrected, failsafe.session_id,
            )
        return data

    def decode(self, frame: str) -> dict:
        """Decode PM-1 frame to raw byte dict using pro_memoria.

        When use_ecc=False, the adapter assumes upstream has already validated
        the frame via FailsafePM1. When use_ecc=True, decode() performs Hamming
        [8,4,4] error detection and correction.
        """
        data = self._decode_bytes(frame)
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

    def to_semantic(self, frame: str) -> str:
        """Generate semantic description for retrieval (RAG implementation)."""
        return _to_rag(self.decode(frame), self.schema)

    def to_events(self, frame: str, previous_frame: str | None = None) -> list[dict]:
        """Detect state transitions and emit multi-agent event bus events."""
        return to_events(self.decode(frame),
                        self.decode(previous_frame) if previous_frame else None,
                        self.schema)


from .rag import to_rag as _to_rag  # noqa: E402  (internal implementation detail)
from .events import to_events  # noqa: E402
