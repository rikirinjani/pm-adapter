"""Legacy Morse frame decoder: the original PM-Adapter test suite (legacy_v1/tests/test_adapter.py), carried over and
converted to unittest. Tests that need the optional 'pro-memoria' package are skipped when it is not installed; the optional-import
tests at the end run either way. The original's real-trace tests read ~/self-harness/traces, which exists only on the original author's machine:
they are skipped when there are no traces (the original asserted that at least one decoded)."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

import common
from pm_adapter import Adapter, LegacyUnavailable, load_schema, to_events, to_semantic
from pm_adapter.legacy import LegacyDecoder

try:
    from pro_memoria import encode_bytes
    HAVE_PM1 = True
except ImportError:
    HAVE_PM1 = False

TRACES = sorted((Path.home() / "self-harness" / "traces").glob("*.pm1")) if (Path.home() / "self-harness" / "traces").is_dir() else []


@unittest.skipUnless(HAVE_PM1, "optional package pro-memoria is not installed")
class CarriedOverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = load_schema("default")
        cls.legacy = LegacyDecoder(cls.schema)
        cls.adapter = Adapter(cls.schema)                 # the v2 class on a frame schema: the original public usage still works
        cls.frame = encode_bytes(bytes([1, 2, 3, 0, 0, 0, 1, 0]))          # fixer, act, high confidence, pass
        cls.frame2 = encode_bytes(bytes([1, 2, 2, 0, 0, 0, 1, 0]))         # confidence dropped to medium

    def both(self):
        return (self.legacy, self.adapter)

    def test_schema_loads(self):
        self.assertEqual(self.schema["state_width"], 8)
        self.assertEqual(len(self.schema["fields"]), 8)
        self.assertEqual(self.schema["fields"][0]["name"], "agent_type")

    def test_to_english(self):
        for a in self.both():
            r = a.to_english(self.frame)
            for word in ("fixer", "act", "high", "pass"):
                self.assertIn(word, r)
        self.assertEqual(self.legacy.to_english(self.frame), "agent_type: fixer, phase: act, confidence: high, error_count: 0, tool_calls: 0, "
                                                             "files_touched: 0, outcome: pass, flags: 0")

    def test_to_json(self):
        for a in self.both():
            r = a.to_json(self.frame)
            self.assertEqual((r["agent_type"], r["phase"], r["confidence"], r["outcome"]), ("fixer", "act", "high", "pass"))

    def test_to_semantic(self):
        for a in self.both():
            r = a.to_semantic(self.frame)
            for part in ("Agent state", "agent type", "fixer"):
                self.assertIn(part, r)

    def test_to_events_initial(self):
        for a in self.both():
            events = a.to_events(self.frame)
            self.assertEqual(len(events), 8)
            self.assertTrue(all(e["event"] == "state_init" for e in events))

    def test_to_events_transition_keeps_the_original_event_name(self):
        for a in self.both():
            changes = [e for e in a.to_events(self.frame2, previous_frame=self.frame) if e["event"] == "value_change"] \
                if a is self.legacy else [e for e in a.to_events(self.frame2, self.frame) if e["event"] == "value_change"]
            self.assertEqual(len(changes), 1)
            self.assertEqual((changes[0]["field"], changes[0]["old"], changes[0]["new"]), ("confidence", "high", "medium"))

    def test_unknown_byte_labels_fall_back_to_the_number(self):
        raw = encode_bytes(bytes([99, 0, 0, 0, 0, 0, 0, 0]))
        self.assertIn("99", self.legacy.to_json(raw)["agent_type"])

    def test_decode_returns_raw_bytes_with_labels(self):
        r = self.legacy.decode(self.frame)
        self.assertEqual((r["agent_type"]["raw"], r["agent_type"]["label"]), (1, "fixer"))
        self.assertEqual(self.adapter.decode(self.frame), r)

    def test_custom_schema_loading(self):
        s = load_schema("default")
        self.assertIsInstance(s, dict)
        self.assertIn("fields", s)
        self.assertIn("state_width", s)
        with self.assertRaises(FileNotFoundError):
            load_schema("no_such_schema")

    def test_adapter_raises_on_no_schema(self):
        with self.assertRaises(TypeError):
            Adapter(None)
        with self.assertRaises(TypeError):
            LegacyDecoder(None)

    def test_module_level_original_functions(self):
        decoded = self.legacy.decode(self.frame)
        self.assertEqual(len(to_events(decoded, None, self.schema)), 8)
        self.assertIn("Agent state", to_semantic(decoded, self.schema))

    @unittest.skipUnless(TRACES, "no real traces in ~/self-harness/traces")
    def test_real_trace_decoding(self):
        decoded = 0
        for f in TRACES[:10]:
            trace = json.loads(f.read_text(encoding="utf-8", errors="replace"))
            frame = trace.get("pm1", "")
            if not frame or len(frame) % 8 != 0:
                continue
            self.legacy.decode(frame)
            decoded += 1
        self.assertGreaterEqual(decoded, 1)

    @unittest.skipUnless(TRACES, "no real traces in ~/self-harness/traces")
    def test_all_four_modes_no_crash_on_real_traces(self):
        for f in TRACES[:5]:
            trace = json.loads(f.read_text(encoding="utf-8", errors="replace"))
            frame = trace.get("pm1", "")
            if not frame or len(frame) % 8 != 0:
                continue
            self.assertTrue(self.legacy.to_english(frame))
            self.assertIsInstance(self.legacy.to_json(frame), dict)
            self.assertTrue(self.legacy.to_semantic(frame))
            self.assertIsInstance(self.legacy.to_events(frame), list)


class OptionalImportTests(unittest.TestCase):
    """The rest of PM-Adapter v2 works without pro-memoria; only frame decoding needs it."""

    def run_without_pm1(self, code):
        pre = "import sys; sys.modules['pro_memoria'] = None; sys.path.insert(0, %r); " % str(common.ROOT)
        return subprocess.run([sys.executable, "-I", "-c", pre + code], capture_output=True, text=True, timeout=120)

    def test_v2_works_without_the_package(self):
        r = self.run_without_pm1(
            "import pm_adapter as pa; a = pa.Adapter(pa.load_schema('vault_demo')); "
            "e = a.store_confirmed('mod', 'return x % 13', evidence='t', source='s'); "
            "print(a.retrieve('mod').entries[0].text, a.assemble('t', hop=1).task_spec)")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "return x % 13 task-spec-v1"), r.stderr)

    def test_frame_calls_raise_a_clear_error_without_it(self):
        r = self.run_without_pm1(
            "import pm_adapter as pa\n"
            "a = pa.Adapter(pa.load_schema('default'))\n"
            "try:\n    a.to_json('.-.-')\nexcept pa.LegacyUnavailable as e:\n    print('LegacyUnavailable', 'pro-memoria' in str(e))\n")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "LegacyUnavailable True"), r.stderr)

    def test_the_error_is_both_a_pm_adapter_error_and_an_import_error(self):
        self.assertTrue(issubclass(LegacyUnavailable, ImportError))


if __name__ == "__main__":
    unittest.main()
