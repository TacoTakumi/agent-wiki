import glob as globmod
from pathlib import Path

import click

from agent_wiki.config import get_vault_path, load_vault_config
from agent_wiki.ingest import ingest_file
from agent_wiki.index import rebuild_index
from agent_wiki.lint import lint_vault
from agent_wiki.log import read_log
from agent_wiki.search import search_vault
from agent_wiki.vault import init_vault


@click.group()
@click.version_option(package_name="agent-wiki")
def cli():
    """Agent Wiki - A personal knowledge base for AI agents."""
    pass


@cli.command()
@click.argument("path", default=".", type=click.Path())
def init(path):
    """Initialize a new wiki vault."""
    vault_path = Path(path).resolve()
    try:
        init_vault(vault_path)
        click.echo(f"Vault initialized at {vault_path}")
    except FileExistsError as e:
        raise click.ClickException(str(e))


@cli.command()
@click.argument("files", nargs=-1, required=True)
@click.option("--topic", "-t", default=None, help="Target topic folder")
@click.option("--tags", default=None, help="Comma-separated tags")
def ingest(files, topic, tags):
    """Ingest files into the wiki vault."""
    vault_path = get_vault_path()
    tag_list = [t.strip() for t in tags.split(",")] if tags else None

    expanded = []
    for pattern in files:
        matches = globmod.glob(pattern)
        if matches:
            expanded.extend(matches)
        else:
            expanded.append(pattern)

    for file_path in expanded:
        path = Path(file_path)
        try:
            result = ingest_file(path, vault_path, topic=topic, tags=tag_list)
            click.echo(f"Ingested {path.name} -> {result.relative_to(vault_path)}")
        except FileNotFoundError as e:
            raise click.ClickException(str(e))


@cli.command()
@click.argument("query")
@click.option("--topic", "-t", default=None, help="Limit search to a topic")
def search(query, topic):
    """Search the wiki vault."""
    vault_path = get_vault_path()
    results = search_vault(vault_path, query, topic=topic)

    if not results:
        click.echo("No results found.")
        return

    for r in results:
        click.echo(f"\n## {r['title']}")
        click.echo(f"   {r['path']}")
        for match in r["matches"][:3]:
            click.echo(f"   > {match}")


@cli.command("index")
def index_cmd():
    """Rebuild the wiki index."""
    vault_path = get_vault_path()
    rebuild_index(vault_path)
    click.echo("Index rebuilt.")


@cli.command()
def lint():
    """Audit the wiki vault for issues."""
    vault_path = get_vault_path()
    issues = lint_vault(vault_path)

    if not issues:
        click.echo("No issues found.")
        return

    for issue in issues:
        icon = {"broken_wikilink": "LINK", "orphan": "ORPHAN",
                "raw_not_ingested": "RAW", "missing_frontmatter": "META"}
        label = icon.get(issue["type"], issue["type"].upper())
        click.echo(f"  [{label}] {issue['detail']}  ({issue['path']})")

    click.echo(f"\n{len(issues)} issue(s) found.")


@cli.command()
def status():
    """Show vault status overview."""
    vault_path = get_vault_path()
    vault_config = load_vault_config(vault_path)
    topics = vault_config.get("topics", [])

    click.echo(f"Vault: {vault_path}\n")

    total_pages = 0
    for topic in topics:
        topic_dir = vault_path / topic
        if not topic_dir.is_dir():
            continue
        count = len(list(topic_dir.rglob("*.md")))
        total_pages += count
        click.echo(f"  {topic}: {count} pages")

    raw_dir = vault_path / "raw"
    raw_count = len(list(raw_dir.iterdir())) if raw_dir.is_dir() else 0
    click.echo(f"\n  raw: {raw_count} files")
    click.echo(f"  total: {total_pages} pages")

    log_entries = read_log(vault_path, last=1)
    if log_entries:
        click.echo(f"\nLast activity: {log_entries[0]}")


@cli.command("log")
@click.option("--last", "-n", default=None, type=int, help="Show last N entries")
def log_cmd(last):
    """Show activity log."""
    vault_path = get_vault_path()
    entries = read_log(vault_path, last=last)

    if not entries:
        click.echo("No log entries.")
        return

    for entry in entries:
        click.echo(entry)
