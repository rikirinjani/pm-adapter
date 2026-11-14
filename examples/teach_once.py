"""Teach-once walk-through (SPEC section 5). Standard library only, no model call, no network.

    python examples/teach_once.py          (from the repository root; its printed output is saved in teach_once_output.txt)

Steps: an event, a confirmation, a store write, a later retrieval by key, packet assembly; then a superseding rule, showing the old
rule is no longer returned (and stays in the log).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pm_adapter import Adapter, load_schema  # noqa: E402


def show(title, value):
    print("\n== %s" % title)
    print(value if isinstance(value, str) else json.dumps(value, indent=(2 if isinstance(value, dict) else None), sort_keys=True))


adapter = Adapter(load_schema("vault_demo"), measurement_sources={"test-runner"})

# 1. Events: the worker's state and a freeze notice from the scheduler (the freeze window is derived later, never pinned raw).
adapter.init_state({"phase": "act", "outcome": "running"}, source="agent")
adapter.notice("freeze", source="scheduler", from_hop=50, to_hop=60)
show("1. event log so far", [(e["seq"], e["type"], e["source"], e["provenance"]) for e in adapter.events()])

# 2. Teach once: the repository note says rule 'mod' returns x % 13. The agent's note is stored UNVERIFIED (not returned yet).
note = adapter.store_rule("mod", "return x % 13", source="agent")
show("2. stored, not yet retrievable", {"entry": note.id, "status": note.status,
                                       "retrievable_by_key": [e.text for e in adapter.retrieve("mod").entries]})

# 3. Confirmation: only an explicit adapter call that names its evidence (a passing test) makes it confirmed. The test runner is a
#    configured measurement source, so the label becomes 'measured'.
confirmed = adapter.confirm(note.id, evidence="test.mod_01.pass", source="test-runner", note="lib/mod_01.py passed")
show("3. confirmed", {"entry": confirmed.id, "status": confirmed.status, "provenance": confirmed.provenance, "evidence": confirmed.evidence,
                      "write_seq": confirmed.seq, "confirmed_seq": confirmed.confirmed_seq})

# 4. Much later, a new ticket arrives that carries the key. Retrieval by exact key returns the one current confirmed entry.
found = adapter.retrieve("mod")
show("4. retrieval by key", [(e.key, e.text, e.status) for e in found.entries])

# 5. Packet assembly for the next worker: task-spec reference, derived decision fields, the delivered rule marked 'applies to <target>'.
packet = adapter.assemble("lib/mod_02.py", hop=55, delivered=found.entries)
show("5a. packet (json)", packet.to_dict())
show("5b. packet (english)", adapter.to_english(packet))
print("\nfreeze_active at hop 70 (outside the window):", adapter.assemble("lib/mod_02.py", hop=70).decision["freeze_active"]["value"])

# 6. A superseding rule: the repository changed the convention. Confirming the new entry marks the old one superseded.
adapter.store_confirmed("mod", "return x % 7", evidence="test.mod_03.pass", source="test-runner", note="lib/mod_03.py passed")
after = adapter.retrieve("mod")
show("6a. retrieval by key after the new rule", [(e.key, e.text, e.status) for e in after.entries])
show("6b. audit view: every entry with its status", [(e.id, e.text, e.status, e.supersedes, e.superseded_by) for e in adapter.entries()])
show("6c. the log keeps the old rule's events", [(e["seq"], e["type"], e["payload"].get("id")) for e in adapter.events() if e["type"].startswith("rule_")])
old_ids = {e.id for e in adapter.entries("superseded")}
delivered_ids = {e.id for e in after.entries}
print("\nsuperseded entry returned by retrieval:", bool(old_ids & delivered_ids))
