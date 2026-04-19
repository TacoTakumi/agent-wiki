import glob as globmod
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import click

from agent_wiki.adapters import ADAPTER_NAMES, build_adapter
from agent_wiki.config import get_vault_path, load_vault_config
from agent_wiki.context import run_context
from agent_wiki.doctor import SourcePathMissing, run_checks
from agent_wiki.conversation import (
    BUNDLE_SUBDIR,
    ingest_conversation as _ingest_conversation,
    write_bundle,
)
from agent_wiki.ingest import ingest_file
from agent_wiki.index import rebuild_index
from agent_wiki.lint import lint_vault
from agent_wiki.log import read_log
from agent_wiki.search import search_vault
from agent_wiki.sync import pending_count, sync as run_sync, synced_count
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
    raw_count = (
        len([p for p in raw_dir.iterdir() if p.is_file()]) if raw_dir.is_dir() else 0
    )
    sessions_dir = vault_path / BUNDLE_SUBDIR
    bundle_count = (
        len(list(sessions_dir.glob("*.md"))) if sessions_dir.is_dir() else 0
    )

    click.echo(f"\n  raw: {raw_count} files")
    click.echo(f"  bundles: {bundle_count}")
    click.echo(f"  sessions synced: {synced_count(vault_path)}")
    click.echo(f"  total: {total_pages} pages")

    log_entries = read_log(vault_path, last=1)
    if log_entries:
        click.echo(f"\nLast activity: {log_entries[0]}")


def _build_summarizer(vault_config):
    """Construct summarizer based on wiki.yaml summarizer.type."""
    from agent_wiki.summarize import make_summarizer
    return make_summarizer(vault_config.get("summarizer") or {})


def _build_redactor(vault_config):
    from agent_wiki.redact import make_redactor
    return make_redactor(vault_config.get("redaction") or {})


@cli.command()
@click.option("--source", "-s", default=None,
              type=click.Choice(list(ADAPTER_NAMES)),
              help="Limit sync to one source")
@click.option("--since", default=None, help="ISO date (YYYY-MM-DD) to limit by")
@click.option("--dry-run", is_flag=True, default=False, help="Report without writing")
@click.option("--include-live", is_flag=True, default=False,
              help="Include sessions modified in the last 60 minutes")
def sync(source, since, dry_run, include_live):
    """Discover new conversations from configured sources and ingest them."""
    vault_path = get_vault_path()
    vault_config = load_vault_config(vault_path)

    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            raise click.BadParameter(f"--since must be ISO 8601: {since}")

    if include_live:
        sources_cfg = vault_config.setdefault("sources", {})
        for name in ("claude_code", "opencode", "drop_zone"):
            if name in sources_cfg:
                sources_cfg[name] = dict(sources_cfg[name])
                sources_cfg[name]["include_live"] = True

    summarizer = _build_summarizer(vault_config) if not dry_run else None
    redactor = _build_redactor(vault_config) if not dry_run else None

    results = run_sync(
        vault_path, source=source, dry_run=dry_run, since=since_dt,
        summarizer=summarizer, redactor=redactor,
    )

    counts = {"new": 0, "updated": 0, "skipped": 0, "error": 0}
    for r in results:
        counts[r.action] = counts.get(r.action, 0) + 1
        if r.action == "error":
            click.echo(f"  [ERROR] {r.source} {r.key}: {r.error}", err=True)
        elif r.action in ("new", "updated"):
            tag = "DRY" if dry_run else r.action.upper()
            page = r.page.relative_to(vault_path) if r.page else ""
            click.echo(f"  [{tag}] {r.key} -> {page}")

    click.echo(
        f"\n{counts['new']} new, {counts['updated']} updated, "
        f"{counts['skipped']} unchanged, {counts['error']} errors"
    )


@cli.command()
@click.argument("source", type=click.Choice(list(ADAPTER_NAMES)))
@click.argument("ref")
@click.option("-o", "--output", default=None, type=click.Path(),
              help="Write bundle to this path instead of raw/sessions/")
def adapt(source, ref, output):
    """Convert one session to a conversation bundle without ingesting."""
    vault_path = get_vault_path()
    vault_config = load_vault_config(vault_path)
    cfg = (vault_config.get("sources") or {}).get(source.replace("-", "_"), {})
    adapter = build_adapter(source, cfg)

    # SOURCE is a path/id the adapter understands.
    ref_value = Path(ref) if source == "claude-code" else ref
    conv = adapter.to_bundle(ref_value)

    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        from agent_wiki.page import render_page
        out_path.write_text(render_page(conv.frontmatter(), conv.body))
        click.echo(f"Bundle written: {out_path}")
    else:
        path = write_bundle(conv, vault_path)
        click.echo(f"Bundle written: {path.relative_to(vault_path)}")


@cli.command()
@click.option("--fix", is_flag=True, default=False,
              help="Apply all fixes without prompting")
@click.option("--dry-run", is_flag=True, default=False,
              help="Only report findings")
def doctor(fix, dry_run):
    """Inspect the vault and offer to fix drift from current schema."""
    vault_path = get_vault_path()
    findings = run_checks(vault_path)

    if not findings:
        click.echo("No issues found.")
        return

    click.echo(f"Found {len(findings)} issue(s):\n")

    applied = 0
    skipped = 0
    for f in findings:
        click.echo(f"  [{f.check.name}] {f.detail}")
        informational = isinstance(f.check, SourcePathMissing)

        if dry_run or informational:
            click.echo(f"    → {f.check.description}")
            skipped += 1
            continue

        should_fix = fix or click.confirm(f"    Fix? [{f.check.description}]", default=True)
        if not should_fix:
            skipped += 1
            continue

        try:
            result = f.check.fix(vault_path)
            click.echo(f"    ✓ {result}")
            applied += 1
        except Exception as e:
            click.echo(f"    ✗ fix failed: {e}", err=True)
            skipped += 1

    click.echo(f"\n{applied} applied, {skipped} skipped")


@cli.command("ingest-conversation")
@click.argument("bundle", type=click.Path(exists=True, dir_okay=False))
@click.option("--no-summarize", is_flag=True, default=False,
              help="Skip the configured summarizer for this ingest")
def ingest_conversation_cmd(bundle, no_summarize):
    """Ingest a single conversation bundle into the vault."""
    vault_path = get_vault_path()
    vault_config = load_vault_config(vault_path)

    src = Path(bundle).resolve()
    sessions_dir = (vault_path / BUNDLE_SUBDIR).resolve()

    # If the bundle isn't already under raw/sessions/, copy it in so the
    # created page's [[wikilink]] resolves.
    try:
        src.relative_to(sessions_dir)
        bundle_in_vault = src
    except ValueError:
        sessions_dir.mkdir(parents=True, exist_ok=True)
        bundle_in_vault = sessions_dir / src.name
        shutil.copy2(src, bundle_in_vault)

    summarizer = None if no_summarize else _build_summarizer(vault_config)
    redactor = _build_redactor(vault_config)

    page_path = _ingest_conversation(
        bundle_in_vault, vault_path,
        summarizer=summarizer, redactor=redactor,
    )
    click.echo(f"Ingested {bundle_in_vault.name} -> {page_path.relative_to(vault_path)}")


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


@cli.command("context")
@click.option(
    "--output-format",
    type=click.Choice(["claude-json", "plain"]),
    default="claude-json",
    help="claude-json: emit {hookSpecificOutput:{additionalContext:...}}. "
         "plain: emit bare text.",
)
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="Log each call and its result as JSONL to "
         "~/.cache/agent-wiki/context.debug.log. Equivalent to "
         "AWIKI_CONTEXT_DEBUG=1.",
)
def context_cmd(output_format, debug):
    """Auto-context hook payload. Reads {'prompt': ...} JSON from stdin.

    Silent-fail: any error, skip, or zero-hit result → exit 0, no output.
    Never blocks the agent's prompt.
    """
    if debug:
        os.environ["AWIKI_CONTEXT_DEBUG"] = "1"

    try:
        payload = json.load(sys.stdin)
        prompt = payload.get("prompt", "")
        if not isinstance(prompt, str):
            return
    except Exception:
        return

    try:
        from agent_wiki.config import get_vault_path
        vault_path = get_vault_path()
    except Exception:
        return

    try:
        block = run_context(prompt, vault_path)
    except Exception:
        return

    if not block:
        return

    if output_format == "plain":
        click.echo(block, nl=False)
    else:
        click.echo(json.dumps({
            "hookSpecificOutput": {"additionalContext": block},
        }))


@cli.group("hook")
def hook_group():
    """Install / uninstall / inspect the auto-context hook for an agent CLI."""
    pass


@hook_group.command("install")
@click.option("--agent", default="claude", help="Target agent CLI (claude, manual).")
@click.option("--config-path", default=None, type=click.Path(),
              help="Override the agent's settings file path (for tests or non-default installs).")
def hook_install(agent, config_path):
    """Wire `awiki context` into the target agent's hook system."""
    from agent_wiki.hooks import get_backend
    try:
        backend = get_backend(agent)
    except KeyError as exc:
        raise click.ClickException(str(exc))
    path = Path(config_path) if config_path else None
    try:
        msg = backend["install"](config_path=path)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    click.echo(msg)


@hook_group.command("uninstall")
@click.option("--agent", default="claude")
@click.option("--config-path", default=None, type=click.Path())
def hook_uninstall(agent, config_path):
    """Remove the auto-context hook from the target agent's settings."""
    from agent_wiki.hooks import get_backend
    try:
        backend = get_backend(agent)
    except KeyError as exc:
        raise click.ClickException(str(exc))
    path = Path(config_path) if config_path else None
    try:
        msg = backend["uninstall"](config_path=path)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    click.echo(msg)


@hook_group.command("status")
@click.option("--agent", default="claude")
@click.option("--config-path", default=None, type=click.Path())
def hook_status(agent, config_path):
    """Report install state for the target agent."""
    from agent_wiki.hooks import get_backend
    try:
        backend = get_backend(agent)
    except KeyError as exc:
        raise click.ClickException(str(exc))
    path = Path(config_path) if config_path else None
    click.echo(backend["status"](config_path=path))
