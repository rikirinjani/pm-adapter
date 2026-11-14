"""R21 to R23 and the output modes: packet assembly, the size budget, no transcript, to_json / to_english / to_events."""
import inspect
import json
import unittest

import common
from pm_adapter import Adapter, BudgetError, Packet, StoreError, ValidationError


def loaded():
    a = common.demo()
    a.init_state({"phase": "act", "outcome": "running"}, source="agent")
    a.notice("freeze", source="scheduler", from_hop=50, to_hop=60)
    e = a.store_confirmed("mod", "return x % 13", evidence="test.mod_01.pass", source="test-runner")
    return a, e


class AssemblyTests(unittest.TestCase):
    def test_packet_has_spec_reference_decision_fields_and_marked_entries(self):
        a, e = loaded()
        p = a.assemble("lib/mod_02.py", hop=55, delivered=a.retrieve("mod").entries)
        d = p.to_dict()
        self.assertEqual(d["task_spec"], "task-spec-v1")
        self.assertEqual(set(d["decision"]), {"phase", "outcome", "freeze_active"})
        self.assertEqual(d["decision"]["freeze_active"]["provenance"], "derived")
        self.assertEqual(d["decision"]["phase"]["provenance"], "claimed")
        s = d["store"][0]
        self.assertEqual((s["key"], s["text"], s["applies_to"], s["status"], s["provenance"]), ("mod", "return x % 13", "lib/mod_02.py", "confirmed", "measured"))
        self.assertIn("confirmed by test.mod_01.pass", s["tag"])
        self.assertEqual(d["missing"], [])

    def test_fields_never_set_are_listed_as_missing_not_invented(self):
        a = common.demo()
        p = a.assemble("t", hop=1)
        self.assertEqual(sorted(p.missing), ["outcome", "phase"])
        self.assertEqual(set(p.decision), {"freeze_active"})

    def test_a_match_result_can_be_delivered_and_no_rule_delivers_nothing(self):
        a, e = loaded()
        req = a.retrieve(item="Return what is left over after the split.").match_request
        hit = a.match(req, lambda p, m: "mod", max_tokens=100)
        miss = a.match(req, lambda p, m: "", max_tokens=100)
        self.assertEqual(len(a.assemble("lib/t_37.py", hop=55, delivered=hit).store), 1)
        self.assertEqual(a.assemble("lib/t_37.py", hop=55, delivered=miss).store, [])

    def test_a_superseded_entry_cannot_be_delivered(self):
        a, old = loaded()
        a.store_confirmed("mod", "return x % 7", evidence="t2", source="test-runner")
        with self.assertRaises(StoreError):
            a.assemble("lib/mod_02.py", hop=55, delivered=[old])
        # a stale copy of a current entry is replaced by the live one (a caller cannot forge text)
        live = a.retrieve("mod").entries[0]
        import dataclasses
        forged = dataclasses.replace(live, text="return x % 99")
        self.assertEqual(a.assemble("t", hop=55, delivered=[forged]).store[0]["text"], "return x % 7")

    def test_assembly_is_deterministic(self):
        a, e = loaded()
        one = a.assemble("lib/mod_02.py", hop=55, delivered=a.retrieve("mod").entries).to_json()
        two = a.assemble("lib/mod_02.py", hop=55, delivered=a.retrieve("mod").entries).to_json()
        self.assertEqual(one, two)

    def test_target_and_budget_arguments_are_validated(self):
        a, e = loaded()
        for bad in ("", None, 5):
            with self.assertRaises(ValidationError):
                a.assemble(bad, hop=55)
        for bad in (0, -1, 1.5, "100"):
            with self.assertRaises(ValidationError):
                a.assemble("t", hop=55, budget_chars=bad)


class BudgetTests(unittest.TestCase):
    def test_a_packet_that_fits_is_returned_whole(self):
        a, e = loaded()
        p = a.assemble("lib/mod_02.py", hop=55, delivered=a.retrieve("mod").entries)
        self.assertLessEqual(p.size(), a.budget_chars)
        again = a.assemble("lib/mod_02.py", hop=55, delivered=a.retrieve("mod").entries, budget_chars=p.size())     # exactly fits
        self.assertEqual(again.size(), p.size())

    def test_overflow_raises_and_names_what_did_not_fit_nothing_is_truncated(self):
        a, e = loaded()
        big = "return x + 1  # " + "pad " * 400
        a.store_confirmed("off", big, evidence="t", source="test-runner")
        entries = a.retrieve(item="x").entries
        with self.assertRaises(BudgetError) as cm:
            a.assemble("lib/t_1.py", hop=55, delivered=entries, budget_chars=1500)
        err = cm.exception
        self.assertEqual(err.budget, 1500)
        self.assertGreater(err.size, 1500)
        self.assertEqual([o["part"] for o in err.overflow], ["store:off"])
        self.assertIn("store:off", str(err))

    def test_default_budget_is_a_real_limit_not_unlimited(self):
        a, e = loaded()
        a.store_confirmed("off", "return x + 1  # " + "pad " * 8000, evidence="t", source="test-runner")
        with self.assertRaises(BudgetError):
            a.assemble("t", hop=55, delivered=a.retrieve("off").entries)

    def test_overflow_names_every_part_that_does_not_fit_in_priority_order(self):
        a, e = loaded()
        for k in ("off", "scale", "cap"):
            a.store_confirmed(k, "return x  # " + "w" * 300, evidence="t", source="test-runner")
        entries = a.retrieve(item="x").entries
        with self.assertRaises(BudgetError) as cm:
            a.assemble("t", hop=55, delivered=entries, budget_chars=1100)
        names = [o["part"] for o in cm.exception.overflow]
        self.assertTrue(names and all(n.startswith("store:") for n in names))
        self.assertNotIn("task_spec", names)                                    # earlier parts have priority

    def test_the_budget_is_never_applied_by_cutting_text(self):
        a, e = loaded()
        a.store_confirmed("off", "return x + 1  # " + "pad " * 400, evidence="t", source="test-runner")
        entries = a.retrieve(item="x").entries
        try:
            a.assemble("t", hop=55, delivered=entries, budget_chars=1500)
        except BudgetError:
            pass
        ok = a.assemble("t", hop=55, delivered=entries, budget_chars=10 ** 6)
        self.assertIn("pad " * 400, ok.to_json())                               # the full text is delivered when it fits


class NoTranscriptTests(unittest.TestCase):
    """R23: the worker receives only the packet and the task specification."""

    def test_assemble_has_no_way_to_attach_a_transcript(self):
        params = list(inspect.signature(Adapter.assemble).parameters)
        self.assertEqual(params, ["self", "target", "hop", "delivered", "budget_chars"])
        a, e = loaded()
        for kw in ("transcript", "history", "log", "messages", "context"):
            with self.assertRaises(TypeError):
                a.assemble("t", hop=55, **{kw: "earlier turns"})
        for name in dir(a):
            self.assertFalse(any(w in name.lower() for w in ("transcript", "attach", "history")), name)

    def test_the_packet_is_only_its_five_parts(self):
        a, e = loaded()
        self.assertEqual(set(a.assemble("t", hop=55).to_dict()), {"target", "hop", "task_spec", "decision", "store", "missing"})


class OutputModeTests(unittest.TestCase):
    def test_to_json_of_a_packet_is_its_dict_and_serialises(self):
        a, e = loaded()
        p = a.assemble("lib/mod_02.py", hop=55, delivered=a.retrieve("mod").entries)
        self.assertEqual(a.to_json(p), p.to_dict())
        self.assertEqual(json.loads(p.to_json()), p.to_dict())
        self.assertIsInstance(p, Packet)

    def test_to_english_is_the_same_content_as_sentences(self):
        a, e = loaded()
        text = a.to_english(a.assemble("lib/mod_02.py", hop=55, delivered=a.retrieve("mod").entries))
        for part in ("task-spec-v1", "lib/mod_02.py", "freeze_active is True", "derived by freeze_active", "Rule mod applies to lib/mod_02.py",
                     "return x % 13", "confirmed by test.mod_01.pass"):
            self.assertIn(part, text)

    def test_mapping_inputs_are_validated(self):
        a = common.demo()
        self.assertEqual(a.to_json({"phase": "act"}), {"phase": "act"})
        self.assertEqual(a.to_english({"phase": "act", "outcome": "pass"}), "phase: act, outcome: pass")
        with self.assertRaises(ValidationError):
            a.to_json({"phase": "sleeping"})
        with self.assertRaises(TypeError):
            a.to_json(42)

    def test_to_events_returns_the_log_or_a_slice_and_original_shaped_events_for_mappings(self):
        a, e = loaded()
        full = a.to_events()
        self.assertEqual(len(full), len(a.events()))
        self.assertEqual([x["seq"] for x in a.to_events(since=2, until=3)], [3])
        ev = a.to_events({"phase": "act", "outcome": "pass"}, {"phase": "think", "outcome": "pass"})
        self.assertEqual(ev, [{"event": "value_change", "field": "phase", "old": "think", "new": "act"}])
        init = a.to_events({"phase": "act"})
        self.assertEqual(init, [{"event": "state_init", "field": "phase", "value": "act"}])


if __name__ == "__main__":
    unittest.main()
