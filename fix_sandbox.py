"""One-use lint corrections for the sandbox implementation."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"sandbox lint anchor mismatch in {path}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/e2h/sandbox.py",
    '''            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_CLEANUP_TIMEOUT_SECONDS,
''',
    '''            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_CLEANUP_TIMEOUT_SECONDS,
''',
)
replace_once(
    "tests/test_sandbox.py",
    "def test_auto_backend_runs_declared_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:\n",
    "def test_auto_backend_runs_declared_sandbox(\n"
    "    tmp_path: Path, monkeypatch: pytest.MonkeyPatch\n"
    ") -> None:\n",
)
replace_once(
    "tests/test_sandbox.py",
    '        success=SuccessSpec(commands=[CommandCheck(id="pass", argv=[sys.executable, "-c", "pass"])]),\n',
    "        success=SuccessSpec(\n"
    '            commands=[CommandCheck(id="pass", argv=[sys.executable, "-c", "pass"])]\n'
    "        ),\n",
)
replace_once(
    "tests/test_sandbox.py",
    '    with pytest.raises(RunnerError, match="requires capsule.sandbox"):\n',
    '    with pytest.raises(RunnerError, match=r"requires capsule\\.sandbox"):\n',
)
replace_once(
    "tests/test_sandbox.py",
    '    assert force_remove_container("docker", cidfile) == "container runtime wrote an invalid container ID"\n',
    "    assert force_remove_container(\n"
    '        "docker", cidfile\n'
    '    ) == "container runtime wrote an invalid container ID"\n',
)

Path(__file__).unlink()
