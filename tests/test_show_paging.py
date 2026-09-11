"""CLI tests for the sliced views of `awiki show`: --outline, --section,
--head and --tail."""

from click.testing import CliRunner

from agent_wiki.cli import cli
from agent_wiki.page import render_page


META = {
    "title": "Check Log", "topic": "research", "tags": ["ops"],
    "created": "2026-09-11", "updated": "2026-09-11", "sources": [],
}

BODY = """# Check Log

Preamble about the log.

## Overview

Some overview text.

### Scope

Scope text.

### Method

Method text.

## Entries

Entry intro.

### 2026-09-01

First entry.
"""


def _make_page(vault, name="research/log.md", body=BODY):
    page = render_page(META, body)
    (vault / name).write_text(page)
    return page


def test_outline_prints_only_heading_lines(tmp_config, tmp_vault):
    _make_page(tmp_vault)
    result = CliRunner().invoke(cli, ["show", "research/log.md", "--outline"])
    assert result.exit_code == 0
    assert result.stdout == (
        "# Check Log\n"
        "## Overview\n"
        "### Scope\n"
        "### Method\n"
        "## Entries\n"
        "### 2026-09-01\n"
    )


def test_show_without_flags_stays_verbatim(tmp_config, tmp_vault):
    page = _make_page(tmp_vault)
    result = CliRunner().invoke(cli, ["show", "research/log.md"])
    assert result.exit_code == 0
    assert result.stdout == page


def test_outline_skips_headings_inside_code_fences(tmp_config, tmp_vault):
    body = "# Title\n\n```\n# Not a heading\n```\n\n## Real\n"
    _make_page(tmp_vault, body=body)
    result = CliRunner().invoke(cli, ["show", "research/log.md", "--outline"])
    assert result.exit_code == 0
    assert result.stdout == "# Title\n## Real\n"


def test_outline_on_a_page_with_no_headings_prints_nothing(tmp_config, tmp_vault):
    _make_page(tmp_vault, body="Just prose, no headings here.\n")
    result = CliRunner().invoke(cli, ["show", "research/log.md", "--outline"])
    assert result.exit_code == 0
    assert result.stdout == ""


def test_show_help_lists_the_outline_flag(tmp_config, tmp_vault):
    result = CliRunner().invoke(cli, ["show", "--help"])
    assert result.exit_code == 0
    assert "--outline" in result.stdout
