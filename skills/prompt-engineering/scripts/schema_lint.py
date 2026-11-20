#!/usr/bin/env python3
"""Lint a JSON Schema (or Pydantic model) for structured-output pitfalls (prompt-engineering skill).

Catches the schema shapes that make LLM structured outputs unreliable or that strict
provider modes reject: deep nesting, missing field descriptions, open objects,
non-required fields under strict mode, huge enums or unions, unbounded arrays, and
verdict-before-reasoning field order.

Usage
  python schema_lint.py schema.json
  python schema_lint.py --model myapp.schemas:ClarityDecision      # needs pydantic importable
  python schema_lint.py schema.json --strict                       # OpenAI-style strict checks
  python schema_lint.py schema.json --fail-on warn                 # CI: warnings fail too

Exit code 1 when findings at or above --fail-on (default: error) exist.
Standard library only (Pydantic optional for --model); Python 3.10+.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

MAX_DEPTH = 3
MAX_PROPS = 20
MAX_ENUM = 50
MAX_UNION = 4
REASONING_NAMES = {"reasoning", "rationale", "explanation", "analysis", "thoughts", "critique"}
VERDICT_NAMES = {"verdict", "label", "answer", "decision", "score", "status", "result", "winner"}
LEVELS = {"info": 0, "warn": 1, "error": 2}


class Linter:
    def __init__(self, root: dict[str, Any], strict: bool) -> None:
        self.root = root
        self.strict = strict
        self.findings: list[tuple[str, str, str]] = []

    def add(self, level: str, path: str, msg: str) -> None:
        self.findings.append((level, path or "$", msg))

    def resolve(self, node: dict[str, Any]) -> dict[str, Any]:
        ref = node.get("$ref")
        if not ref or not ref.startswith("#/"):
            return node
        target: Any = self.root
        for part in ref[2:].split("/"):
            target = target.get(part, {})
        return target

    def walk(self, node: dict[str, Any], path: str, depth: int, seen: frozenset[str]) -> None:
        ref = node.get("$ref")
        if ref:
            if ref in seen:
                self.add(
                    "warn",
                    path,
                    f"recursive $ref {ref}; most providers reject or truncate recursion",
                )
                return
            seen = seen | {ref}
        node = self.resolve(node)

        for key in ("anyOf", "oneOf"):
            branches = node.get(key, [])
            non_null = [b for b in branches if self.resolve(b).get("type") != "null"]
            if len(non_null) > MAX_UNION:
                self.add(
                    "warn", path, f"{key} with {len(non_null)} branches; models pick wrong branches"
                )
            for i, b in enumerate(branches):
                self.walk(b, f"{path}.{key}[{i}]", depth, seen)
        if node.get("oneOf") and self.strict:
            self.add("warn", path, "oneOf is unsupported in some strict modes; prefer anyOf")

        enum = node.get("enum")
        if enum is not None and len(enum) > MAX_ENUM:
            self.add("warn", path, f"enum with {len(enum)} values; consider a lookup step instead")

        t = node.get("type")
        types = t if isinstance(t, list) else [t]
        if "object" in types:
            self.check_object(node, path, depth, seen)
        if "array" in types:
            if "maxItems" not in node:
                self.add("info", path, "array without maxItems; outputs can run long")
            items = node.get("items")
            if isinstance(items, dict):
                self.walk(items, f"{path}[]", depth + 1, seen)
            else:
                self.add("warn", path, "array without an 'items' schema")

    def check_object(
        self, node: dict[str, Any], path: str, depth: int, seen: frozenset[str]
    ) -> None:
        if depth > MAX_DEPTH:
            self.add("warn", path, f"nesting depth {depth} > {MAX_DEPTH}; flatten for reliability")
        props: dict[str, Any] = node.get("properties", {})
        if not props:
            if (
                node.get("additionalProperties") not in (False, None)
                or "additionalProperties" not in node
            ):
                self.add(
                    "warn", path, "free-form object (no properties); the model can return anything"
                )
            return
        if len(props) > MAX_PROPS:
            self.add("warn", path, f"{len(props)} properties; split into smaller calls")
        if self.strict:
            if node.get("additionalProperties") is not False:
                self.add("error", path, "strict mode requires additionalProperties: false")
            missing = [k for k in props if k not in node.get("required", [])]
            if missing:
                self.add(
                    "error",
                    path,
                    f"strict mode requires every property in 'required': {missing} "
                    "(make optional fields nullable instead)",
                )
        names = list(props)
        lowered = [n.lower() for n in names]
        r_idx = next((i for i, n in enumerate(lowered) if n in REASONING_NAMES), None)
        v_idx = next((i for i, n in enumerate(lowered) if n in VERDICT_NAMES), None)
        if r_idx is not None and v_idx is not None and v_idx < r_idx:
            self.add(
                "warn",
                path,
                f"'{names[v_idx]}' comes before '{names[r_idx]}'; "
                "put reasoning first so the model commits to evidence before the label",
            )
        for name, sub in props.items():
            sub_path = f"{path}.{name}" if path else name
            resolved = self.resolve(sub)
            if not (sub.get("description") or resolved.get("description")):
                self.add(
                    "info" if resolved.get("enum") else "warn",
                    sub_path,
                    "missing description; descriptions are part of the prompt",
                )
            if len(name) <= 2:
                self.add("warn", sub_path, "non-descriptive field name; names steer the model too")
            self.walk(sub, sub_path, depth + 1, seen)


def load_schema(args: argparse.Namespace) -> dict[str, Any]:
    if args.model:
        sys.path.insert(0, str(Path.cwd()))
        module, _, attr = args.model.partition(":")
        model = getattr(importlib.import_module(module), attr)
        return model.model_json_schema()
    return json.loads(Path(args.schema).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("schema", nargs="?", help="path to a JSON Schema file")
    p.add_argument("--model", help="Pydantic model as 'pkg.module:Model'")
    p.add_argument("--strict", action="store_true", help="apply strict structured-output rules")
    p.add_argument("--fail-on", choices=list(LEVELS), default="error")
    args = p.parse_args(argv)
    if not args.schema and not args.model:
        p.error("pass a schema file or --model")

    schema = load_schema(args)
    linter = Linter(schema, strict=args.strict)
    if schema.get("type") != "object" and "$ref" not in schema:
        linter.add("error", "$", "root must be an object schema (wrap lists/scalars in an object)")
    linter.walk(schema, "", 1, frozenset())

    for level, path, msg in sorted(linter.findings, key=lambda f: -LEVELS[f[0]]):
        print(f"{level.upper():<5} {path}: {msg}")
    counts = {lvl: sum(f[0] == lvl for f in linter.findings) for lvl in LEVELS}
    print(f"\n{counts['error']} errors, {counts['warn']} warnings, {counts['info']} info")
    threshold = LEVELS[args.fail_on]
    return 1 if any(LEVELS[f[0]] >= threshold for f in linter.findings) else 0


if __name__ == "__main__":
    sys.exit(main())
