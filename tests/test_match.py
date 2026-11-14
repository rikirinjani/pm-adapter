"""R16 to R20: the match step. The model call is a caller-supplied function; the adapter parses and never guesses."""
import ast
import unittest
from pathlib import Path

import common
from pm_adapter import MatchError, MatchRequest, NONE_ANSWER_IS_RELIABLE, RetrievalError, parse_reply, run_match


def request(n=3):
    a = common.demo()
    keys = list(a._schema.keys)[:n]
    for k in keys:
        a.store_confirmed(k, "return %s" % k, evidence="t", source="test-runner")
    return a, a.retrieve(item="Return what is left over after the split.").match_request, keys


class PromptTests(unittest.TestCase):
    def test_prompt_has_the_item_every_line_and_the_question(self):
        a, req, keys = request(4)
        p = req.prompt()
        self.assertIn("Return what is left over after the split.", p)
        for k in keys:
            self.assertIn("- %s: return %s" % (k, k), p)
        self.assertIn("Which one of these lines applies to this item?", p)
        self.assertIn("or none", p)

    def test_request_needs_an_item(self):
        with self.assertRaises(RetrievalError):
            MatchRequest("  ", [])

    def test_names_are_keys_unless_a_key_repeats(self):
        a = common.demo()
        a.store_confirmed("mod", "return x % 13", evidence="t", source="test-runner")
        a.store_rule("mod", "return x % 7", source="agent")
        req = a.retrieve(item="x", include_unverified=True).match_request
        self.assertEqual(len(set(req.names)), 2)                                 # a repeated key is told apart by entry id


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.a, self.req, self.keys = request(3)

    def test_exact_name_is_accepted(self):
        for k in self.keys:
            r = parse_reply(k, self.req)
            self.assertEqual((r.status, r.name, r.rule_delivered), ("matched", k, True))
            self.assertEqual(r.entry.key, k)

    def test_whitespace_and_one_pair_of_quotes_are_tolerated_nothing_else(self):
        self.assertEqual(parse_reply("  off\n", self.req).status, "matched")
        self.assertEqual(parse_reply("'off'", self.req).status, "matched")
        self.assertEqual(parse_reply('"off"', self.req).status, "matched")
        self.assertEqual(parse_reply("`off`", self.req).status, "matched")
        for bad in ("off.", "The answer is off", "Off", "off, scale", "off scale", "off or scale", "**off**", "o"):
            r = parse_reply(bad, self.req)
            self.assertEqual((r.status, r.entry), ("unknown", None), bad)

    def test_none_means_no_rule(self):
        for t in ("none", "None", " NONE "):
            r = parse_reply(t, self.req)
            self.assertEqual((r.status, r.entry, r.rule_delivered), ("none", None, False))

    def test_empty_reply_means_no_rule(self):
        for t in ("", "   ", "\n", None):
            r = parse_reply(t, self.req)
            self.assertEqual((r.status, r.entry), ("empty", None), repr(t))

    def test_cut_reply_means_no_rule_even_if_it_looks_like_a_name(self):
        r = parse_reply("off", self.req, finish_reason="length")
        self.assertEqual((r.status, r.entry, r.rule_delivered), ("cut", None, False))
        r = parse_reply({"text": "off", "finish_reason": "length"}, self.req)
        self.assertEqual(r.status, "cut")
        r = parse_reply({"text": "", "finish_reason": "length"}, self.req)
        self.assertEqual(r.status, "cut")

    def test_unknown_name_means_no_rule(self):
        for t in ("mod2", "nonexistent", "zz", "r1"):
            self.assertEqual(parse_reply(t, self.req).status, "unknown", t)

    def test_a_name_of_an_entry_not_in_this_request_is_unknown(self):
        self.assertEqual(parse_reply("fmult", self.req).status, "unknown")       # a schema key without a confirmed entry

    def test_mapping_and_object_replies(self):
        class R:
            text, finish_reason = "off", "stop"
        self.assertEqual(parse_reply({"text": "off", "finish_reason": "stop"}, self.req).status, "matched")
        self.assertEqual(parse_reply(R(), self.req).status, "matched")


class RunMatchTests(unittest.TestCase):
    def setUp(self):
        self.a, self.req, self.keys = request(3)

    def test_the_callers_function_gets_the_prompt_and_the_callers_token_limit(self):
        seen = []

        def model(prompt, max_tokens):
            seen.append((prompt, max_tokens))
            return "off"
        r = self.a.match(self.req, model, max_tokens=4321)
        self.assertEqual(seen, [(self.req.prompt(), 4321)])
        self.assertTrue(r.rule_delivered)

    def test_the_token_limit_has_no_default(self):
        with self.assertRaises(TypeError):
            self.a.match(self.req, lambda p, m: "off")                           # missing keyword: Python refuses
        with self.assertRaises(TypeError):
            run_match(self.req, lambda p, m: "off")
        for bad in (0, -5, 1.5, "100", None, True):
            with self.assertRaises(MatchError, msg=repr(bad)):
                self.a.match(self.req, lambda p, m: "off", max_tokens=bad)
        with self.assertRaises(MatchError):
            self.a.match(self.req, "not a function", max_tokens=100)

    def test_failed_answers_never_fall_back_to_a_guess(self):
        for reply in ("", None, "none", "gibberish", {"text": "off", "finish_reason": "length"}, "off scale"):
            r = self.a.match(self.req, lambda p, m, reply=reply: reply, max_tokens=100)
            self.assertIsNone(r.entry)
            self.assertFalse(r.rule_delivered)

    def test_an_error_in_the_callers_function_propagates(self):
        def boom(prompt, max_tokens):
            raise TimeoutError("model down")
        with self.assertRaises(TimeoutError):
            self.a.match(self.req, boom, max_tokens=100)

    def test_verify_hook(self):
        calls = []
        r = self.a.match(self.req, lambda p, m: "off", max_tokens=100, verify=lambda e: calls.append(e.key) or True)
        self.assertEqual((r.status, calls), ("matched", ["off"]))
        r = self.a.match(self.req, lambda p, m: "off", max_tokens=100, verify=lambda e: False)
        self.assertEqual((r.status, r.entry, r.rule_delivered), ("rejected", None, False))
        # verify is not called when there is nothing to verify
        called = []
        self.a.match(self.req, lambda p, m: "none", max_tokens=100, verify=lambda e: called.append(1))
        self.assertEqual(called, [])

    def test_none_is_documented_as_unreliable(self):
        self.assertIs(NONE_ANSWER_IS_RELIABLE, False)
        import pm_adapter.match as m
        self.assertIn("NOT reliable", m.__doc__)
        self.assertIn("no default", m.__doc__)


class NoModelClientTests(unittest.TestCase):
    """R16: the adapter imports no model client and makes no network call."""

    def test_the_package_imports_only_the_standard_library_and_itself(self):
        allowed_stdlib = {"__future__", "copy", "dataclasses", "json", "logging", "pathlib", "re", "typing", "collections"}
        banned = {"requests", "urllib", "http", "socket", "ssl", "openai", "anthropic", "httpx", "aiohttp", "subprocess", "ftplib", "smtplib"}
        pkg = Path(common.ROOT) / "pm_adapter"
        seen = set()
        for f in pkg.glob("*.py"):
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [n.name.split(".")[0] for n in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    mods = [(node.module or "").split(".")[0]]
                for m in mods:
                    seen.add(m)
                    self.assertNotIn(m, banned, "%s imports %s" % (f.name, m))
        self.assertTrue(seen <= allowed_stdlib | {"pro_memoria"}, sorted(seen - allowed_stdlib))


if __name__ == "__main__":
    unittest.main()
