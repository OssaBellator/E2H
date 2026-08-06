"""Strict JSON and YAML document loading helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys at every depth."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key ({key!r})",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key: {key!r}")
        result[key] = value
    return result


def load_mapping_document(
    path: Path,
    *,
    noun: str,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Load one UTF-8 JSON/YAML mapping with strict, unambiguous keys."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"unable to read {noun}: {exc}") from exc
    if max_bytes is not None and len(raw) > max_bytes:
        raise ValueError(f"{noun} exceeds {max_bytes} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{noun} must be UTF-8") from exc

    suffix = path.suffix.lower()
    if suffix not in {".json", ".yaml", ".yml"}:
        raise ValueError(f"{noun} must use .json, .yaml, or .yml")
    try:
        data: Any
        if suffix == ".json":
            data = json.loads(
                text,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
        else:
            data = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except (json.JSONDecodeError, yaml.YAMLError, ValueError) as exc:
        raise ValueError(f"invalid {noun} syntax: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{noun} root must be an object")
    return data
