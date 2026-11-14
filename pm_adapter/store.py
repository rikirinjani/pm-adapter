"""Durable store, the vault (R8 to R11). Entries are a projection of the event log (R5): only the adapter appends the events that
create them, and this module only reads events.

Entry life: ``rule_stored`` creates an unverified entry; ``rule_confirmed`` (an explicit adapter call that names the evidence) makes it
confirmed; when a confirmed entry is written under a key that already has a current confirmed entry, a ``rule_superseded`` event marks the
older one superseded. A superseded entry stays in the log for audit and is never returned by retrieval again.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

CONFIRMED, UNVERIFIED, SUPERSEDED = "confirmed", "unverified", "superseded"


@dataclass(frozen=True)
class Entry:
    id: str
    key: str
    text: str
    status: str                     # confirmed | unverified | superseded
    provenance: str                 # measured | claimed (derived is for computed fields only)
    seq: int                        # write sequence number: the log position of the event that created the entry
    confirmed_seq: Optional[int]    # log position of the confirming event
    supersedes: Optional[str]       # id of the entry this one replaced
    superseded_by: Optional[str]    # id of the entry that replaced this one
    evidence: Optional[str]
    source: str

    def to_dict(self):
        return {f: getattr(self, f) for f in self.__dataclass_fields__}


class EntryFold:
    """Incremental projection of the log into entries: ``apply`` each event once, in order. ``fold_entries`` is the same thing in one call."""

    def __init__(self):
        self._entries = {}
        self.upto = 0                     # number of log events applied so far

    def apply(self, e):
        t, p = e["type"], e["payload"]
        if t == "rule_stored":
            self._entries[p["id"]] = Entry(p["id"], p["key"], p["text"], UNVERIFIED, e["provenance"], e["seq"], None, None, None, None, e["source"])
        elif t == "rule_confirmed":
            old = self._entries[p["id"]]
            self._entries[p["id"]] = Entry(old.id, old.key, old.text, CONFIRMED, e["provenance"], old.seq, e["seq"], p.get("supersedes"),
                                           old.superseded_by, p["evidence"], old.source)
        elif t == "rule_superseded":
            old = self._entries[p["id"]]
            self._entries[p["id"]] = Entry(old.id, old.key, old.text, SUPERSEDED, old.provenance, old.seq, old.confirmed_seq, old.supersedes,
                                           p["superseded_by"], old.evidence, old.source)

    def entries(self):
        return sorted(self._entries.values(), key=lambda x: x.seq)


def fold_entries(events):
    """Fold the log into the list of entries (all statuses, ordered by write sequence number)."""
    f = EntryFold()
    for e in events:
        f.apply(e)
    return f.entries()


def current(entries, key=None, *, include_unverified=False):
    """Entries retrieval may return: current confirmed ones, plus unverified ones only when asked for (R10). Never superseded ones (R9)."""
    keep = {CONFIRMED} | ({UNVERIFIED} if include_unverified else set())
    return [e for e in entries if e.status in keep and (key is None or e.key == key)]
