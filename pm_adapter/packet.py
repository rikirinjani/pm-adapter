"""Packet assembly (R21 to R23).

The outgoing packet holds: a reference to the invariant task specification, the decision fields (derived values, validated values), and
delivered store entries marked "applies to <target>" with their provenance. It has a size budget: if the content does not fit, assembly
raises BudgetError naming what did not fit; nothing is ever truncated silently (R22). There is no way to attach a transcript (R23): the
worker receives the packet and the task specification only.
"""
from __future__ import annotations

import json

from .errors import BudgetError

DEFAULT_BUDGET_CHARS = 20000


class Packet:
    """The assembled packet. ``to_dict``, ``to_json`` and ``to_english`` are the output modes for a packet."""

    def __init__(self, target, hop, task_spec, decision, store, missing):
        self.target, self.hop = target, hop
        self.task_spec, self.decision, self.store, self.missing = task_spec, decision, store, missing

    def to_dict(self):
        return {"target": self.target, "hop": self.hop, "task_spec": self.task_spec, "decision": self.decision,
                "store": self.store, "missing": self.missing}

    def to_json(self, **kw):
        return json.dumps(self.to_dict(), sort_keys=True, **kw)

    def size(self):
        return len(self.to_json())

    def to_english(self):
        out = []
        if self.task_spec:
            out.append("Task specification: %s." % self.task_spec)
        out.append("Target: %s%s." % (self.target, "" if self.hop is None else " at hop %s" % self.hop))
        for name, d in self.decision.items():
            how = {"derived": "derived by %s from events %s" % (d.get("function"), d.get("events")),
                   "measured": "measured (evidence %s)" % d.get("evidence"),
                   "claimed": "claimed (no measurement behind it)"}[d["provenance"]]
            out.append("%s is %s, %s." % (name, d["value"], how))
        for s in self.store:
            out.append("Rule %s applies to %s: %s %s." % (s["key"], s["applies_to"], s["text"], s["tag"]))
        if self.missing:
            out.append("Not set yet: %s." % ", ".join(self.missing))
        return " ".join(out)


def entry_tag(entry):
    """The provenance tag shown next to a delivered line. Confirmed lines name their evidence; unverified lines say so."""
    if entry.status == "confirmed":
        return "[confirmed by %s at seq %d]" % (entry.evidence, entry.confirmed_seq)
    return "[unverified note, seq %d]" % entry.seq


def delivered_item(entry, target):
    return {"key": entry.key, "text": entry.text, "status": entry.status, "provenance": entry.provenance, "evidence": entry.evidence,
            "seq": entry.seq, "applies_to": target, "tag": entry_tag(entry)}


def _size(part):
    return len(json.dumps(part, sort_keys=True))


def check_budget(packet: Packet, parts, budget_chars):
    """Raise BudgetError if the packet is larger than the budget. ``parts`` is [(name, json-able content)] in priority order; the error names
    every part that does not fit when parts are accepted in that order."""
    size = packet.size()
    if size <= budget_chars:
        return
    used, overflow = 0, []
    for name, content in parts:
        s = _size(content)
        if used + s <= budget_chars:
            used += s
        else:
            overflow.append({"part": name, "size": s})
    if not overflow:                                    # only the JSON framing pushes it over: name the last part
        name, content = parts[-1]
        overflow.append({"part": name, "size": _size(content)})
    raise BudgetError(budget_chars, size, overflow)
