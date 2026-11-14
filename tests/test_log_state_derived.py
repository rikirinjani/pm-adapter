"""R3 to R7: provenance, the append-only log, state as a fold, derived state (the freeze example)."""
import json
import unittest

import common
from pm_adapter import EventLog, PMAdapterError, ValidationError
from pm_adapter.log import EVENT_TYPES


class ProvenanceTests(unittest.TestCase):
    def test_measured_needs_a_trusted_source_and_named_evidence(self):
        a = common.demo()
        a.change_state("outcome", "pass", source="test-runner", evidence="test.mod_01.pass")     # trusted + evidence
        a.change_state("phase", "act", source="agent", evidence="my-own-note")                              # untrusted source
        a.change_state("outcome", "fail", source="test-runner")                                             # trusted but no evidence
        provs = [e["provenance"] for e in a.events()]
        self.assertEqual(provs, ["measured", "claimed", "claimed"])

    def test_a_caller_cannot_choose_the_label(self):
        import inspect
        for fn in (common.demo().init_state, common.demo().change_state, common.demo().notice, common.demo().store_rule):
            self.assertNotIn("provenance", inspect.signature(fn).parameters)

    def test_no_measurement_source_configured_means_nothing_is_measured(self):
        a = common.demo(measurement_sources=())
        a.change_state("outcome", "pass", source="test-runner", evidence="e1")
        self.assertEqual(a.events()[0]["provenance"], "claimed")

    def test_state_carries_label_and_evidence(self):
        a = common.demo()
        a.change_state("outcome", "pass", source="test-runner", evidence="e1")
        s = a.state()["outcome"]
        self.assertEqual((s["value"], s["provenance"], s["evidence"]), ("pass", "measured", "e1"))


class EvidenceIdTests(unittest.TestCase):
    """R3a: the evidence shown in a packet is an opaque id; descriptive text stays in the log (the note)."""

    GOOD = ["t", "test.mod_01.pass", "fix-h10", "ci:run-42", "A" * 40, "prefill", "a.b_c-d:e9"]
    BAD = ["test:lib/mod_01.py passed", "has space", "slash/path", "", "x" * 41, "\u00fcn\u00ef", "tab\there", "semi;colon", "new\nline", 5, ["t"], "a@b"]

    def test_the_rule(self):
        from pm_adapter.schema import EVIDENCE_RULE, check_evidence
        self.assertIn("1 to 40 characters", EVIDENCE_RULE)
        for ok in self.GOOD:
            self.assertEqual(check_evidence(ok), ok)
        for bad in self.BAD:
            with self.assertRaises(ValidationError, msg=repr(bad)):
                check_evidence(bad)

    def test_every_writer_refuses_a_descriptive_evidence_and_logs_nothing(self):
        a = common.demo()
        calls = [lambda: a.init_state({"phase": "act"}, source="agent", evidence="my own note"),
                 lambda: a.change_state("phase", "act", source="agent", evidence="my own note"),
                 lambda: a.notice("freeze", source="s", from_hop=1, to_hop=2, evidence="my own note"),
                 lambda: a.store_rule("mod", "return x % 13", source="agent", evidence="my own note"),
                 lambda: a.store_confirmed("mod", "return x % 13", source="agent", evidence="test:lib/mod_01.py passed")]
        for c in calls:
            with self.assertRaises(ValidationError):
                c()
        e = a.store_rule("mod", "return x % 13", source="agent")
        with self.assertRaises(ValidationError):
            a.confirm(e.id, evidence="test:lib/mod_01.py passed", source="test-runner")
        self.assertEqual([x["type"] for x in a.events()], ["rule_stored"])      # only the one valid write is in the log

    def test_descriptive_text_goes_to_the_log_only_never_to_the_packet(self):
        a = common.demo()
        a.init_state({"phase": "act"}, source="agent")
        note = "lib/mod_01.py passed in the nightly run of the old ticket"
        a.store_confirmed("mod", "return x % 13", evidence="test.mod_01.pass", source="test-runner", note=note)
        logged = [e for e in a.events() if e.get("note")]
        self.assertEqual({e["type"] for e in logged}, {"rule_stored", "rule_confirmed"})
        self.assertTrue(all(e["note"] == note for e in logged))
        packet = a.assemble("lib/mod_02.py", hop=1, delivered=a.retrieve("mod").entries)
        for text in (packet.to_json(), a.to_english(packet)):
            self.assertIn("test.mod_01.pass", text)                             # the opaque id is shown
            self.assertNotIn("nightly", text)
            self.assertNotIn("lib/mod_01.py", text)                             # nothing of the note, no earlier ticket's file name

    def test_a_packet_never_contains_a_note_from_any_event(self):
        a = common.demo()
        a.change_state("outcome", "pass", source="test-runner", evidence="ev1", note="SECRET-NOTE-1")
        a.notice("freeze", source="s", from_hop=1, to_hop=5, evidence="ev2", note="SECRET-NOTE-2")
        p = a.assemble("t", hop=3)
        self.assertNotIn("SECRET-NOTE", p.to_json() + a.to_english(p))
        self.assertEqual(sorted(e["note"] for e in a.events()), ["SECRET-NOTE-1", "SECRET-NOTE-2"])


class LogTests(unittest.TestCase):
    def test_event_types(self):
        self.assertEqual(set(EVENT_TYPES), {"state_init", "state_change", "notice", "rule_stored", "rule_confirmed", "rule_superseded"})

    def test_each_event_has_seq_source_and_provenance(self):
        a = common.demo()
        a.init_state({"phase": "idle"}, source="agent")
        a.change_state("phase", "act", source="agent")
        a.notice("freeze", source="scheduler", from_hop=1, to_hop=2)
        evs = a.events()
        self.assertEqual([e["seq"] for e in evs], [1, 2, 3])
        for e in evs:
            self.assertTrue(e["source"])
            self.assertIn(e["provenance"], ("measured", "derived", "claimed"))

    def test_state_change_records_old_and_new_like_the_original_events(self):
        a = common.demo()
        a.init_state({"phase": "idle"}, source="agent")
        ev = a.change_state("phase", "act", source="agent")
        self.assertEqual(ev["payload"], {"field": "phase", "old": "idle", "new": "act"})
        self.assertEqual(a.events()[0]["payload"], {"field": "phase", "value": "idle"})

    def test_the_log_is_append_only(self):
        log = EventLog()
        for name in ("remove", "clear", "pop", "update", "delete", "insert", "__setitem__", "__delitem__"):
            self.assertFalse(hasattr(log, name), name)
        log.append("notice", {"kind": "x"}, source="s", provenance="claimed")
        copy_ = log.events()
        copy_[0]["payload"]["kind"] = "tampered"
        copy_.clear()
        self.assertEqual(log.events()[0]["payload"]["kind"], "x")         # callers get copies
        with self.assertRaises(PMAdapterError):
            log.append("not_a_type", {}, source="s", provenance="claimed")

    def test_current_state_is_a_fold_over_the_log(self):
        a = common.demo()
        a.init_state({"phase": "idle", "outcome": "running"}, source="agent")
        a.change_state("phase", "act", source="agent")
        a.change_state("outcome", "pass", source="agent")
        a.change_state("phase", "review", source="agent")
        # fold by hand from the events() view and compare with state()
        folded = {}
        for e in a.events():
            p = e["payload"]
            if e["type"] == "state_init":
                folded[p["field"]] = p["value"]
            elif e["type"] == "state_change":
                folded[p["field"]] = p["new"]
        self.assertEqual({k: v["value"] for k, v in a.state().items()}, folded)
        self.assertEqual(folded, {"phase": "review", "outcome": "pass"})

    def test_event_slices(self):
        a = common.demo()
        for hop in (1, 2, 3, 4):
            a.notice("tick", source="s", hop=hop)
        self.assertEqual([e["seq"] for e in a.to_events(since=1, until=3)], [2, 3])


class FreezeExampleTests(unittest.TestCase):
    def setUp(self):
        self.a = common.demo()
        self.a.init_state({"phase": "act", "outcome": "running"}, source="agent")
        self.a.notice("freeze", source="scheduler", from_hop=50, to_hop=60)

    def test_freeze_active_is_derived_from_the_schedule_notice(self):
        for hop, expected in ((49, False), (50, True), (55, True), (60, True), (61, False), (150, False)):
            d = self.a.derive("freeze_active", hop=hop)
            self.assertEqual(d["value"], expected, hop)

    def test_derived_entry_records_function_and_events_and_provenance(self):
        d = self.a.derive("freeze_active", hop=55)
        self.assertEqual((d["provenance"], d["function"], d["events"]), ("derived", "freeze_active", [3]))

    def test_the_packet_holds_the_derived_value_not_the_raw_schedule(self):
        p = self.a.assemble("lib/mod_02.py", hop=55)
        self.assertIs(p.decision["freeze_active"]["value"], True)
        text = p.to_json()
        for raw in ("from_hop", "to_hop", "50", "60", "scheduler"):
            self.assertNotIn(raw, text)           # nothing of the schedule is pinned in the packet
        self.assertIs(self.a.assemble("lib/mod_02.py", hop=70).decision["freeze_active"]["value"], False)

    def test_derivation_is_deterministic_and_does_not_touch_the_log(self):
        before = self.a.events()
        first = [self.a.derive("freeze_active", hop=h) for h in range(40, 70)]
        second = [self.a.derive("freeze_active", hop=h) for h in range(40, 70)]
        self.assertEqual(first, second)
        self.assertEqual(self.a.events(), before)

    def test_a_derived_field_needs_the_hop(self):
        with self.assertRaises(ValidationError):
            self.a.assemble("lib/mod_02.py")                # hop missing: no guess
        with self.assertRaises(ValidationError):
            self.a.derive("freeze_active", hop=None)

    def test_bad_freeze_notice_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.a.notice("freeze", source="scheduler", from_hop=60, to_hop=50)
        with self.assertRaises(ValidationError):
            self.a.notice("freeze", source="scheduler")

    def test_transient_and_invariant_fields_stay_out_of_the_packet(self):
        self.a.change_state("log_tail", "x" * 500, source="agent")
        p = self.a.assemble("lib/mod_02.py", hop=55)
        self.assertNotIn("log_tail", p.decision)
        self.assertNotIn("task_spec", p.decision)
        self.assertEqual(p.task_spec, "task-spec-v1")
        self.assertNotIn("xxxxx", p.to_json())


if __name__ == "__main__":
    unittest.main()
