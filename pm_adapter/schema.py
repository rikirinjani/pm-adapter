"""Packet schema (R1, R2): every field has a name, a type, a closed vocabulary where possible, and a role.

Roles
    decision   goes into the packet (the packet holds the derived or validated value)
    transient  validated and logged, never placed in the packet (unpublished discovery notes, T2: such a field changed 0.4 percent of decisions)
    invariant  fixed by the schema (``const``); lives in the task specification, which the packet only references

A schema is plain JSON. Bundled schemas (``load_schema``) include the original frame schemas used by ``pm_adapter.legacy``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .errors import SchemaError, ValidationError

ROLES = ("decision", "transient", "invariant")
TYPES = ("enum", "int", "bool", "str")
PROVENANCE = ("measured", "derived", "claimed")
EVIDENCE_MAX_LEN = 40
EVIDENCE_RULE = "a short token: 1 to 40 characters from letters, digits, dot, dash, underscore and colon (no spaces)"
_EVIDENCE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,40}$")
RESERVED_KEYS = ("none",)             # "none" is the match step's no-rule answer, so no store key may carry that name

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


def load_schema(name: str) -> dict:
    """Load a bundled JSON schema by name (e.g. "default" for the original frame schema, "vault_demo" for the v2 example)."""
    path = _SCHEMA_DIR / ("%s.json" % name)
    if not path.is_file():
        raise FileNotFoundError("schema not found: %r (%s)" % (name, path))
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def is_legacy_schema(schema) -> bool:
    """True for the original byte-frame schemas (``state_width`` and fields with a ``byte`` index)."""
    return isinstance(schema, dict) and "state_width" in schema and any("byte" in f for f in schema.get("fields", []))


def check_evidence(evidence, *, field="evidence"):
    """R3a: the evidence shown in a packet is an opaque id. Anything that is not a short token is refused (ValidationError).
    Descriptive text about the evidence belongs in the ``note`` argument, which is kept in the event log only."""
    if not isinstance(evidence, str) or not _EVIDENCE_RE.match(evidence):
        raise ValidationError(field, "evidence must be %s; got %r (put descriptive text in note=)" % (EVIDENCE_RULE, evidence))
    return evidence


class Field:
    __slots__ = ("name", "type", "values", "role", "derived", "const")

    def __init__(self, name, type_, values, role, derived=None, const=None):
        self.name, self.type, self.values, self.role, self.derived, self.const = name, type_, values, role, derived, const

    def check(self, value):
        """Return ``value`` if the schema allows it, else raise ValidationError. No coercion, no repair."""
        if self.type == "enum":
            if not isinstance(value, str) or value not in self.values:
                raise ValidationError(self.name, "value %r is not in the vocabulary %r" % (value, list(self.values)))
        elif self.type == "int":
            if type(value) is not int:
                raise ValidationError(self.name, "expected an int, got %r" % (value,))
        elif self.type == "bool":
            if type(value) is not bool:
                raise ValidationError(self.name, "expected a bool, got %r" % (value,))
        elif self.type == "str":
            if not isinstance(value, str):
                raise ValidationError(self.name, "expected a str, got %r" % (value,))
        return value


class Schema:
    """A validated v2 schema. ``keys`` is the closed vocabulary of store keys (R8)."""

    def __init__(self, name, fields, keys, task_spec_ref):
        self.name = name
        self.fields = fields                                  # ordered list of Field
        self.by_name = {f.name: f for f in fields}
        self.keys = tuple(keys)
        self.key_set = frozenset(keys)
        self.task_spec_ref = task_spec_ref

    @classmethod
    def from_dict(cls, d) -> "Schema":
        if not isinstance(d, dict) or not isinstance(d.get("fields"), list) or not d["fields"]:
            raise SchemaError("a schema needs a non-empty 'fields' list")
        fields, seen = [], set()
        for raw in d["fields"]:
            name = raw.get("name")
            if not isinstance(name, str) or not name:
                raise SchemaError("every field needs a name: %r" % (raw,))
            if name in seen:
                raise SchemaError("duplicate field %r" % name)
            seen.add(name)
            type_, role = raw.get("type"), raw.get("role")
            if type_ not in TYPES:
                raise SchemaError("field %r: type must be one of %s, got %r" % (name, TYPES, type_))
            if role not in ROLES:
                raise SchemaError("field %r: role must be one of %s, got %r" % (name, ROLES, role))
            values = raw.get("values")
            if type_ == "enum":
                if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values) or len(set(values)) != len(values):
                    raise SchemaError("field %r: an enum needs a non-empty list of distinct string values" % name)
                values = tuple(values)
            elif values is not None:
                raise SchemaError("field %r: only enum fields carry 'values'" % name)
            const = raw.get("const")
            if role == "invariant" and const is None:
                raise SchemaError("field %r: an invariant field needs a 'const'" % name)
            if const is not None and role != "invariant":
                raise SchemaError("field %r: 'const' belongs to invariant fields" % name)
            derived = raw.get("derived")
            if derived is not None and role != "decision":
                raise SchemaError("field %r: only decision fields can be derived" % name)
            f = Field(name, type_, values, role, derived, const)
            if const is not None:
                f.check(const)
            fields.append(f)
        keys = d.get("keys", [])
        if not isinstance(keys, list) or not all(isinstance(k, str) and k for k in keys) or len(set(keys)) != len(keys):
            raise SchemaError("'keys' must be a list of distinct non-empty strings")
        bad = [k for k in keys if k in RESERVED_KEYS]
        if bad:
            raise SchemaError("reserved store key(s): %s" % bad)
        ref = d.get("task_spec_ref")
        if ref is not None and not isinstance(ref, str):
            raise SchemaError("'task_spec_ref' must be a string")
        return cls(d.get("name", "unnamed"), fields, keys, ref)

    def validate_fields(self, values: dict, *, writable_only: bool = True) -> dict:
        """Check a {field: value} mapping (R2). Rejects unknown fields and values outside the vocabulary.

        Derived fields and invariant fields are computed or fixed by the adapter and cannot be written by a caller."""
        if not isinstance(values, dict):
            raise ValidationError("<packet>", "expected a mapping of field to value")
        out = {}
        for name, value in values.items():
            f = self.by_name.get(name)
            if f is None:
                raise ValidationError(name, "unknown field")
            if writable_only and f.derived is not None:
                raise ValidationError(name, "derived field: the adapter computes it from the event log")
            if writable_only and f.role == "invariant":
                raise ValidationError(name, "invariant field: fixed by the schema")
            out[name] = f.check(value)
        return out

    def check_key(self, key):
        if key not in self.key_set:
            raise ValidationError("key", "%r is not in the schema's closed key vocabulary" % (key,))
        return key
