import glob as globmod
import json
import os
import sys
from pathlib import Path

import click

from agent_wiki import __version__
from agent_wiki.adapters import ADAPTER_NAMES
from agent_wiki.config import get_vault_path
from agent_wiki.doctor import RawContentDrift, SourcePathMissing, run_checks
from agent_wiki.fetch import FetchError, is_url
from agent_wiki.ingest import PageDriftError, UnchangedURLSkip
from agent_wiki.vault import init_vault


def _service():
    """Resolve the configured vault into a VaultService (local or remote)."""
    from agent_wiki.config import get_backend
    return get_backend()


@click.group()
@click.version_option(version=__version__, prog_name="awiki")
def cli():
    """Agent Wiki - A personal knowledge base for AI agents."""
    pass


@cli.command()
@click.argument("path", default=None, required=False, type=click.Path())
@click.option("--remote", "url", default=None,
              help="Set up a REMOTE vault: full base URL incl. scheme and port, "
                   "e.g. http://host:8731 (no path).")
@click.option("--token", default=None, help="Bearer token for the remote server.")
@click.option("--clear", "clear", is_flag=True, default=False,
              help="Remove remote-server config from this client.")
def init(path, url, token, clear):
    """Initialize a vault: local (a path) or remote (--remote URL --token T).

    With no arguments, prompts for local vs remote.
    """
    from agent_wiki.config import load_user_config, save_user_config

    if clear:
        cfg = load_user_config()
        cfg.pop("server", None)
        save_user_config(cfg)
        click.echo("Remote server config cleared.")
        return

    # Decide mode.
    if url is None and path is None:
        mode = click.prompt("Set up (l)ocal or (r)emote vault?",
                            type=click.Choice(["l", "r"]), default="l")
        if mode == "r":
            url = click.prompt("Server URL (e.g. http://host:8731)")
            token = click.prompt("Token", hide_input=True)
        else:
            path = click.prompt("Vault path", default=".")

    if url is not None:  # remote
        if not token:
            token = click.prompt("Token", hide_input=True)
        save_user_config({"server": {"url": url, "token": token}})
        click.echo(f"Connected to remote vault at {url}")
        return

    # local
    vault_path = Path(path).resolve()
    try:
        init_vault(vault_path)   # also writes vault_path to user config
        click.echo(f"Vault initialized at {vault_path}")
    except FileExistsError as e:
        raise click.ClickException(str(e))


@cli.command()
@click.argument("files", nargs=-1, required=True)
@click.option("--topic", "-t", default=None, help="Target topic folder")
@click.option("--tags", default=None, help="Comma-separated tags")
@click.option("--update", is_flag=True, default=False,
              help="Update the page for an existing raw from an EXTERNAL file. "
                   "To rebuild after editing the vault's own raw, use `awiki reingest`.")
@click.option("--force", is_flag=True, default=False,
              help="Overwrite even if the page has diverged from its raw source")
def ingest(files, topic, tags, update, force):
    """Ingest files or URLs into the wiki vault."""
    svc = _service()
    tag_list = [t.strip() for t in tags.split(",")] if tags else None

    expanded = []
    for pattern in files:
        if is_url(pattern):
            expanded.append(pattern)  # URLs bypass glob expansion
            continue
        matches = globmod.glob(pattern)
        if matches:
            expanded.extend(matches)
        else:
            expanded.append(pattern)

    skipped = 0
    for file_path in expanded:
        if is_url(file_path):
            try:
                out = svc.ingest_url(file_path, topic=topic, tags=tag_list,
                                     update=update, force=force)
                verb = "Updated" if update else "Ingested"
                click.echo(f"{verb} {file_path} -> {out['page']}")
            except UnchangedURLSkip:
                # Not an error: nothing changed upstream, so exit 0.
                click.echo(f"unchanged: {file_path} (already up to date; --force to re-render)")
            except (FetchError, ValueError, FileExistsError) as e:
                # Network error, unsupported content type, or extractor failure:
                # one friendly line, no traceback (newlines collapsed).
                msg = " ".join(str(e).split())
                click.echo(f"error: could not ingest {file_path}: {msg}", err=True)
                skipped += 1
            continue
        path = Path(file_path)
        try:
            out = svc.ingest(path, topic=topic, tags=tag_list, update=update, force=force)
            verb = "Updated" if update else "Ingested"
            click.echo(f"{verb} {path.name} -> {out['page']}")
        except PageDriftError as e:
            click.echo(f"refused: {path.name}: {e}", err=True)
            if e.diff:
                click.echo(e.diff, err=True)
            skipped += 1
        except FileExistsError:
            click.echo(f"skipped: {path.name} already exists — use --update to overwrite",
                       err=True)
            skipped += 1
        except FileNotFoundError as e:
            click.echo(f"skipped: {e}", err=True)
            skipped += 1
        except ValueError as e:
            click.echo(f"skipped: {path.name}: {e}", err=True)
            skipped += 1
    if skipped:
        sys.exit(1)


@cli.command()
@click.argument("name")
@click.option("--force", is_flag=True, default=False,
              help="Rebuild even if the page has diverged from its raw source")
def reingest(name, force):
    """Rebuild a page from its existing raw/<name> after you edit the raw.

    The canonical loop: edit raw/<name>, then `awiki reingest <name>`. It compares
    the page to the raw and, if they differ, prints a diff and stops — review it
    (fold anything worth keeping into the raw), then re-run with --force.

    The body is taken verbatim from the raw; front matter (title from the first
    `# H1`, tags, created) is regenerated — keep the H1 stable or the slug (and thus
    the page path) changes, which can orphan the page.
    """
    try:
        out = _service().reingest(name, force=force)
    except PageDriftError as e:
        if e.diff:
            click.echo(e.diff, err=True)
        raise click.ClickException(str(e))
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))
    click.echo(f"Reingested {name} -> {out['page']}")


def _echo_result(r, show_coverage=False):
    """Print one search result in the standard title/path/snippet format."""
    suffix = f"  ({r['coverage']}/{r['term_count']} terms)" if show_coverage else ""
    click.echo(f"\n## {r['title']}{suffix}")
    click.echo(f"   {r['path']}")
    for match in r["matches"][:3]:
        click.echo(f"   > {match}")


@cli.command()
@click.argument("query")
@click.option("--topic", "-t", default=None, help="Limit search to a topic")
@click.option("--limit", "-N", default=20, show_default=True,
              type=click.IntRange(min=1),
              help="Max results to show in the all-terms tier")
def search(query, topic, limit):
    """Search the wiki vault."""
    out = _service().search(query, topic=topic, limit=limit)

    if out["total"] == 0:
        click.echo("No results found.")
        return

    for r in out["all"]:
        _echo_result(r)

    if out["partial"]:
        # Only separate from the all-tier with a blank line if it printed anything.
        prefix = "\n" if out["all"] else ""
        click.echo(f"{prefix}Partial matches (some terms only):")
        for r in out["partial"]:
            _echo_result(r, show_coverage=True)

    if out["truncated"]:
        click.echo(
            f"\nShowing {out['shown']} of {out['total']} matches — "
            f"narrow your query or use --topic."
        )


@cli.command()
@click.argument("path")
def show(path):
    """Print a wiki page (or any vault file) by its vault-relative path."""
    try:
        content = _service().show(path)
    except (ValueError, FileNotFoundError) as e:
        raise click.ClickException(str(e))
    click.echo(content, nl=False)


@cli.command()
@click.option("--raw", is_flag=True, default=False,
              help="Print only the marker-wrapped block (no agent header).")
def directions(raw):
    """Print self-installing instructions for wiring awiki into an agent."""
    from agent_wiki.directions import render_directions
    click.echo(render_directions(raw=raw), nl=False)


@cli.command("index")
def index_cmd():
    """Rebuild the wiki index."""
    _service().rebuild_index()
    click.echo("Index rebuilt.")


@cli.command()
@click.option("--refetch", is_flag=True,
              help="Re-fetch URL sources and flag any whose upstream content "
                   "changed (network; off by default). Local vaults only.")
def lint(refetch):
    """Audit the wiki vault for issues."""
    issues = _service().lint(refetch=refetch)

    if not issues:
        click.echo("No issues found.")
        return

    for issue in issues:
        icon = {"broken_wikilink": "LINK", "orphan": "ORPHAN",
                "raw_not_ingested": "RAW", "missing_frontmatter": "META",
                "raw_page_drift": "DRIFT", "source_drift": "SOURCE",
                "upstream_changed": "UPSTREAM"}
        label = icon.get(issue["type"], issue["type"].upper())
        click.echo(f"  [{label}] {issue['detail']}  ({issue['path']})")

    click.echo(f"\n{len(issues)} issue(s) found.")


@cli.command()
def status():
    """Show vault status overview."""
    st = _service().status()

    click.echo(f"Vault: {st['vault']}\n")

    for t in st["topics"]:
        click.echo(f"  {t['topic']}: {t['count']} pages")

    click.echo(f"\n  raw: {st['raw']} files")
    click.echo(f"  bundles: {st['bundles']}")
    click.echo(f"  sessions synced: {st['sessions_synced']}")
    click.echo(f"  total: {st['total']} pages")

    if st["last_activity"]:
        click.echo(f"\nLast activity: {st['last_activity']}")


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
    try:
        out = _service().sync(
            source=source, since=since, dry_run=dry_run, include_live=include_live,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    for r in out["results"]:
        if r["action"] == "error":
            click.echo(f"  [ERROR] {r['source']} {r['key']}: {r['error']}", err=True)
        elif r["action"] in ("new", "updated"):
            tag = "DRY" if dry_run else r["action"].upper()
            page = r["page"] if r["page"] else ""
            click.echo(f"  [{tag}] {r['key']} -> {page}")

    counts = out["counts"]
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
    out = _service().adapt(source, ref, output=output)
    click.echo(f"Bundle written: {out['bundle']}")


@cli.command()
@click.option("--fix", is_flag=True, default=False,
              help="Apply all (schema) fixes without prompting")
@click.option("--dry-run", is_flag=True, default=False,
              help="Only report findings")
@click.option("--reconcile-raw", "reconcile_raw", is_flag=True, default=False,
              help="Rewrite raw/ from drifted pages (server-local only)")
def doctor(fix, dry_run, reconcile_raw):
    """Inspect the vault and offer to fix drift from current schema."""
    svc = _service()
    from agent_wiki.remote import RemoteVaultService
    if reconcile_raw and isinstance(svc, RemoteVaultService):
        raise click.ClickException(
            "--reconcile-raw rewrites raw/ and must be run on the server; "
            "it is not available to remote clients."
        )
    if isinstance(svc, RemoteVaultService):
        out = svc.doctor(fix=fix, dry_run=dry_run)
        if not out["findings"]:
            click.echo("No issues found.")
            return
        click.echo(f"Found {len(out['findings'])} issue(s):\n")
        for f in out["findings"]:
            click.echo(f"  [{f['name']}] {f['detail']}")
            click.echo(f"    → {f['description']}")
        click.echo(f"\n{out['applied']} applied, {out['skipped']} skipped")
        return

    # local: interactive confirm loop using core checks.
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
        is_reconcile = isinstance(f.check, RawContentDrift)

        if dry_run or informational or (is_reconcile and not reconcile_raw):
            click.echo(f"    → {f.check.description}")
            skipped += 1
            continue

        if is_reconcile:
            should_fix = fix or click.confirm(
                f"    Rewrite raw from page? [{f.check.description}]", default=False)
        else:
            should_fix = fix or click.confirm(
                f"    Fix? [{f.check.description}]", default=True)
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
    out = _service().ingest_conversation(Path(bundle), no_summarize=no_summarize)
    click.echo(f"Ingested {out['bundle']} -> {out['page']}")


@cli.command("log")
@click.option("--last", "-n", default=None, type=int, help="Show last N entries")
def log_cmd(last):
    """Show activity log."""
    entries = _service().log(last=last)

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
    help="claude-json: emit {hookSpecificOutput:{hookEventName:UserPromptSubmit,"
         "additionalContext:...}}. plain: emit bare text.",
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
        svc = _service()
    except Exception:
        return

    try:
        block = svc.context(prompt)
    except Exception:
        return

    if not block:
        return

    if output_format == "plain":
        click.echo(block, nl=False)
    else:
        click.echo(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": block,
            },
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


@cli.group("token")
def token_group():
    """Manage server bearer tokens (admin, local on the server host)."""
    pass


@token_group.command("add")
@click.argument("name")
@click.option("--role", type=click.Choice(["reader", "writer", "admin"]), required=True)
def token_add(name, role):
    """Generate a token, print it ONCE, and store only its hash."""
    from agent_wiki.server_config import add_token
    try:
        secret = add_token(name, role)
    except ValueError as e:
        raise click.ClickException(str(e))
    click.echo(f"Token for '{name}' ({role}) — store it now, it will not be shown again:")
    click.echo(secret)


@token_group.command("list")
def token_list():
    """List token names and roles (never the secret)."""
    from agent_wiki.server_config import list_tokens
    tokens = list_tokens()
    if not tokens:
        click.echo("No tokens.")
        return
    for t in tokens:
        click.echo(f"  {t['name']}: {t['role']}")


@token_group.command("revoke")
@click.argument("name")
def token_revoke(name):
    """Revoke a token by name."""
    from agent_wiki.server_config import revoke_token
    click.echo(f"Revoked '{name}'." if revoke_token(name) else f"No token named '{name}'.")


@cli.command()
@click.option("--bind", default=None, help="Bind address (default from server.yaml or 127.0.0.1).")
@click.option("--port", default=None, type=int, help="Port (default from server.yaml or 8731).")
def serve(bind, port):
    """Run the agent-wiki HTTP server for the LOCAL vault (server host)."""
    import uvicorn
    from agent_wiki.server_config import load_server_config
    from agent_wiki.server.app import create_app

    vault_path = get_vault_path()
    server_config = load_server_config()
    host = bind or server_config["bind"]
    bind_port = port or server_config["port"]
    if not server_config["tokens"]:
        click.echo("WARNING: no tokens configured; all requests will be rejected. "
                   "Run 'awiki token add ...' first.", err=True)
    app = create_app(vault_path, server_config)
    click.echo(f"Serving vault {vault_path} on http://{host}:{bind_port}")
    uvicorn.run(app, host=host, port=bind_port)
