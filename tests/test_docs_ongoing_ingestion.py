"""The README presents ongoing session ingestion up front; CHANGELOG records it."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_readme_headline_section_near_the_top():
    lines = (REPO_ROOT / "README.md").read_text().splitlines()
    head = lines[:150]
    assert any(re.match(r"^##+ .*(ongoing|automatic).*session", l, re.I) for l in head)
    text = "\n".join(lines)
    for agent in ("claude", "pi", "opencode"):
        assert f"awiki hook install --agent {agent}" in text, agent


def test_changelog_names_the_four_headline_items():
    text = (REPO_ROOT / "CHANGELOG.md").read_text()
    section = text.split("## [0.9.0]", 1)[1].split("\n## [", 1)[0]
    for needle in ("pi adapter", "sync --detach", "hook install", "doctor"):
        assert needle.lower() in section.lower(), needle
