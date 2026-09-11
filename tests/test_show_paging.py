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


WATERMARK_BODY = """# Feeds

Intro line.

## Watermarks - as of 2026-09-01

Watermark preamble.

### Feed A

Feed A text.

### Feed B

Feed B text.

## Sources

Source list.
"""

AMBIGUOUS_BODY = """# Ops

## Check log

Current entries.

## Sources

Source list.

### Old check log

Archived entries.
"""


def test_section_prints_the_matching_section_and_stops(tmp_config, tmp_vault):
    _make_page(tmp_vault, body=WATERMARK_BODY)
    result = CliRunner().invoke(
        cli, ["show", "research/log.md", "--section", "watermarks"])
    assert result.exit_code == 0
    assert result.stdout == (
        "## Watermarks - as of 2026-09-01\n"
        "\n"
        "Watermark preamble.\n"
        "\n"
        "### Feed A\n"
        "\n"
        "Feed A text.\n"
        "\n"
        "### Feed B\n"
        "\n"
        "Feed B text.\n"
    )


def test_section_output_drops_the_frontmatter(tmp_config, tmp_vault):
    _make_page(tmp_vault, body=WATERMARK_BODY)
    result = CliRunner().invoke(
        cli, ["show", "research/log.md", "--section", "feeds"])
    assert result.exit_code == 0
    assert not result.stdout.startswith("---")
    assert result.stdout.startswith("# Feeds\n")


def test_section_notes_other_matches_on_stderr(tmp_config, tmp_vault):
    _make_page(tmp_vault, body=AMBIGUOUS_BODY)
    result = CliRunner().invoke(
        cli, ["show", "research/log.md", "--section", "check log"])
    assert result.exit_code == 0
    assert result.stdout == "## Check log\n\nCurrent entries.\n"
    assert "### Old check log" in result.stderr
    assert "## Check log" not in result.stderr


def test_section_with_one_match_notes_nothing(tmp_config, tmp_vault):
    _make_page(tmp_vault, body=WATERMARK_BODY)
    result = CliRunner().invoke(
        cli, ["show", "research/log.md", "--section", "sources"])
    assert result.exit_code == 0
    assert "also match" not in result.stderr


def test_section_with_no_match_exits_one_and_names_the_text(tmp_config, tmp_vault):
    _make_page(tmp_vault, body=WATERMARK_BODY)
    result = CliRunner().invoke(
        cli, ["show", "research/log.md", "--section", "nosuch"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "nosuch" in result.stderr


def test_show_help_lists_the_section_flag(tmp_config, tmp_vault):
    result = CliRunner().invoke(cli, ["show", "--help"])
    assert result.exit_code == 0
    assert "--section" in result.stdout


def _check_log_body(entries=5):
    """A page whose 'Check log' section holds `entries` dated child entries,
    each with body text and one deeper sub-entry, followed by a leaf section."""
    parts = ["# Ops\n", "\n", "## Check log\n", "\n", "Preamble for the log.\n", "\n"]
    for n in range(1, entries + 1):
        parts += [
            f"### Entry {n}\n", "\n", f"Body {n}.\n", "\n",
            f"#### Detail {n}\n", "\n", f"Detail body {n}.\n", "\n",
        ]
    parts += ["## Notes\n", "\n", "Notes body with no sub-headings.\n"]
    return "".join(parts)


def test_section_head_prints_the_first_child_entries(tmp_config, tmp_vault):
    _make_page(tmp_vault, body=_check_log_body())
    result = CliRunner().invoke(
        cli, ["show", "research/log.md", "--section", "check log", "--head", "2"])
    assert result.exit_code == 0
    assert result.stdout == (
        "## Check log\n"
        "\n"
        "Preamble for the log.\n"
        "\n"
        "### Entry 1\n"
        "\n"
        "Body 1.\n"
        "\n"
        "#### Detail 1\n"
        "\n"
        "Detail body 1.\n"
        "\n"
        "### Entry 2\n"
        "\n"
        "Body 2.\n"
        "\n"
        "#### Detail 2\n"
        "\n"
        "Detail body 2.\n"
    )


def test_section_tail_prints_the_last_child_entries(tmp_config, tmp_vault):
    _make_page(tmp_vault, body=_check_log_body())
    result = CliRunner().invoke(
        cli, ["show", "research/log.md", "--section", "check log", "--tail", "2"])
    assert result.exit_code == 0
    assert result.stdout.startswith("## Check log\n\nPreamble for the log.\n\n### Entry 4\n")
    for n in (4, 5):
        assert f"### Entry {n}\n" in result.stdout
        assert f"#### Detail {n}\n" in result.stdout
    for n in (1, 2, 3):
        assert f"### Entry {n}\n" not in result.stdout
    assert "## Notes" not in result.stdout


def test_section_head_beyond_the_entry_count_prints_them_all(tmp_config, tmp_vault):
    _make_page(tmp_vault, body=_check_log_body())
    runner = CliRunner()
    sliced = runner.invoke(
        cli, ["show", "research/log.md", "--section", "check log", "--head", "9"])
    whole = runner.invoke(
        cli, ["show", "research/log.md", "--section", "check log"])
    assert sliced.exit_code == 0
    assert sliced.stdout == whole.stdout
    assert "### Entry 5" in sliced.stdout


def test_leaf_section_head_equals_the_unsliced_section(tmp_config, tmp_vault):
    _make_page(tmp_vault, body=_check_log_body())
    runner = CliRunner()
    sliced = runner.invoke(
        cli, ["show", "research/log.md", "--section", "notes", "--head", "1"])
    whole = runner.invoke(cli, ["show", "research/log.md", "--section", "notes"])
    assert sliced.exit_code == 0
    assert sliced.stdout == whole.stdout == "## Notes\n\nNotes body with no sub-headings.\n"


def test_show_help_lists_the_head_and_tail_flags(tmp_config, tmp_vault):
    result = CliRunner().invoke(cli, ["show", "--help"])
    assert result.exit_code == 0
    assert "--head" in result.stdout
    assert "--tail" in result.stdout
