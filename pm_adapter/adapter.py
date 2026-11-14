"""PM-Adapter v2: the adapter owns the state.

It validates the packet (R1, R2), logs events (R4), folds the log into current state (R5), derives decision fields (R6, R7), keeps the
durable store and removes what is superseded (R8 to R11), retrieves (R12 to R15), builds the match question and parses the answer
(R16 to R20), and assembles the bounded packet for the next worker (R21 to R23). It makes no model call and no network call: every
function is a lookup or a computation. The model call of the match step is a function the caller supplies.

Frame input (the original Morse frames) still works through ``pm_adapter.legacy``; ``Adapter`` accepts a frame string wherever the
original ``Adapter`` did. That path needs the optional ``pro-memoria`` package.
"""
from __future__ import annotations

import json
from collections.abc import Mapping

from . import legacy
from .derived import DerivedRegistry
from .errors import PMAdapterError, RetrievalError, SchemaError, StoreError, ValidationError
from .log import EventLog
from .match import MatchRequest, MatchResult, run_match
from .packet import DEFAULT_BUDGET_CHARS, Packet, check_budget, delivered_item
from .schema import Schema, check_evidence, is_legacy_schema
from .store import CONFIRMED, SUPERSEDED, UNVERIFIED, Entry, EntryFold, current

DEFAULT_TABLE_LIMIT = 24                 # R13: lines offered to the match step; the range the author tested with a small rule set; beyond it retrieval carries a flag
OUTSIDE_TESTED_RANGE = "outside_tested_range"


class Retrieval:
    """Result of ``Adapter.retrieve``. ``mode`` is "key" or "table"; ``flag`` is None or "outside_tested_range" (R14)."""

    def __init__(self, mode, entries, flag, match_request):
        self.mode, self.entries, self.flag, self.match_request = mode, list(entries), flag, match_request

    def to_dict(self):
        return {"mode": self.mode, "flag": self.flag, "entries": [e.to_dict() for e in self.entries],
                "match_prompt": self.match_request.prompt() if self.match_request else None}


class Adapter:
    def __init__(self, schema, use_ecc=False, *, measurement_sources=(), budget_chars=DEFAULT_BUDGET_CHARS,
                 table_limit=DEFAULT_TABLE_LIMIT, registry=None):
        if schema is None:
            raise TypeError("schema is required")
        self.schema = schema                              # the schema dict, as in the original
        self.use_ecc = use_ecc
        self.budget_chars = budget_chars
        self.table_limit = table_limit
        self._measurement_sources = frozenset(measurement_sources)
        self._legacy = legacy.LegacyDecoder(schema, use_ecc)
        self.registry = registry if registry is not None else DerivedRegistry()
        self._schema = None if is_legacy_schema(schema) else Schema.from_dict(schema)
        if self._schema is not None:
            for f in self._schema.fields:
                if f.derived is not None and f.derived not in self.registry:
                    raise SchemaError("field %r names the derived function %r, which is not registered" % (f.name, f.derived))
        self._log = EventLog()
        self._fold = EntryFold()

    # ------------------------------------------------------------------ helpers
    def _need_v2(self):
        if self._schema is None:
            raise PMAdapterError("this adapter was built from a legacy frame schema (byte fields); v2 features need a v2 schema")
        return self._schema

    def _label(self, source, evidence):
        """R3: only the adapter decides that a field is 'measured': the event's source must be a configured measurement source and the
        event must name its evidence. Anything a caller merely asserts is 'claimed'."""
        if source in self._measurement_sources and isinstance(evidence, str) and evidence:
            return "measured"
        return "claimed"

    @staticmethod
    def _evidence(evidence):
        """R3a: None (no evidence) or an opaque id; anything else is refused. Descriptive text goes in ``note``, which only the log keeps."""
        return None if evidence is None else check_evidence(evidence)

    def _entries(self):
        raw = self._log.raw()
        for e in raw[self._fold.upto:]:
            self._fold.apply(e)
        self._fold.upto = len(raw)
        return self._fold.entries()

    # ------------------------------------------------------------------ events and state (R4, R5)
    def init_state(self, fields, *, source, evidence=None, note=None):
        """Log one ``state_init`` event per field. Unknown fields and out-of-vocabulary values are rejected, nothing is repaired."""
        schema = self._need_v2()
        valid = schema.validate_fields(fields)
        evidence = self._evidence(evidence)
        prov = self._label(source, evidence)
        return [self._log.append("state_init", {"field": n, "value": v}, source=source, provenance=prov, evidence=evidence, note=note)
                for n, v in valid.items()]

    def change_state(self, field, value, *, source, evidence=None, note=None):
        """Log a ``state_change`` event (old value from the folded state, new value validated)."""
        schema = self._need_v2()
        valid = schema.validate_fields({field: value})
        evidence = self._evidence(evidence)
        old = self.state().get(field, {}).get("value")
        return self._log.append("state_change", {"field": field, "old": old, "new": valid[field]}, source=source,
                                provenance=self._label(source, evidence), evidence=evidence, note=note)

    def notice(self, kind, *, source, evidence=None, note=None, **fields):
        """Log a ``notice`` event, for example ``notice("freeze", source="scheduler", from_hop=50, to_hop=60)``."""
        self._need_v2()
        if not isinstance(kind, str) or not kind:
            raise ValidationError("notice", "kind must be a non-empty string")
        if kind == "freeze":
            a, b = fields.get("from_hop"), fields.get("to_hop")
            if type(a) is not int or type(b) is not int or a > b:
                raise ValidationError("notice", "a freeze notice needs int from_hop <= to_hop")
        try:
            json.dumps(fields)
        except TypeError as exc:
            raise ValidationError("notice", "fields must be JSON-serialisable: %s" % exc)
        evidence = self._evidence(evidence)
        return self._log.append("notice", dict(fields, kind=kind), source=source, provenance=self._label(source, evidence), evidence=evidence, note=note)

    def events(self, since=0, until=None):
        return self._log.events(since, until)

    def state(self):
        """Current state, folded from the log (R5): {field: {value, provenance, evidence, seq}} for the fields that were set."""
        out = {}
        for e in self._log.raw():
            p = e["payload"]
            if e["type"] in ("state_init", "state_change"):
                out[p["field"]] = {"value": p["value"] if e["type"] == "state_init" else p["new"], "provenance": e["provenance"],
                                   "evidence": e["evidence"], "seq": e["seq"]}
        return out

    def derive(self, name, *, hop):
        """Compute one derived field now (R6, R7): value, provenance 'derived', function name, event seqs."""
        if name not in self.registry:
            raise PMAdapterError("no derived function %r" % (name,))
        return self.registry.compute(name, self._log.raw(), {"hop": hop})

    # ------------------------------------------------------------------ the vault (R8 to R11)
    def store_rule(self, key, text, *, source, evidence=None, note=None):
        """Write an UNVERIFIED entry under a key of the schema's closed vocabulary. It is not returned by retrieval until confirmed."""
        schema = self._need_v2()
        schema.check_key(key)
        if not isinstance(text, str) or not text.strip():
            raise StoreError("an entry needs text")
        evidence = self._evidence(evidence)
        rid = "r%d" % (len(self._log) + 1)
        self._log.append("rule_stored", {"id": rid, "key": key, "text": text}, source=source, provenance=self._label(source, evidence), evidence=evidence,
                         note=note)
        return next(e for e in self._entries() if e.id == rid)

    def confirm(self, entry_id, *, evidence, source, note=None):
        """R11: the only way an entry becomes confirmed. ``evidence`` is an opaque id naming what confirms it (R3a: a short token such as
        "test.mod_01.pass", never descriptive text; describe it in ``note``, which only the log keeps).

        R9a, WRITE ORDER: if another confirmed entry exists under the key, it is marked superseded (R9) and stays in the log. Supersession is
        by write order: the newest confirmed entry under a key always wins. The adapter cannot tell an old truth from a new error, so the
        CALLER's duty is to confirm only verified facts, in the order in which they became true (a later confirmed write of an old or wrong
        value wins)."""
        self._need_v2()
        if not isinstance(evidence, str) or not evidence.strip():
            raise StoreError("confirmation must name its evidence")
        check_evidence(evidence)
        entries = self._entries()
        target = next((e for e in entries if e.id == entry_id), None)
        if target is None:
            raise StoreError("no entry %r" % (entry_id,))
        if target.status != UNVERIFIED:
            raise StoreError("entry %s is %s, only an unverified entry can be confirmed" % (entry_id, target.status))
        old = next((e for e in entries if e.key == target.key and e.status == CONFIRMED), None)
        self._log.append("rule_confirmed", {"id": entry_id, "key": target.key, "evidence": evidence, "supersedes": old.id if old else None},
                         source=source, provenance=self._label(source, evidence), evidence=evidence, note=note)
        if old is not None:
            self._log.append("rule_superseded", {"id": old.id, "superseded_by": entry_id}, source="adapter", provenance="derived",
                             evidence=evidence)
        return next(e for e in self._entries() if e.id == entry_id)

    def store_confirmed(self, key, text, *, evidence, source, note=None):
        """store_rule then confirm in one call. Same write-order rule as ``confirm`` (R9a): the newest confirmed entry under the key wins."""
        evidence = check_evidence(evidence)
        return self.confirm(self.store_rule(key, text, source=source, evidence=evidence, note=note).id, evidence=evidence, source=source, note=note)

    def entries(self, status=None):
        """Audit view: copies of the entries of every status (superseded ones included) or of one status."""
        out = self._entries()
        return [e for e in out if status is None or e.status == status]

    # ------------------------------------------------------------------ retrieval (R12 to R15)
    def retrieve(self, key=None, *, item=None, include_unverified=False, table_limit=None):
        """With a key: the current confirmed entry for that key (exact lookup, R12). Without a key: the whole table of current confirmed
        entries plus a match request for ``item`` (R13); when MORE THAN ``table_limit`` LINES are offered (confirmed entries, plus unverified
        ones when ``include_unverified`` is true; default limit 24) the result still works but carries the flag 'outside_tested_range' (R14).
        Unverified entries are returned only when ``include_unverified`` is true (R10). Superseded entries are never returned (R9). There is
        no similarity search in this path (R15)."""
        schema = self._need_v2()
        limit = self.table_limit if table_limit is None else table_limit
        if type(limit) is not int or limit < 0:
            raise RetrievalError("table_limit must be a non-negative int")
        entries = self._entries()
        if key is not None:
            schema.check_key(key)
            hits = sorted(current(entries, key, include_unverified=include_unverified), key=lambda e: (e.status != CONFIRMED, -e.seq))
            return Retrieval("key", hits, None, None)
        if item is None:
            raise RetrievalError("retrieval without a key needs the item text: the match question is about it")
        table = current(entries, None, include_unverified=include_unverified)
        flag = OUTSIDE_TESTED_RANGE if len(table) > limit else None        # R14: the number of lines offered to the match step
        return Retrieval("table", table, flag, MatchRequest(item, table) if table else None)

    # ------------------------------------------------------------------ match step (R16 to R20)
    def match(self, request, call_model, *, max_tokens, verify=None) -> MatchResult:
        """Run the match question through the caller's ``call_model(prompt, max_tokens)``. ``max_tokens`` is required: no default (R18).
        The answer is accepted only if it is exactly one existing entry name; anything else delivers no rule (R17). See pm_adapter.match."""
        return run_match(request, call_model, max_tokens=max_tokens, verify=verify)

    # ------------------------------------------------------------------ packet assembly (R21 to R23)
    def assemble(self, target, *, hop=None, delivered=(), budget_chars=None) -> Packet:
        """Build the outgoing packet for ``target``: task-spec reference, decision fields (derived values from the log, validated values from
        state), and the delivered entries marked 'applies to <target>' with their provenance tag. ``delivered`` holds Entry objects or one
        MatchResult. Raises BudgetError if the content does not fit (R22). There is no argument that attaches a transcript (R23)."""
        schema = self._need_v2()
        if not isinstance(target, str) or not target:
            raise ValidationError("target", "a non-empty string is required")
        budget = self.budget_chars if budget_chars is None else budget_chars
        if type(budget) is not int or budget <= 0:
            raise ValidationError("budget_chars", "a positive int is required")
        if isinstance(delivered, MatchResult):
            delivered = [delivered.entry] if delivered.rule_delivered else []
        events, state = self._log.raw(), self.state()
        decision, missing = {}, []
        for f in schema.fields:
            if f.role != "decision":
                continue
            if f.derived is not None:
                decision[f.name] = self.registry.compute(f.derived, events, {"hop": hop})
            elif f.name in state:
                s = state[f.name]
                decision[f.name] = {"value": s["value"], "provenance": s["provenance"], "evidence": s["evidence"], "seq": s["seq"]}
            else:
                missing.append(f.name)
        live = {e.id: e for e in self._entries()}
        store = []
        for e in delivered:
            now = live.get(getattr(e, "id", None))
            if now is None or now.status == SUPERSEDED:
                raise StoreError("entry %r is not a current entry of this adapter's store; a superseded entry is never delivered" % (getattr(e, "id", e),))
            store.append(delivered_item(now, target))
        packet = Packet(target, hop, schema.task_spec_ref, decision, store, missing)
        parts = [("task_spec", schema.task_spec_ref)] + [("decision:%s" % n, d) for n, d in decision.items()] + \
                [("store:%s" % s["key"], s) for s in store]
        check_budget(packet, parts, budget)
        return packet

    # ------------------------------------------------------------------ output modes (R15, section 3.8)
    @staticmethod
    def _decoded(mapping):
        return {k: {"label": v} for k, v in mapping.items()}

    def to_json(self, x):
        """A Packet gives its dict; a validated {field: value} mapping gives a copy; a frame string gives the original labels dict (legacy)."""
        if isinstance(x, Packet):
            return x.to_dict()
        if isinstance(x, str):
            return self._legacy.to_json(x)
        if isinstance(x, Mapping):
            return dict(self._need_v2().validate_fields(dict(x), writable_only=False))
        raise TypeError("to_json takes a Packet, a mapping or a frame string")

    def to_english(self, x):
        """The same content as sentences, for people."""
        if isinstance(x, Packet):
            return x.to_english()
        if isinstance(x, str):
            return self._legacy.to_english(x)
        if isinstance(x, Mapping):
            return ", ".join("%s: %s" % (k, v) for k, v in self._need_v2().validate_fields(dict(x), writable_only=False).items())
        raise TypeError("to_english takes a Packet, a mapping or a frame string")

    def to_events(self, x=None, previous=None, *, since=0, until=None):
        """No argument: the event log (or a slice by sequence number). A frame string (and optionally ``previous``): the original frame events
        (state_init and value_change). Two mappings: the same shape from their difference."""
        if x is None:
            return self._log.events(since, until)
        if isinstance(x, str):
            return self._legacy.to_events(x, previous)
        if isinstance(x, Mapping):
            fields = {"fields": [{"name": k} for k in x]}
            return legacy.to_events(self._decoded(x), self._decoded(previous) if previous is not None else None, fields)
        raise TypeError("to_events takes nothing, a frame string or a mapping")

    def to_semantic(self, x):
        """Kept for compatibility: a dense text description. NOT recommended for retrieval: in the author's runs similarity retrieval lost the
        rule as the store grew and did not bridge a paraphrased key. Retrieve by key (``retrieve``) instead."""
        if isinstance(x, str):
            return self._legacy.to_semantic(x)
        if isinstance(x, Mapping):
            return legacy.to_rag(self._decoded(x), {"fields": [{"name": k} for k in x]})
        raise TypeError("to_semantic takes a mapping or a frame string")

    # legacy frame access under the original method name
    def decode(self, frame):
        return self._legacy.decode(frame)
