"""R1 and R2: the schema lists every field with type, vocabulary and role; validation rejects, never repairs."""
import unittest

import common
from pm_adapter import Adapter, PMAdapterError, Schema, SchemaError, ValidationError, load_schema


class SchemaTests(unittest.TestCase):
    def test_demo_schema_has_type_vocabulary_and_role_for_every_field(self):
        s = Schema.from_dict(load_schema("vault_demo"))
        self.assertEqual([f.name for f in s.fields], ["phase", "outcome", "freeze_active", "log_tail", "task_spec"])
        for f in s.fields:
            self.assertIn(f.type, ("enum", "int", "bool", "str"))
            self.assertIn(f.role, ("decision", "transient", "invariant"))
        self.assertEqual(s.by_name["phase"].values, ("idle", "think", "act", "review"))
        self.assertEqual(s.by_name["freeze_active"].derived, "freeze_active")

    def test_malformed_schemas_raise_schema_error(self):
        base = load_schema("vault_demo")

        def bad(mutate):
            d = {k: (list(v) if isinstance(v, list) else v) for k, v in base.items()}
            d["fields"] = [dict(f) for f in base["fields"]]
            mutate(d)
            with self.assertRaises(SchemaError):
                Schema.from_dict(d)
        bad(lambda d: d["fields"][0].pop("role"))                         # a role is required
        bad(lambda d: d["fields"][0].update(role="boss"))
        bad(lambda d: d["fields"][0].update(type="float"))
        bad(lambda d: d["fields"][0].update(values=[]))                   # an enum needs a vocabulary
        bad(lambda d: d["fields"][3].update(values=["a"]))                # only enums carry values
        bad(lambda d: d["fields"].append(dict(d["fields"][0])))           # duplicate field
        bad(lambda d: d["fields"][4].pop("const"))                        # an invariant needs a const
        bad(lambda d: d["fields"][1].update(derived="x", role="transient"))   # only decision fields can be derived
        bad(lambda d: d.update(keys=["a", "a"]))
        bad(lambda d: d.update(keys=["none"]))                            # reserved: "none" is the no-rule answer
        with self.assertRaises(SchemaError):
            Schema.from_dict({"fields": []})

    def test_derived_name_must_be_registered(self):
        d = load_schema("vault_demo")
        d["fields"] = [dict(f) for f in d["fields"]]
        d["fields"][2]["derived"] = "no_such_function"
        with self.assertRaises(SchemaError):
            Adapter(d)

    def test_legacy_schema_builds_an_adapter_but_v2_calls_say_so(self):
        a = Adapter(load_schema("default"))
        with self.assertRaises(PMAdapterError):
            a.init_state({"phase": "idle"}, source="x")
        with self.assertRaises(TypeError):
            Adapter(None)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.a = common.demo()

    def test_valid_values_are_logged(self):
        evs = self.a.init_state({"phase": "act", "outcome": "running"}, source="agent")
        self.assertEqual([e["type"] for e in evs], ["state_init", "state_init"])

    def test_unknown_field_is_rejected_and_nothing_is_logged(self):
        with self.assertRaises(ValidationError) as cm:
            self.a.init_state({"phase": "act", "mood": "good"}, source="agent")
        self.assertEqual(cm.exception.field, "mood")
        self.assertEqual(self.a.events(), [])          # the valid half was not written either

    def test_value_outside_the_vocabulary_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.a.change_state("phase", "sleeping", source="agent")

    def test_no_repair_no_coercion(self):
        for field, value in (("phase", "Act"), ("phase", " act"), ("phase", 2), ("phase", None), ("log_tail", 5)):
            with self.assertRaises(ValidationError, msg=repr((field, value))):
                self.a.change_state(field, value, source="agent")
        self.assertEqual(self.a.events(), [])

    def test_int_and_bool_types_are_strict(self):
        d = common.schema_with_keys(["k"])
        d["fields"] = d["fields"] + [{"name": "n", "type": "int", "role": "transient"}, {"name": "b", "type": "bool", "role": "transient"}]
        a = Adapter(d)
        a.change_state("n", 3, source="x")
        a.change_state("b", True, source="x")
        for field, value in (("n", "3"), ("n", True), ("n", 3.0), ("b", 1), ("b", "true")):
            with self.assertRaises(ValidationError, msg=repr((field, value))):
                a.change_state(field, value, source="x")

    def test_derived_and_invariant_fields_cannot_be_written(self):
        with self.assertRaises(ValidationError):
            self.a.change_state("freeze_active", True, source="agent")          # the adapter derives it
        with self.assertRaises(ValidationError):
            self.a.change_state("task_spec", "task-spec-v2", source="agent")    # fixed by the schema

    def test_validate_fields_on_a_whole_mapping(self):
        s = Schema.from_dict(load_schema("vault_demo"))
        self.assertEqual(s.validate_fields({"phase": "idle"}), {"phase": "idle"})
        with self.assertRaises(ValidationError):
            s.validate_fields({"phase": "idle", "extra": 1})


if __name__ == "__main__":
    unittest.main()
