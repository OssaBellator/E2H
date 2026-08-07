from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPENDABOT = ROOT / ".github" / "dependabot.yml"


def _config() -> dict[str, Any]:
    parsed = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def test_dependabot_uses_uv_for_locked_python_dependencies() -> None:
    config = _config()
    assert config["version"] == 2
    updates = config["updates"]
    assert isinstance(updates, list)

    uv = next(item for item in updates if item["package-ecosystem"] == "uv")
    assert uv["directory"] == "/"
    assert uv["schedule"] == {"interval": "weekly"}
    assert uv["open-pull-requests-limit"] == 5
    assert uv["groups"] == {
        "python-dependencies": {
            "patterns": ["*"],
        }
    }
    assert not any(item["package-ecosystem"] == "pip" for item in updates)


def test_dependabot_updates_actions_but_holds_untagged_setup_node_pin() -> None:
    updates = _config()["updates"]
    actions = next(item for item in updates if item["package-ecosystem"] == "github-actions")
    assert actions["directory"] == "/"
    assert actions["schedule"] == {"interval": "weekly"}
    assert actions["open-pull-requests-limit"] == 5
    assert actions["groups"] == {
        "github-actions": {
            "patterns": ["*"],
        }
    }
    assert actions["ignore"] == [{"dependency-name": "actions/setup-node"}]


def test_dependabot_has_exactly_the_two_reviewed_update_streams() -> None:
    updates = _config()["updates"]
    assert [item["package-ecosystem"] for item in updates] == ["uv", "github-actions"]
