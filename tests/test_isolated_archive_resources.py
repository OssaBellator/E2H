from __future__ import annotations

import io

import pytest

from e2h.isolated_runner import (
    _max_remote_archive_bytes,
    _validate_remote_archive_resources,
)
from e2h.runner import RunnerError
from e2h.workspace_archive import WorkspaceArchive


def _archive(*, source_bytes: int, entries: int, archive_bytes: int) -> WorkspaceArchive:
    return WorkspaceArchive(
        file=io.BytesIO(b"archive"),
        directories=frozenset({"."}),
        source_bytes=source_bytes,
        entries=entries,
        archive_bytes=archive_bytes,
    )


def test_remote_archive_transfer_bound_scales_with_operator_limits() -> None:
    small = _max_remote_archive_bytes(1024, 10)
    assert small > 1024
    assert _max_remote_archive_bytes(2048, 10) > small
    assert _max_remote_archive_bytes(1024, 11) > small


@pytest.mark.parametrize(("max_bytes", "max_entries"), [(0, 1), (1, 0), (-1, 1), (1, -1)])
def test_remote_archive_transfer_bound_requires_positive_limits(
    max_bytes: int,
    max_entries: int,
) -> None:
    with pytest.raises(RunnerError, match="limits must be positive"):
        _max_remote_archive_bytes(max_bytes, max_entries)


def test_remote_archive_resources_reject_capture_metadata_over_limit() -> None:
    with pytest.raises(RunnerError, match="metadata exceeds configured capture limits"):
        _validate_remote_archive_resources(
            _archive(source_bytes=1025, entries=10, archive_bytes=2048),
            max_source_bytes=1024,
            max_entries=10,
        )

    with pytest.raises(RunnerError, match="metadata exceeds configured capture limits"):
        _validate_remote_archive_resources(
            _archive(source_bytes=1024, entries=11, archive_bytes=2048),
            max_source_bytes=1024,
            max_entries=10,
        )


def test_remote_archive_resources_reject_unexpected_tar_amplification() -> None:
    limit = _max_remote_archive_bytes(1024, 10)
    with pytest.raises(RunnerError, match="derived transfer bound"):
        _validate_remote_archive_resources(
            _archive(source_bytes=1024, entries=10, archive_bytes=limit + 1),
            max_source_bytes=1024,
            max_entries=10,
        )


def test_remote_archive_resources_accept_archive_within_derived_bound() -> None:
    limit = _max_remote_archive_bytes(1024, 10)
    _validate_remote_archive_resources(
        _archive(source_bytes=1024, entries=10, archive_bytes=limit),
        max_source_bytes=1024,
        max_entries=10,
    )
