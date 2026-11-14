"""Derived state (R6, R7): a registry of pure functions that compute decision fields from the event log.

The packet holds the derived value, never the raw input it came from. Each function takes ``(events, ctx)`` and returns
``(value, seqs)``: the value and the sequence numbers of the events that produced it. ``events`` are copies and ``ctx`` is a plain dict
(for example ``{"hop": 55}``); a function must not read anything else, so the same log always gives the same value.

Shipped example: ``freeze_active``, "a freeze window is active at this hop", computed from ``notice`` events of kind "freeze"
({"kind": "freeze", "from_hop": 50, "to_hop": 60}). In the author's runs a raw schedule pinned in the packet led to violations; the derived field did not.
"""
from __future__ import annotations

import copy

from .errors import PMAdapterError, ValidationError


def freeze_active(events, ctx):
    hop = ctx.get("hop")
    if not isinstance(hop, int) or isinstance(hop, bool):
        raise ValidationError("hop", "required (an int) to derive freeze_active")
    active, used = False, []
    for e in events:
        p = e["payload"]
        if e["type"] == "notice" and p.get("kind") == "freeze":
            used.append(e["seq"])
            if p["from_hop"] <= hop <= p["to_hop"]:
                active = True
    return active, used


class DerivedRegistry:
    def __init__(self, functions=None):
        self._fns = {}
        for name, fn in (functions or {"freeze_active": freeze_active}).items():
            self.register(name, fn)

    def register(self, name, fn):
        if not callable(fn):
            raise PMAdapterError("derived function %r is not callable" % (name,))
        self._fns[name] = fn

    def names(self):
        return sorted(self._fns)

    def __contains__(self, name):
        return name in self._fns

    def compute(self, name, events, ctx):
        """Run one function and return the packet entry: value, provenance 'derived', the function's name and the events it used (R7)."""
        value, seqs = self._fns[name](copy.deepcopy(events), dict(ctx))
        return {"value": value, "provenance": "derived", "function": name, "events": list(seqs)}
