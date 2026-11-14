"""Legacy codec: the original PM-Adapter's Morse frame decoder, carried over unchanged in behaviour.

Frames are the PM-1 Morse bit-strings of the retired frame layer. Decoding them needs the ``pro-memoria`` package
(https://github.com/rikirinjani/pro-memoria). That import is OPTIONAL here: nothing else in PM-Adapter v2 needs it, and without it
only the frame functions raise ``LegacyUnavailable`` (with an install hint) while everything else works.

The original's four output modes keep their original output shapes on frames, including the event name ``value_change`` (the v2 event
log calls the same event ``state_change``; see README). ``Adapter`` in this module is the original class under its original name.
"""
from __future__ import annotations

import logging

from .errors import LegacyUnavailable

_HINT = ("the Morse frame decoder needs the optional package 'pro-memoria' "
         "(pip install git+https://github.com/rikirinjani/pro-memoria.git); PM-Adapter v2 itself does not need it")


def _pro_memoria():
    try:
        import pro_memoria
    except ImportError as exc:
        raise LegacyUnavailable(_HINT) from exc
    return pro_memoria


def _import_failsafe():
    """Import FailsafePM1 from pro-memoria (used only when use_ecc=True)."""
    _pro_memoria()
    try:
        from pro_memoria.opencode_plugin.failsafe import FailsafePM1
    except ImportError as exc:
        raise LegacyUnavailable(_HINT) from exc
    return FailsafePM1


# --- the original module-level functions (rag.py and events.py), same signatures and outputs -------------------------------------
def to_rag(decoded: dict, schema: dict) -> str:
    """Dense natural-language description of a decoded frame. Kept as ``to_semantic``; NOT recommended for retrieval (README R15)."""
    parts = []
    for field in schema["fields"]:
        name = field["name"].replace("_", " ")
        parts.append("%s set to %s" % (name, decoded[field["name"]]["label"]))
    return "Agent state: " + ", ".join(parts)


def to_events(current: dict, previous, schema: dict) -> list:
    """State transitions of decoded frames: state_init events without a previous frame, value_change events with one (original names)."""
    events = []
    if previous is None:
        for field in schema["fields"]:
            name = field["name"]
            events.append({"event": "state_init", "field": name, "value": current[name]["label"]})
        return events
    for field in schema["fields"]:
        name = field["name"]
        old_label, new_label = previous[name]["label"], current[name]["label"]
        if old_label != new_label:
            events.append({"event": "value_change", "field": name, "old": old_label, "new": new_label})
    return events


def to_english(decoded: dict, schema: dict) -> str:
    return ", ".join("%s: %s" % (f["name"], decoded[f["name"]]["label"]) for f in schema["fields"])


def to_json(decoded: dict, schema: dict) -> dict:
    return {f["name"]: decoded[f["name"]]["label"] for f in schema["fields"]}


class LegacyDecoder:
    """The original ``Adapter``: decodes PM-1 Morse frames through a byte schema into the four output modes."""

    def __init__(self, schema: dict, use_ecc: bool = False):
        if schema is None:
            raise TypeError("schema is required")
        self.schema = schema            # {"state_width": 8, "fields": [...]}
        self.use_ecc = use_ecc
        self._failsafe = None

    def _get_failsafe(self):
        if self._failsafe is None:
            self._failsafe = _import_failsafe()(session_id="pm-adapter")
        return self._failsafe

    def _decode_bytes(self, frame: str) -> bytes:
        if not self.use_ecc:
            return _pro_memoria().decode_bytes(frame)
        failsafe = self._get_failsafe()
        corrected_before = failsafe.total_corrected
        data = failsafe.decode(frame)
        n_corrected = failsafe.total_corrected - corrected_before
        if data is None:
            if n_corrected > 0:
                raise ValueError("PM-1 ECC: %d bit(s) corrected but frame still unrecoverable (session=%s)" % (n_corrected, failsafe.session_id))
            detail = "unrecoverable corruption (double-bit error)"
            if failsafe.last_error is not None:
                err_type = failsafe.last_error.get("error_type", "unknown")
                reason = failsafe.last_error.get("details", {}).get("reason", "n/a")
                detail = "%s: %s" % (err_type, reason)
            raise ValueError("PM-1 ECC decode failed - %s (session=%s)" % (detail, failsafe.session_id))
        if n_corrected > 0:
            logging.warning("PM-1 ECC corrected %d bit error(s) in frame (session=%s)", n_corrected, failsafe.session_id)
        return data

    def decode(self, frame: str) -> dict:
        """Frame to {field: {"raw": byte, "label": text}}."""
        data = self._decode_bytes(frame)
        result = {}
        for field in self.schema["fields"]:
            raw = data[field["byte"]]
            result[field["name"]] = {"raw": raw, "label": field.get("values", {}).get(str(raw), str(raw))}
        return result

    def to_english(self, frame: str) -> str:
        return to_english(self.decode(frame), self.schema)

    def to_json(self, frame: str) -> dict:
        return to_json(self.decode(frame), self.schema)

    def to_semantic(self, frame: str) -> str:
        return to_rag(self.decode(frame), self.schema)

    def to_events(self, frame: str, previous_frame=None) -> list:
        return to_events(self.decode(frame), self.decode(previous_frame) if previous_frame else None, self.schema)


Adapter = LegacyDecoder          # the original class name, for code that imports it from the legacy module
