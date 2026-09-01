"""U10: ENVELOPE_SCHEMA is the allowed envelope shape.

Authored from harness-research.md §4 and D-ENV-1: one place, the nine
keys, commit optional, verdict nullable. Owner 2026-08-30: pin the
allowed shape (types, required keys, nullable verdict, optional
commit) rather than enumerating denials. Extra-key and missing-key
remain the two CUE-doctrine negatives the brief named.

The module already owns the nine keys; the export does not exist at
HEAD. Implementation (envelope.py internals, launch.py) was not read
beyond the existing test import of FIELDS / build / invalid.
"""

from __future__ import annotations

import unittest

import support

envelope = support.load("envelope")

NINE = (
    "job", "status", "verdict", "counts", "findings",
    "artifacts", "spend", "stamp", "note",
)
STATUS_ENUM = {"ok", "invalid", "tripped"}
VERDICT_ENUM = {"approve", "changes", None}
COUNT_KEYS = ("p1", "p2", "p3", "opinions")
STAMP = {"ref": "f0b8bb3", "started": "2026-08-22T12:00:00Z",
         "ended": "2026-08-22T12:04:00Z"}


def _schema():
    return getattr(envelope, "ENVELOPE_SCHEMA", None)


def _type_list(node):
    if not isinstance(node, dict):
        return []
    raw = node.get("type")
    if raw is None:
        types = []
    elif isinstance(raw, list):
        types = list(raw)
    else:
        types = [raw]
    for alt in node.get("anyOf") or node.get("oneOf") or ():
        types.extend(_type_list(alt))
    return types


def _allows_null(node):
    if node is True:
        return True
    types = _type_list(node)
    if "null" in types:
        return True
    enum = node.get("enum") if isinstance(node, dict) else None
    return enum is not None and None in enum


def _prop(schema, key):
    props = (schema or {}).get("properties") or {}
    return props.get(key)


def _value_matches(types, value):
    if not types:
        return False
    if value is None:
        return "null" in types
    if isinstance(value, bool):
        return "boolean" in types
    mapping = (
        (str, "string"),
        (int, "integer"),
        (float, "number"),
        (list, "array"),
        (dict, "object"),
    )
    for py, name in mapping:
        if isinstance(value, py):
            if name == "integer" and "number" in types:
                return True
            return name in types
    return False


def _instance_matches_allowed(schema, instance):
    """Does this instance sit inside the schema's declared allowed shape?

    Reads property types. A types-free or wrong-typed schema cannot
    accept a real envelope — that is the mutant the skeptic ran.
    """
    if not isinstance(schema, dict):
        return False, "ENVELOPE_SCHEMA is not an object"
    if schema.get("type") not in (None, "object"):
        return False, f"schema type is {schema.get('type')!r}, not object"
    if not isinstance(instance, dict):
        return False, "instance is not an object"
    required = schema.get("required") or []
    missing = [k for k in required if k not in instance]
    if missing:
        return False, f"missing required {missing}"
    props = schema.get("properties") or {}
    extra = [k for k in instance if k not in props]
    if extra and schema.get("additionalProperties") is False:
        return False, f"extra keys {extra}"
    for key in required:
        node = props.get(key)
        types = _type_list(node) if isinstance(node, dict) else []
        if not types:
            return False, f"required {key!r} has no type in the schema"
        if key in instance and not _value_matches(types, instance[key]):
            return (
                False,
                f"{key} value {type(instance[key]).__name__} not in {types}",
            )
    commit_node = props.get("commit")
    if "commit" in instance:
        types = _type_list(commit_node) if isinstance(commit_node, dict) else []
        if not types or not _value_matches(types, instance["commit"]):
            return False, "optional commit is present but not typed as object"
    return True, ""


class EnvelopeSchemaIsExported(unittest.TestCase):
    def test_envelope_schema_is_exported_from_one_place(self):
        """Scenario: ENVELOPE_SCHEMA is exported from envelope.py with the nine keys"""
        self.assertTrue(
            hasattr(envelope, "ENVELOPE_SCHEMA"),
            "envelope.py must export ENVELOPE_SCHEMA (JSON Schema)",
        )
        schema = envelope.ENVELOPE_SCHEMA
        self.assertIsInstance(schema, dict)
        self.assertEqual(schema.get("type"), "object")

    def test_schema_requires_the_nine_keys(self):
        """Scenario: ENVELOPE_SCHEMA is exported from envelope.py with the nine keys"""
        schema = _schema()
        self.assertIsInstance(
            schema, dict,
            "ENVELOPE_SCHEMA must be exported as a JSON Schema object",
        )
        required = schema.get("required")
        self.assertIsInstance(required, list, f"required={required!r}")
        self.assertEqual(
            list(required), list(NINE),
            "required must be the nine envelope keys, in contract order",
        )
        self.assertEqual(list(envelope.FIELDS), list(NINE))

    def test_commit_is_optional(self):
        """Scenario: ENVELOPE_SCHEMA is exported from envelope.py with the nine keys"""
        schema = _schema()
        self.assertIsInstance(
            schema, dict,
            "ENVELOPE_SCHEMA must be exported as a JSON Schema object",
        )
        required = schema.get("required") or []
        self.assertIn(
            "job", required,
            "commit-optional is only meaningful on a schema that requires "
            f"the nine keys; required={required!r}",
        )
        self.assertNotIn("commit", required)
        props = schema.get("properties") or {}
        self.assertIn(
            "commit", props,
            "commit is a known optional property, not an extra key",
        )
        self.assertIn(
            "object", _type_list(props["commit"]),
            f"optional commit must be an object, got {props['commit']!r}",
        )

    def test_verdict_is_nullable(self):
        """Scenario: ENVELOPE_SCHEMA is exported from envelope.py with the nine keys"""
        schema = _schema()
        self.assertIsInstance(
            schema, dict,
            "ENVELOPE_SCHEMA must be exported as a JSON Schema object",
        )
        props = schema.get("properties") or {}
        self.assertIn("verdict", props)
        self.assertTrue(
            _allows_null(props["verdict"]),
            f"verdict must allow null, got {props['verdict']!r}",
        )


class EnvelopeSchemaPinsTheAllowedShape(unittest.TestCase):
    def test_schema_pins_the_allowed_property_types(self):
        """Scenario: ENVELOPE_SCHEMA pins the allowed property types"""
        schema = _schema()
        self.assertIsInstance(
            schema, dict,
            "ENVELOPE_SCHEMA must be exported as a JSON Schema object",
        )
        props = schema.get("properties") or {}
        expected = {
            "job": "string",
            "status": "string",
            "counts": "object",
            "findings": "array",
            "artifacts": "object",
            "spend": "object",
            "stamp": "object",
            "note": "string",
        }
        for key, want in expected.items():
            self.assertIn(key, props, f"properties missing {key}")
            types = _type_list(props[key])
            self.assertIn(
                want, types,
                f"{key} must be typed {want}, got {props[key]!r}",
            )
        verdict_types = _type_list(props["verdict"])
        self.assertIn("string", verdict_types, props["verdict"])
        self.assertIn("null", verdict_types, props["verdict"])

    def test_status_enum_is_ok_invalid_tripped(self):
        """Scenario: ENVELOPE_SCHEMA pins the allowed property types"""
        schema = _schema()
        self.assertIsInstance(schema, dict, "ENVELOPE_SCHEMA must be exported")
        node = _prop(schema, "status")
        self.assertIsInstance(node, dict, f"status node={node!r}")
        enum = node.get("enum")
        self.assertIsInstance(enum, list, f"status.enum={enum!r}")
        self.assertEqual(set(enum), STATUS_ENUM, f"status.enum={enum!r}")

    def test_verdict_enum_is_approve_changes_null(self):
        """Scenario: ENVELOPE_SCHEMA pins the allowed property types"""
        schema = _schema()
        self.assertIsInstance(schema, dict, "ENVELOPE_SCHEMA must be exported")
        node = _prop(schema, "verdict")
        self.assertIsInstance(node, dict, f"verdict node={node!r}")
        enum = node.get("enum")
        self.assertIsInstance(enum, list, f"verdict.enum={enum!r}")
        self.assertEqual(set(enum), VERDICT_ENUM, f"verdict.enum={enum!r}")

    def test_counts_is_an_object_with_integer_tallies(self):
        """Scenario: ENVELOPE_SCHEMA pins the allowed property types"""
        schema = _schema()
        self.assertIsInstance(schema, dict, "ENVELOPE_SCHEMA must be exported")
        node = _prop(schema, "counts")
        self.assertIsInstance(node, dict, f"counts node={node!r}")
        self.assertIn("object", _type_list(node))
        cprops = node.get("properties") or {}
        for key in COUNT_KEYS:
            self.assertIn(key, cprops, f"counts.properties missing {key}")
            self.assertIn(
                "integer", _type_list(cprops[key]),
                f"counts.{key} must be integer, got {cprops[key]!r}",
            )

    def test_stamp_is_an_object_with_a_ref(self):
        """Scenario: ENVELOPE_SCHEMA pins the allowed property types"""
        schema = _schema()
        self.assertIsInstance(schema, dict, "ENVELOPE_SCHEMA must be exported")
        node = _prop(schema, "stamp")
        self.assertIsInstance(node, dict, f"stamp node={node!r}")
        self.assertIn("object", _type_list(node))
        sprops = node.get("properties") or {}
        self.assertIn("ref", sprops, f"stamp.properties={sprops!r}")
        self.assertIn(
            "string", _type_list(sprops["ref"]),
            f"stamp.ref must be string, got {sprops['ref']!r}",
        )
        required = node.get("required") or []
        self.assertIn("ref", required, f"stamp.required={required!r}")

    def test_findings_items_are_objects(self):
        """Scenario: ENVELOPE_SCHEMA pins the allowed property types"""
        schema = _schema()
        self.assertIsInstance(schema, dict, "ENVELOPE_SCHEMA must be exported")
        node = _prop(schema, "findings")
        self.assertIsInstance(node, dict, f"findings node={node!r}")
        items = node.get("items")
        self.assertIsInstance(items, dict, f"findings.items={items!r}")
        self.assertIn("object", _type_list(items), f"findings.items={items!r}")


class EnvelopeSchemaPositiveAndNegative(unittest.TestCase):
    def _complete(self):
        env = envelope.build(
            "verify", status="ok", verdict="approve", stamp=STAMP,
        )
        env = dict(env)
        env["commit"] = {"subject": "dispatch: pin U10", "body": "why"}
        return env

    def test_a_complete_envelope_including_commit_is_accepted(self):
        """Scenario: a complete envelope is a positive schema example"""
        schema = _schema()
        self.assertIsInstance(
            schema, dict,
            "ENVELOPE_SCHEMA must be exported as a JSON Schema object",
        )
        env = self._complete()
        self.assertIn("commit", env)
        self.assertEqual(list(k for k in env if k != "commit"), list(NINE))
        ok, why = _instance_matches_allowed(schema, env)
        self.assertTrue(ok, why)

    def test_a_null_verdict_on_invalid_is_accepted(self):
        """Scenario: a complete envelope is a positive schema example"""
        schema = _schema()
        self.assertIsInstance(
            schema, dict,
            "ENVELOPE_SCHEMA must be exported as a JSON Schema object",
        )
        env = envelope.invalid("verify", "could not look", stamp=STAMP)
        self.assertIsNone(env["verdict"], "plant: invalid carries no verdict")
        self.assertNotIn("commit", env, "plant: commit is absent")
        ok, why = _instance_matches_allowed(schema, env)
        self.assertTrue(ok, why)

    def test_an_extra_key_is_rejected(self):
        """Scenario: an extra key is a negative schema example"""
        schema = _schema()
        self.assertIsInstance(
            schema, dict,
            "ENVELOPE_SCHEMA must be exported as a JSON Schema object",
        )
        self.assertIs(
            schema.get("additionalProperties"), False,
            "extra keys are refused only when additionalProperties is false",
        )
        env = self._complete()
        env["transcript"] = "the whole conversation"
        self.assertIn("transcript", env, "plant: extra key landed")
        ok, why = _instance_matches_allowed(schema, env)
        self.assertFalse(
            ok,
            "an extra top-level key must fail the schema: "
            f"why={why!r} env_keys={list(env)}",
        )

    def test_a_missing_key_is_rejected(self):
        """Scenario: a missing key is a negative schema example"""
        schema = _schema()
        self.assertIsInstance(
            schema, dict,
            "ENVELOPE_SCHEMA must be exported as a JSON Schema object",
        )
        required = schema.get("required") or []
        self.assertIn(
            "note", required,
            "missing-note is only a negative if note is required",
        )
        env = self._complete()
        del env["note"]
        self.assertNotIn("note", env, "plant: note removed")
        ok, why = _instance_matches_allowed(schema, env)
        self.assertFalse(
            ok,
            "a missing required key must fail the schema: "
            f"why={why!r} env_keys={list(env)}",
        )


if __name__ == "__main__":
    unittest.main()
