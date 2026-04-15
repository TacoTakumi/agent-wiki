import yaml
import pytest
from pathlib import Path
from agent_wiki.ingest import ingest_file
from agent_wiki.page import parse_page


def test_ingest_copies_to_raw(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# My Notes\n\nSome content here.\n")

    ingest_file(source, tmp_vault, topic="research")

    assert (tmp_vault / "raw" / "notes.md").exists()
    assert (tmp_vault / "raw" / "notes.md").read_text() == source.read_text()


def test_ingest_creates_wiki_page(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# My Notes\n\nSome content here.\n")

    result = ingest_file(source, tmp_vault, topic="research")

    assert result.exists()
    assert result.parent.name == "research"

    page = parse_page(result)
    assert page["meta"]["title"] == "My Notes"
    assert page["meta"]["topic"] == "research"
    assert page["meta"]["sources"] == ["raw/notes.md"]
    assert "Some content here." in page["body"]


def test_ingest_with_tags(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# My Notes\n\nContent.\n")

    result = ingest_file(source, tmp_vault, topic="tools", tags=["python", "cli"])

    page = parse_page(result)
    assert page["meta"]["tags"] == ["python", "cli"]


def test_ingest_uses_default_topic(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# My Notes\n\nContent.\n")

    result = ingest_file(source, tmp_vault)

    assert result.parent.name == "research"


def test_ingest_title_from_filename(tmp_vault, tmp_path):
    source = tmp_path / "my-great-notes.md"
    source.write_text("No heading here, just text.\n")

    result = ingest_file(source, tmp_vault)

    page = parse_page(result)
    assert page["meta"]["title"] == "my-great-notes"


def test_ingest_appends_to_log(tmp_vault, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# My Notes\n\nContent.\n")

    ingest_file(source, tmp_vault, topic="research")

    log_content = (tmp_vault / "log.md").read_text()
    assert "ingest" in log_content.lower()
    assert "notes.md" in log_content


def test_ingest_nonexistent_file(tmp_vault, tmp_path):
    with pytest.raises(FileNotFoundError):
        ingest_file(tmp_path / "nope.md", tmp_vault)
