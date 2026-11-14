"""Append-only event log (R4, R5). Current state is a fold over this log; nothing else is the source of truth.

Event types: state_init, state_change (both as in the original ``to_events``, see README for the original's name "value_change"),
notice, rule_confirmed, rule_superseded, plus rule_stored (an unverified entry has to be written somewhere; R4 lists no type for it).
"""
from __future__ import annotations

import copy

from .errors import PMAdapterError
from .schema import PROVENANCE

EVENT_TYPES = ("state_init", "state_change", "notice", "rule_stored", "rule_confirmed", "rule_superseded")


class EventLog:
    def __init__(self):
        self._events = []                  # private: the only operations are append and read-copy

    def append(self, type_, payload, *, source, provenance, evidence=None, note=None):
        if type_ not in EVENT_TYPES:
            raise PMAdapterError("unknown event type %r" % (type_,))
        if provenance not in PROVENANCE:
            raise PMAdapterError("unknown provenance %r" % (provenance,))
        if not isinstance(source, str) or not source:
            raise PMAdapterError("every event needs a source")
        ev = {"seq": len(self._events) + 1, "type": type_, "source": source, "provenance": provenance,
              "evidence": evidence, "payload": copy.deepcopy(payload)}
        if note is not None:
            ev["note"] = str(note)                  # descriptive text about the evidence: log only, never read into a packet (R3a)
        self._events.append(ev)
        return copy.deepcopy(ev)

    def events(self, since=0, until=None):
        """Copies of the events with since < seq <= until (seq counts from 1)."""
        return [copy.deepcopy(e) for e in self._events if e["seq"] > since and (until is None or e["seq"] <= until)]

    def raw(self):
        """A tuple of the stored event dicts for internal folds. Read-only by convention: callers outside the package use ``events()``."""
        return tuple(self._events)

    def __len__(self):
        return len(self._events)
