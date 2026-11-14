"""R8 to R15: the vault (entries, supersession, unverified entries, explicit confirmation), exact-key lookup among 1,000 entries,
the table limit and the outside_tested_range flag, no similarity search in the default path."""
import dataclasses
import inspect
import unittest

import common
from pm_adapter import Adapter, OUTSIDE_TESTED_RANGE, RetrievalError, StoreError, ValidationError


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.a = common.demo()

    def test_entry_fields(self):
        e = self.a.store_rule("mod", "return x % 13", source="agent")
        self.assertEqual({f.name for f in dataclasses.fields(e)}, {"id", "key", "text", "status", "provenance", "seq", "confirmed_seq",
                                                                    "supersedes", "superseded_by", "evidence", "source"})
        self.assertEqual((e.key, e.text, e.status, e.provenance, e.supersedes), ("mod", "return x % 13", "unverified", "claimed", None))
        self.assertEqual(e.seq, 1)                                   # write sequence number = log position

    def test_key_must_be_in_the_closed_vocabulary(self):
        with self.assertRaises(ValidationError):
            self.a.store_rule("modulo", "return x % 13", source="agent")
        with self.assertRaises(ValidationError):
            self.a.retrieve("modulo")
        with self.assertRaises(StoreError):
            self.a.store_rule("mod", "   ", source="agent")

    def test_confirmation_is_explicit_and_names_its_evidence(self):
        e = self.a.store_rule("mod", "return x % 13", source="agent")
        for bad in (None, "", "   "):
            with self.assertRaises(StoreError):
                self.a.confirm(e.id, evidence=bad, source="test-runner")
        self.assertEqual(self.a.entries()[0].status, "unverified")
        c = self.a.confirm(e.id, evidence="test.mod_01.pass", source="test-runner")
        self.assertEqual((c.status, c.evidence, c.provenance, c.confirmed_seq), ("confirmed", "test.mod_01.pass", "measured", 2))
        with self.assertRaises(StoreError):
            self.a.confirm(e.id, evidence="again", source="test-runner")      # only an unverified entry can be confirmed
        with self.assertRaises(StoreError):
            self.a.confirm("r999", evidence="x", source="test-runner")

    def test_supersession_old_entry_is_never_returned_again_but_stays_in_the_log(self):
        old = self.a.store_confirmed("mod", "return x % 13", evidence="t1", source="test-runner")
        new = self.a.store_confirmed("mod", "return x % 7", evidence="t2", source="test-runner")
        self.assertEqual([e.text for e in self.a.retrieve("mod").entries], ["return x % 7"])
        self.assertEqual([e.text for e in self.a.retrieve("mod", include_unverified=True).entries], ["return x % 7"])
        table = self.a.retrieve(item="a ticket")
        self.assertEqual([e.text for e in table.entries], ["return x % 7"])
        audit = {e.id: e for e in self.a.entries()}
        self.assertEqual(audit[old.id].status, "superseded")
        self.assertEqual(audit[old.id].superseded_by, new.id)
        self.assertEqual(audit[new.id].supersedes, old.id)
        types = [e["type"] for e in self.a.events()]
        self.assertIn("rule_superseded", types)                                 # the old entry's events are still there for audit
        self.assertEqual(types.count("rule_stored"), 2)
        self.assertEqual(self.a.entries("superseded")[0].text, "return x % 13")

    def test_three_generations_only_the_last_is_current(self):
        for n in (13, 7, 5):
            self.a.store_confirmed("mod", "return x %% %d" % n, evidence="t%d" % n, source="test-runner")
        self.assertEqual([e.text for e in self.a.retrieve("mod").entries], ["return x % 5"])
        self.assertEqual(sorted(e.status for e in self.a.entries()), ["confirmed", "superseded", "superseded"])

    def test_supersession_is_by_write_order_a_later_confirmed_stale_write_wins(self):
        # R9a, stale entries written after the teaching: the adapter cannot tell an old truth from a new error. The newest confirmed entry under a key wins,
        # even if it is the stale one. The caller's duty is to confirm only verified facts, in the order in which they became true.
        true_rule = self.a.store_confirmed("mod", "return x % 13", evidence="test.mod_01.pass", source="test-runner")
        stale = self.a.store_confirmed("mod", "return x % 7", evidence="prefill", source="prefill", note="an old note written after the teaching")
        self.assertEqual([e.text for e in self.a.retrieve("mod").entries], ["return x % 7"])         # the stale line is delivered
        audit = {e.id: e for e in self.a.entries()}
        self.assertEqual(audit[true_rule.id].status, "superseded")                                    # the true rule is the one superseded
        self.assertEqual(audit[stale.id].supersedes, true_rule.id)
        packet = self.a.assemble("lib/mod_02.py", hop=1, delivered=self.a.retrieve("mod").entries)
        self.assertEqual([s["text"] for s in packet.store], ["return x % 7"])
        # the same two writes in the right order give the right answer
        b = common.demo()
        b.store_confirmed("mod", "return x % 7", evidence="prefill", source="prefill")
        b.store_confirmed("mod", "return x % 13", evidence="test.mod_01.pass", source="test-runner")
        self.assertEqual([e.text for e in b.retrieve("mod").entries], ["return x % 13"])

    def test_the_docs_state_the_write_order_rule_and_the_callers_duty(self):
        for doc in (Adapter.confirm.__doc__, Adapter.store_confirmed.__doc__):
            self.assertIn("write order", doc.lower().replace("-", " "))
        self.assertIn("CALLER", Adapter.confirm.__doc__)
        self.assertIn("only verified facts", Adapter.confirm.__doc__)

    def test_confirming_one_key_does_not_touch_another(self):
        self.a.store_confirmed("mod", "return x % 13", evidence="t", source="test-runner")
        self.a.store_confirmed("off", "return x + 5", evidence="t", source="test-runner")
        self.assertEqual(len(self.a.retrieve("mod").entries), 1)
        self.assertEqual(len(self.a.retrieve("off").entries), 1)
        self.assertEqual(self.a.entries("superseded"), [])

    def test_unverified_entries_are_stored_but_not_returned_unless_asked(self):
        self.a.store_rule("mod", "return x % 13", source="agent")
        self.assertEqual(self.a.retrieve("mod").entries, [])
        self.assertEqual([e.text for e in self.a.retrieve("mod", include_unverified=True).entries], ["return x % 13"])
        self.assertEqual(self.a.retrieve(item="a ticket").entries, [])
        self.assertIsNone(self.a.retrieve(item="a ticket").match_request)
        self.assertEqual(len(self.a.retrieve(item="a ticket", include_unverified=True).entries), 1)

    def test_with_unverified_asked_for_the_confirmed_entry_comes_first(self):
        self.a.store_confirmed("mod", "return x % 13", evidence="t", source="test-runner")
        self.a.store_rule("mod", "return x % 99", source="agent")
        got = self.a.retrieve("mod", include_unverified=True).entries
        self.assertEqual([e.status for e in got], ["confirmed", "unverified"])

    def test_only_the_adapter_writes_entries(self):
        e = self.a.store_rule("mod", "return x % 13", source="agent")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            e.status = "confirmed"                                              # an entry is a frozen copy
        got = self.a.entries()
        got.clear()
        self.assertEqual(len(self.a.entries()), 1)                              # views are copies
        public = [n for n in dir(self.a) if not n.startswith("_")]
        self.assertFalse([n for n in public if n in ("vault", "write", "add", "set_entry", "delete", "remove")])


class RetrievalTests(unittest.TestCase):
    def test_exact_key_lookup_among_1000_entries(self):
        keys = ["k%04d" % i for i in range(1000)]
        a = Adapter(common.schema_with_keys(keys))
        for i, k in enumerate(keys):
            a.store_confirmed(k, "%s: return x + %d" % (k, i), evidence="t%d" % i, source="loader")
        self.assertEqual(len(a.entries("confirmed")), 1000)
        for i in range(0, 1000, 7):                                             # 143 lookups, each exactly one right entry
            hit = a.retrieve(keys[i]).entries
            self.assertEqual([e.text for e in hit], ["%s: return x + %d" % (keys[i], i)])
        # supersede 50 keys and look all 1,000 up again: always exactly the current text
        for i in range(0, 1000, 20):
            a.store_confirmed(keys[i], "%s: return x * %d" % (keys[i], i + 1), evidence="s%d" % i, source="loader")
        for i, k in enumerate(keys):
            want = "%s: return x * %d" % (k, i + 1) if i % 20 == 0 else "%s: return x + %d" % (k, i)
            self.assertEqual([e.text for e in a.retrieve(k).entries], [want])
        self.assertEqual(len(a.entries("superseded")), 50)

    def test_lookup_of_a_key_with_no_confirmed_entry_is_empty_not_a_guess(self):
        a = common.demo()
        self.assertEqual(a.retrieve("mod").entries, [])
        self.assertEqual(a.retrieve("mod").mode, "key")

    def test_table_at_or_below_the_limit_has_no_flag_and_a_match_request(self):
        a = common.demo()
        keys = a._schema.keys
        for k in keys:                                                          # 12 confirmed entries
            a.store_confirmed(k, "return %s" % k, evidence="t", source="test-runner")
        r = a.retrieve(item="Return the input moved up by the house-rule amount.")
        self.assertEqual((r.mode, len(r.entries), r.flag), ("table", 12, None))
        self.assertEqual(a.table_limit, 24)                                     # R13: the default limit is 24 lines offered
        self.assertIsNotNone(r.match_request)
        self.assertEqual(sorted(r.match_request.names), sorted(keys))

    def test_exactly_the_limit_has_no_flag_one_more_line_raises_it(self):
        keys = ["k%02d" % i for i in range(25)]
        a = Adapter(common.schema_with_keys(keys))
        for k in keys[:24]:
            a.store_confirmed(k, "return 1", evidence="t", source="s")
        self.assertEqual((len(a.retrieve(item="x").entries), a.retrieve(item="x").flag), (24, None))
        a.store_confirmed(keys[24], "return 1", evidence="t", source="s")
        r = a.retrieve(item="some ticket")
        self.assertEqual((r.mode, len(r.entries), r.flag), ("table", 25, OUTSIDE_TESTED_RANGE))
        self.assertIsNotNone(r.match_request)                                   # it still works: table and match request are returned
        self.assertEqual(OUTSIDE_TESTED_RANGE, "outside_tested_range")
        self.assertIsNone(a.retrieve(item="some ticket", table_limit=25).flag)  # the limit is a parameter

    def test_the_flag_counts_lines_offered_including_unverified_ones_when_asked_for(self):
        # replaces the old test_the_flag_counts_confirmed_entries_only: R14 now counts the lines offered to the match step
        keys = ["k%02d" % i for i in range(100)]
        a = Adapter(common.schema_with_keys(keys))
        for k in keys[:12]:
            a.store_confirmed(k, "return 1", evidence="t", source="s")
        for k in keys[12:]:
            a.store_rule(k, "return 2", source="s")                             # 88 unverified look-alikes
        default = a.retrieve(item="x")
        self.assertEqual((len(default.entries), default.flag), (12, None))      # only the confirmed lines are offered by default
        asked = a.retrieve(item="x", include_unverified=True)
        self.assertEqual((len(asked.entries), asked.flag), (100, OUTSIDE_TESTED_RANGE))   # 100 lines offered: the flag is raised

    def test_retrieval_without_a_key_needs_the_item(self):
        with self.assertRaises(RetrievalError):
            common.demo().retrieve()
        a = common.demo()
        a.store_confirmed("mod", "return x % 13", evidence="t", source="s")
        with self.assertRaises(RetrievalError):
            a.retrieve(item="   ")                                              # a blank item cannot be asked about

    def test_a_bad_table_limit_is_rejected(self):
        for bad in (-1, 1.5, "12"):
            with self.assertRaises(RetrievalError):
                common.demo().retrieve(item="x", table_limit=bad)

    def test_there_is_no_similarity_search_in_the_default_path(self):
        a = common.demo()
        for name in dir(a):
            self.assertFalse(any(w in name.lower() for w in ("similar", "search", "embed", "rank", "fuzzy")), name)
        self.assertEqual(list(inspect.signature(Adapter.retrieve).parameters), ["self", "key", "item", "include_unverified", "table_limit"])

    def test_to_semantic_is_kept_and_documented_as_not_recommended(self):
        a = common.demo()
        out = a.to_semantic({"phase": "act", "outcome": "pass"})
        self.assertIn("phase set to act", out)
        self.assertIn("NOT recommended for retrieval", Adapter.to_semantic.__doc__)


if __name__ == "__main__":
    unittest.main()
