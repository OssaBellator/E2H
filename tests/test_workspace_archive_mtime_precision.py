from __future__ import annotations

import os
import tarfile
from pathlib import Path

import pytest

import e2h.workspace_archive as workspace_archive
from e2h.workspace_archive import sealed_workspace_archive_supported, stable_workspace_archive

pytestmark = pytest.mark.skipif(
    not sealed_workspace_archive_supported(),
    reason="sealed workspace archive capture is unavailable",
)


def _expected_pax_timestamp(value_ns: int) -> str:
    sign = "-" if value_ns < 0 else ""
    seconds, nanoseconds = divmod(abs(value_ns), 1_000_000_000)
    if nanoseconds == 0:
        return f"{sign}{seconds}"
    fraction = f"{nanoseconds:09d}".rstrip("0")
    return f"{sign}{seconds}.{fraction}"


@pytest.mark.parametrize(
    ("value_ns", "expected"),
    [
        (0, "0"),
        (1, "0.000000001"),
        (1_700_000_000_123_456_789, "1700000000.123456789"),
        (-500_000_000, "-0.5"),
        (-1_500_000_000, "-1.5"),
    ],
)
def test_pax_timestamp_formatter_preserves_integer_nanoseconds(
    value_ns: int,
    expected: str,
) -> None:
    assert workspace_archive._pax_timestamp_ns(value_ns) == expected


def test_sealed_archive_emits_exact_file_mtime_pax_record(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = workspace / "marker.txt"
    marker.write_text("trusted", encoding="utf-8")
    requested_ns = 1_700_000_000_123_456_789
    os.utime(marker, ns=(requested_ns, requested_ns))
    observed_ns = marker.stat(follow_symlinks=False).st_mtime_ns
    if observed_ns % 1_000_000_000 == 0:
        pytest.skip("test filesystem rounds mtimes to whole seconds")

    with stable_workspace_archive(
        workspace,
        max_bytes=1024,
        max_entries=10,
    ) as captured:
        with tarfile.open(fileobj=captured.file, mode="r:") as archive:
            member = archive.getmember("marker.txt")
            assert member.pax_headers["mtime"] == _expected_pax_timestamp(observed_ns)
