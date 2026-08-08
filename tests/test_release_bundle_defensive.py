from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import e2h.release_bundle as release_bundle
from e2h.release_bundle import ReleaseBundleError


def test_read_regular_bytes_rejects_missing_nonregular_empty_and_oversize(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(ReleaseBundleError, match="unable to open fixture"):
        release_bundle._read_regular_bytes(missing, limit=8, noun="fixture")

    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ReleaseBundleError, match="fixture must be a regular file"):
        release_bundle._read_regular_bytes(directory, limit=8, noun="fixture")

    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    with pytest.raises(ReleaseBundleError, match="fixture must not be empty"):
        release_bundle._read_regular_bytes(empty, limit=8, noun="fixture")

    oversize = tmp_path / "oversize"
    oversize.write_bytes(b"ab")
    with pytest.raises(ReleaseBundleError, match="fixture exceeds 1 bytes"):
        release_bundle._read_regular_bytes(oversize, limit=1, noun="fixture")


def test_copy_verified_file_rejects_bad_sources_destination_and_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "copy"
    with pytest.raises(ReleaseBundleError, match="unable to open fixture"):
        release_bundle._copy_verified_file(
            tmp_path / "missing",
            destination,
            expected_digest="0" * 64,
            noun="fixture",
        )

    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ReleaseBundleError, match="fixture must be a regular file"):
        release_bundle._copy_verified_file(
            directory,
            destination,
            expected_digest="0" * 64,
            noun="fixture",
        )

    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    with pytest.raises(ReleaseBundleError, match="fixture must not be empty"):
        release_bundle._copy_verified_file(
            empty,
            destination,
            expected_digest=hashlib.sha256(b"").hexdigest(),
            noun="fixture",
        )

    source = tmp_path / "source"
    source.write_bytes(b"ok")
    digest = hashlib.sha256(b"ok").hexdigest()
    destination.write_bytes(b"occupied")
    with pytest.raises(ReleaseBundleError, match="unable to stage fixture"):
        release_bundle._copy_verified_file(
            source,
            destination,
            expected_digest=digest,
            noun="fixture",
        )

    monkeypatch.setattr(release_bundle, "_MAX_BUNDLE_FILE_BYTES", 1)
    with pytest.raises(ReleaseBundleError, match="fixture exceeds 1 bytes"):
        release_bundle._copy_verified_file(
            source,
            tmp_path / "oversize-copy",
            expected_digest=digest,
            noun="fixture",
        )


def test_checksum_path_and_parser_reject_malformed_inputs(tmp_path: Path) -> None:
    for unsafe in ("../escape", "/absolute", "dist\\wheel.whl"):
        with pytest.raises(ReleaseBundleError, match="unsafe checksum path"):
            release_bundle._safe_checksum_path(unsafe)

    invalid_utf8 = tmp_path / "invalid-utf8"
    invalid_utf8.write_bytes(b"\xff")
    with pytest.raises(ReleaseBundleError, match="must be UTF-8"):
        release_bundle._parse_checksums(invalid_utf8)

    invalid_line = tmp_path / "invalid-line"
    invalid_line.write_text("not a checksum\n", encoding="utf-8")
    with pytest.raises(ReleaseBundleError, match="invalid release checksum line"):
        release_bundle._parse_checksums(invalid_line)

    too_many = tmp_path / "too-many"
    too_many.write_text(
        "".join(f"{'a' * 64}  dist/file-{index}.whl\n" for index in range(65)),
        encoding="utf-8",
    )
    with pytest.raises(ReleaseBundleError, match="too many entries"):
        release_bundle._parse_checksums(too_many)


def test_checksum_shape_rejects_wrong_distribution_sets() -> None:
    static = set(release_bundle._STATIC_CHECKSUM_PATHS)

    with pytest.raises(ReleaseBundleError, match="exactly two distributions"):
        release_bundle._validate_checksum_shape(
            static | {"dist/a.whl", "dist/a.tar.gz", "dist/extra.txt"}
        )

    with pytest.raises(ReleaseBundleError, match="exactly one wheel"):
        release_bundle._validate_checksum_shape(static | {"dist/a.tar.gz", "dist/b.tar.gz"})

    with pytest.raises(ReleaseBundleError, match="exactly one sdist"):
        release_bundle._validate_checksum_shape(static | {"dist/a.whl", "dist/extra.txt"})


def test_json_loader_rejects_invalid_encoding_syntax_constants_and_roots(tmp_path: Path) -> None:
    invalid_utf8 = tmp_path / "invalid-utf8.json"
    invalid_utf8.write_bytes(b"\xff")
    with pytest.raises(ReleaseBundleError, match="fixture must be UTF-8"):
        release_bundle._load_json_object(invalid_utf8, noun="fixture")

    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{", encoding="utf-8")
    with pytest.raises(ReleaseBundleError, match="invalid fixture"):
        release_bundle._load_json_object(invalid_json, noun="fixture")

    nonstandard = tmp_path / "nonstandard.json"
    nonstandard.write_text('{"value": NaN}', encoding="utf-8")
    with pytest.raises(ReleaseBundleError, match="non-standard JSON constant"):
        release_bundle._load_json_object(nonstandard, noun="fixture")

    array_root = tmp_path / "array.json"
    array_root.write_text("[]", encoding="utf-8")
    with pytest.raises(ReleaseBundleError, match="fixture root must be an object"):
        release_bundle._load_json_object(array_root, noun="fixture")


def test_layout_helpers_reject_invalid_dist_shapes(tmp_path: Path) -> None:
    bundle_file = tmp_path / "bundle-file"
    bundle_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ReleaseBundleError, match="release bundle must be a real directory"):
        release_bundle._verify_bundle_layout(bundle_file)

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in release_bundle._STATIC_CHECKSUM_PATHS | {"release-checksums.txt"}:
        (bundle / name).write_text("fixture", encoding="utf-8")
    (bundle / "dist").write_text("not a directory", encoding="utf-8")
    with pytest.raises(ReleaseBundleError, match="release bundle dist must be a real directory"):
        release_bundle._verify_bundle_layout(bundle)

    missing_dist = tmp_path / "missing-dist"
    missing_dist.mkdir()
    with pytest.raises(ReleaseBundleError, match="unable to inspect release dist directory"):
        release_bundle._verify_distribution_layout(missing_dist, {"dist/package.whl": "0" * 64})

    mismatched = tmp_path / "mismatched"
    (mismatched / "dist").mkdir(parents=True)
    (mismatched / "dist" / "extra.whl").write_text("fixture", encoding="utf-8")
    with pytest.raises(ReleaseBundleError, match="release dist layout does not match checksums"):
        release_bundle._verify_distribution_layout(
            mismatched,
            {"dist/package.whl": "0" * 64},
        )

    symlinked = tmp_path / "symlinked"
    (symlinked / "dist").mkdir(parents=True)
    target = tmp_path / "target.whl"
    target.write_text("fixture", encoding="utf-8")
    (symlinked / "dist" / "package.whl").symlink_to(target)
    with pytest.raises(ReleaseBundleError, match="release distribution must be a regular file"):
        release_bundle._verify_distribution_layout(
            symlinked,
            {"dist/package.whl": "0" * 64},
        )


def test_stage_verified_bundle_wraps_checksum_manifest_write_errors(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "release-checksums.txt").mkdir()
    with pytest.raises(ReleaseBundleError, match="unable to stage release checksum manifest"):
        release_bundle._stage_verified_bundle(tmp_path, staging, {}, b"checksums")
