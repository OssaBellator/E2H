"""Strict JSON and YAML document loading helpers."""

from __future__ import annotations

import json
import math
import os
import stat
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd


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


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def _requested_parent_identity(requested_parent: Path, *, noun: str) -> os.stat_result:
    try:
        current_parent = requested_parent.resolve(strict=True)
        return current_parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"unable to read {noun}: {exc}") from exc


def _validate_json_compatible(
    value: Any,
    *,
    path: str = "$",
    active: set[int] | None = None,
) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if active is None:
        active = set()
    if type(value) is list:
        identity = id(value)
        if identity in active:
            raise ValueError(f"{path} contains a recursive value")
        active.add(identity)
        try:
            for index, item in enumerate(value):
                _validate_json_compatible(item, path=f"{path}[{index}]", active=active)
        finally:
            active.remove(identity)
        return
    if type(value) is dict:
        identity = id(value)
        if identity in active:
            raise ValueError(f"{path} contains a recursive value")
        active.add(identity)
        try:
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError(f"{path} mapping keys must be strings")
                _validate_json_compatible(item, path=f"{path}[{key!r}]", active=active)
        finally:
            active.remove(identity)
        return
    raise ValueError(f"{path} contains unsupported value type {type(value).__name__}")


def _read_document_bytes(path: Path, *, noun: str, max_bytes: int | None) -> bytes:
    if "\x00" in os.fspath(path):
        raise ValueError(f"unable to read {noun}: path must not contain NUL")
    requested_parent = path.parent.absolute()
    try:
        parent = requested_parent.resolve(strict=True)
        parent_expected = parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"unable to read {noun}: {exc}") from exc
    if not stat.S_ISDIR(parent_expected.st_mode):
        raise ValueError(f"{noun} parent must be a directory")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_descriptor = os.open(parent, directory_flags)
    except OSError as exc:
        raise ValueError(f"unable to read {noun}: {exc}") from exc
    descriptor: int | None = None
    try:
        parent_opened = os.fstat(parent_descriptor)
        requested_opened = _requested_parent_identity(requested_parent, noun=noun)
        if (
            _stat_identity(parent_opened) != _stat_identity(parent_expected)
            or _stat_identity(requested_opened) != _stat_identity(parent_opened)
        ):
            raise ValueError(f"{noun} parent changed while opening")
        try:
            expected = (
                os.stat(
                    path.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if _STAT_SUPPORTS_DIR_FD
                else (parent / path.name).stat(follow_symlinks=False)
            )
        except OSError as exc:
            raise ValueError(f"unable to read {noun}: {exc}") from exc
        if stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode):
            raise ValueError(f"{noun} must be a regular file")
        if max_bytes is not None and expected.st_size > max_bytes:
            raise ValueError(f"{noun} exceeds {max_bytes} bytes")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = (
                os.open(path.name, flags, dir_fd=parent_descriptor)
                if _OPEN_SUPPORTS_DIR_FD
                else os.open(parent / path.name, flags)
            )
        except OSError as exc:
            raise ValueError(f"unable to read {noun}: {exc}") from exc
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError(f"{noun} must be a regular file")
        if _stat_identity(opened) != _stat_identity(expected):
            raise ValueError(f"{noun} changed while opening")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read() if max_bytes is None else handle.read(max_bytes + 1)
        after = os.fstat(descriptor)
        current = (
            os.stat(
                path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if _STAT_SUPPORTS_DIR_FD
            else (parent / path.name).stat(follow_symlinks=False)
        )
        parent_after = os.fstat(parent_descriptor)
        parent_current = _requested_parent_identity(requested_parent, noun=noun)
        if max_bytes is not None and len(raw) > max_bytes:
            raise ValueError(f"{noun} exceeds {max_bytes} bytes")
        if (
            _stat_identity(after) != _stat_identity(opened)
            or _stat_identity(current) != _stat_identity(opened)
            or len(raw) != opened.st_size
        ):
            raise ValueError(f"{noun} changed while reading")
        if _stat_identity(parent_after) != _stat_identity(parent_opened) or _stat_identity(
            parent_current
        ) != _stat_identity(parent_opened):
            raise ValueError(f"{noun} parent changed while reading")
        return raw
    except OSError as exc:
        raise ValueError(f"unable to read {noun}: {exc}") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(OSError):
            os.close(parent_descriptor)


def load_mapping_document(
    path: Path,
    *,
    noun: str,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Load one UTF-8 JSON/YAML mapping with strict, unambiguous keys."""
    raw = _read_document_bytes(path, noun=noun, max_bytes=max_bytes)
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
    try:
        _validate_json_compatible(data)
    except ValueError as exc:
        raise ValueError(f"{noun} must contain JSON-compatible values: {exc}") from exc
    return cast(dict[str, Any], data)
