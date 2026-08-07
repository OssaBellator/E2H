from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PUBLIC_DOCS = [
    README,
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    ROOT / "ROADMAP.md",
    ROOT / "LICENSE",
]
_LOCAL_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _relative_markdown_targets(path: Path) -> list[Path]:
    targets: list[Path] = []
    text = path.read_text(encoding="utf-8")
    for raw_target in _LOCAL_LINK.findall(text):
        target = raw_target.strip()
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1]
        target = target.split("#", 1)[0]
        if not target:
            continue
        assert not target.startswith("/"), f"absolute local docs link in {path}: {target}"
        targets.append(path.parent / target)
    return targets


def test_public_repository_policy_files_exist_and_local_links_resolve() -> None:
    for path in PUBLIC_DOCS:
        assert path.is_file(), path

    for source in (README, ROOT / "CONTRIBUTING.md", ROOT / "SECURITY.md"):
        for target in _relative_markdown_targets(source):
            assert target.exists(), f"broken local link in {source}: {target}"


def test_readme_represents_current_capability_layers() -> None:
    text = README.read_text(encoding="utf-8")
    for heading in (
        "## Controlled optimization",
        "## Frontier integrations",
        "## Community benchmark",
        "## Release and supply-chain integrity",
        "## Security boundaries",
    ):
        assert heading in text
    assert "twelve connected vertical slices" not in text
    assert "e2h-mcp --help" in text
    assert "e2h-a2a --help" in text
    assert "benchmark long-horizon" in text
    assert "benchmark environments verify" in text


def test_package_metadata_points_to_public_repository_surfaces() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    assert project["readme"] == "README.md"
    assert project["requires-python"] == ">=3.11"
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert set(project["keywords"]) >= {
        "ai-agents",
        "benchmarking",
        "evaluation",
        "reproducibility",
    }

    classifiers = set(project["classifiers"])
    for version in ("3.11", "3.12", "3.13"):
        assert f"Programming Language :: Python :: {version}" in classifiers
    assert not any(classifier.startswith("License ::") for classifier in classifiers)

    assert project["urls"] == {
        "Homepage": "https://github.com/OssaBellator/E2H",
        "Repository": "https://github.com/OssaBellator/E2H",
        "Issues": "https://github.com/OssaBellator/E2H/issues",
        "Documentation": "https://github.com/OssaBellator/E2H/tree/main/docs",
        "Roadmap": "https://github.com/OssaBellator/E2H/blob/main/ROADMAP.md",
    }
