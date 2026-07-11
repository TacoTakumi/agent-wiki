import glob as globmod
import json
import os
import sys
from pathlib import Path

import click
from agentsquire import BundledPackageDataSource, check_stale
from agentsquire.cli import skills_command_group

from agent_wiki import __version__
from agent_wiki.adapters import ADAPTER_NAMES
from agent_wiki.config import get_vault_path
from agent_wiki.doctor import (
    RawContentDrift, RenderHashDivergent, RenderHashUnstamped,
    SourcePathMissing, run_checks,
)
from agent_wiki.fetch import FetchError, is_url
from agent_wiki.ingest import PageDriftError, UnchangedURLSkip, resolve_raw
from agent_wiki.vault import init_vault


def _service():
    """Resolve the configured vault into a VaultService (local or remote)."""
    from agent_wiki.config import get_backend
    return get_backend()


def _repair_stale_config_if_needed(fix, dry_run):
    """If the configured local vault_path is stale (missing), help fix the config.

    Runs before doctor resolves the vault, since a stale path otherwise hard-stops
    every command. With an override (--vault/AWIKI_VAULT) pointing at a real vault,
    offer to persist it as the new vault_path; without one, there's nothing to
    repair to — report and stop with a friendly, actionable error."""
    from pathlib import Path
    from agent_wiki.config import (
        get_config_dir, load_user_config, resolve_vault_override, save_user_config,
    )

    config = load_user_config()
    if (config.get("server") or {}).get("url"):
        return  # remote-configured; a local vault_path is not in play
    vault_path = config.get("vault_path")
    if not vault_path or Path(vault_path).expanduser().exists():
        return  # nothing configured, or the configured vault is fine

    config_file = get_config_dir() / "config.yaml"
    click.echo(
        f"Configured vault is stale: vault_path in {config_file} points at "
        f"{vault_path}, which does not exist."
    )
    override = resolve_vault_override()
    if override is None:
        raise click.ClickException(
            "Re-run with --vault PATH (or set AWIKI_VAULT) pointing at the correct "
            "vault to repair the config, or run 'awiki init <path>'."
        )
    if not override.exists():
        raise click.ClickException(
            f"--vault/AWIKI_VAULT points at {override}, which also does not exist."
        )
    if dry_run:
        click.echo(f"    → would update vault_path to {override}")
        return
    if fix or click.confirm(f"    Update vault_path to {override}?", default=True):
        config["vault_path"] = str(override)
        save_user_config(config)
        click.echo(f"    ✓ updated vault_path to {override}")


@click.group()
@click.version_option(version=__version__, prog_name="awiki")
@click.option("--vault", default=None, type=click.Path(),
              metavar="PATH",
              help="Use this vault for this invocation, overriding the configured "
                   "one (also settable via AWIKI_VAULT). Forces a local vault.")
def cli(vault):
    """Agent Wiki - A personal knowledge base for AI agents."""
    # Proactive skill-staleness notice (AgentSquire). Safe by design: swallows
    # its own errors, never reads stdin, never prompts, and never touches stdout
    # or the exit code. It emits at most one stderr line naming
    # `awiki skills update`, and it is intentionally NOT gated on an interactive
    # TTY, so agents (which run awiki with captured, non-TTY stderr) see it too;
    # CI or AGENTSQUIRE_NO_UPDATE_CHECK suppress it. Runs before any subcommand
    # dispatch.
    check_stale(
        BundledPackageDataSource("agent_wiki"),
        prog_name="awiki",
        update_command="awiki skills update",
    )
    # `vault` is read back from the root context by config.resolve_vault_override();
    # nothing to do here beyond letting click record it on the context.


# Mount AgentSquire's ready-made skills command group. The three awiki skills
# ride inside the wheel as package data under agent_wiki/skills/ (resource_path
# defaults to "skills"); this exposes `awiki skills install|status|update|
# uninstall`, each taking --scope user|project and --harness NAME.
cli.add_command(skills_command_group("agent_wiki", default_scope="user"))


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
@click.option("--tag-mode", type=click.Choice(["off", "warn", "strict"]), default=None,
              help="Force the tag vocabulary mode for this ingest only "
                   "(does not change the vault's configured mode)")
def ingest(files, topic, tags, update, force, tag_mode):
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
                                     update=update, force=force, tag_mode=tag_mode)
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
            out = svc.ingest(path, topic=topic, tags=tag_list, update=update,
                             force=force, tag_mode=tag_mode)
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
    svc = _service()
    try:
        out = svc.reingest(name, force=force)
    except PageDriftError as e:
        if e.diff:
            click.echo(e.diff, err=True)
        raise click.ClickException(str(e))
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))
    click.echo(f"Reingested {name} -> {out['page']}")
    # Surface where the page landed on stderr (REQ-12): a local absolute path, or
    # for a remote vault the server URL + vault-relative path. stdout stays clean.
    click.echo(svc.describe_location(out["page"]), err=True)


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
    svc = _service()
    try:
        content = svc.show(path)
    except (ValueError, FileNotFoundError) as e:
        raise click.ClickException(str(e))
    click.echo(content, nl=False)
    # Surface where the content was read from on stderr (REQ-13): a local absolute
    # path, or for a remote vault the server URL + vault-relative path. stdout stays
    # byte-identical to the file so skills that parse show output verbatim are unaffected.
    click.echo(svc.describe_location(path), err=True)


@cli.command()
@click.argument("name")
def raw(name):
    """Print the raw source path for a page, by its raw <name>.

    A page's raw/<name> file is its source of truth: edit the raw, then
    `awiki reingest <name>`. On a local vault this prints the raw file's absolute
    path to stdout so it drops straight into command substitution (e.g.
    `$EDITOR "$(awiki raw foo)"`), erroring exactly as reingest does on a missing
    or ambiguous name. On a remote vault the raw lives on the server: it prints
    the server-side reference and notes on stderr that it is not editable locally.
    """
    from agent_wiki.remote import RemoteVaultService
    svc = _service()
    if isinstance(svc, RemoteVaultService):
        click.echo(svc.describe_location(f"raw/{name}"))
        click.echo(
            "remote vault: this raw source lives on the server and is not "
            "directly editable locally.", err=True)
        return
    try:
        raw_path = resolve_raw(svc.vault_path, name)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))
    click.echo(str(raw_path))


@cli.command()
@click.option("--raw", is_flag=True, default=False,
              help="Print only the marker-wrapped block (no agent header).")
def guide(raw):
    """Print self-installing instructions for wiring awiki into an agent."""
    from agent_wiki.guide import render_guide
    click.echo(render_guide(raw=raw), nl=False)


@cli.command("directions", hidden=True)
@click.option("--raw", is_flag=True, default=False,
              help="Print only the marker-wrapped block (no agent header).")
@click.pass_context
def directions(ctx, raw):
    """Deprecated alias for `awiki guide`."""
    ctx.invoke(guide, raw=raw)


@cli.command("index")
def index_cmd():
    """Rebuild the wiki index."""
    _service().rebuild_index()
    click.echo("Index rebuilt.")


# Canonical lint-type -> CLI label mapping. One distinct label per lint type;
# every type lint.py can emit (lint.LINT_TYPES) must have an entry here.
LINT_LABELS = {
    "broken_wikilink": "LINK",
    "orphan": "ORPHAN",
    "raw_not_ingested": "RAW",
    "missing_frontmatter": "META",
    "raw_page_drift": "DRIFT",
    "source_drift": "SOURCE",
    "upstream_changed": "UPSTREAM",
    "stale_content": "STALE",
    "page_size": "SIZE",
    "index_incomplete": "INDEX",
    "tag_audit": "TAG",
}


@cli.command()
@click.option("--refetch", is_flag=True,
              help="Re-fetch URL sources and flag any whose upstream content "
                   "changed (network; off by default). Local vaults only.")
@click.option("--strict", is_flag=True,
              help="CI gate: exit non-zero if any tag-audit (TAG) finding exists. "
                   "Does not change which findings print.")
def lint(refetch, strict):
    """Audit the wiki vault for issues."""
    issues = _service().lint(refetch=refetch)

    if not issues:
        click.echo("No issues found.")
        return

    for issue in issues:
        label = LINT_LABELS.get(issue["type"], issue["type"].upper())
        click.echo(f"  [{label}] {issue['detail']}  ({issue['path']})")

    click.echo(f"\n{len(issues)} issue(s) found.")

    # --strict gates the exit code only (REQ-13): tag-audit findings fail CI
    # while plain lint stays report-only. The printed findings are unchanged.
    if strict and any(i["type"] == "tag_audit" for i in issues):
        sys.exit(1)


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
    _repair_stale_config_if_needed(fix, dry_run)
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
        informational = isinstance(f.check, (SourcePathMissing, RenderHashDivergent))
        is_reconcile = isinstance(f.check, RawContentDrift)
        # The render_hash migration stamp is preview-by-default (REQ-09): list it
        # but write nothing unless --fix is passed — never via interactive confirm.
        is_stamp = isinstance(f.check, RenderHashUnstamped)

        if (dry_run or informational or (is_reconcile and not reconcile_raw)
                or (is_stamp and not fix)):
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


@cli.group("tag")
def tag_group():
    """Manage the wiki.yaml tag vocabulary."""
    pass


@tag_group.command("add")
@click.argument("preferred")
@click.option("--alias", "aliases", multiple=True,
              help="An alias for the preferred term (repeatable).")
def tag_add(preferred, aliases):
    """Add a preferred tag (and optional aliases) to the vocabulary.

    Idempotent: re-adding an existing term or alias is a no-op. Refuses to bind
    an alias already claimed by a different preferred term, exiting non-zero
    without writing."""
    from agent_wiki.config import (
        detect_vocabulary_conflicts, load_vault_config, parse_tag_vocabulary,
    )
    from agent_wiki.tag_yaml import update_tags_block

    vault = get_vault_path()
    try:
        vocab = parse_tag_vocabulary(load_vault_config(vault))
    except FileNotFoundError as e:
        raise click.ClickException(str(e))

    # Reuse the existing key's casing when the preferred term is already present,
    # so re-adding never forks a case-variant duplicate.
    key = next((k for k in vocab.vocabulary if k.lower() == preferred.lower()),
               preferred)
    proposed = {k: list(v) for k, v in vocab.vocabulary.items()}
    merged = list(proposed.get(key, []))
    seen = {a.lower() for a in merged} | {key.lower()}
    for alias in aliases:
        if alias.lower() not in seen:
            merged.append(alias)
            seen.add(alias.lower())
    proposed[key] = merged

    if proposed == vocab.vocabulary:
        click.echo(f"'{key}' already up to date; no change.")
        return

    # Refuse only conflicts this add would introduce, not ones already on disk.
    proposed_vocab = parse_tag_vocabulary(
        {"tags": {"mode": "warn", "vocabulary": proposed}}
    )
    before = {c.token for c in detect_vocabulary_conflicts(vocab)}
    new_conflicts = [c for c in detect_vocabulary_conflicts(proposed_vocab)
                     if c.token not in before]
    if new_conflicts:
        raise click.ClickException("; ".join(c.message for c in new_conflicts))

    update_tags_block(vault / "wiki.yaml", proposed)
    detail = f" (aliases: {', '.join(aliases)})" if aliases else ""
    click.echo(f"Added '{key}'{detail} to the tag vocabulary.")


@tag_group.command("suggest")
@click.option("--write", is_flag=True, default=False,
              help="Merge the suggested draft into wiki.yaml's tags block.")
def tag_suggest(write):
    """Suggest a tag vocabulary from the tags in use across the vault.

    Scans every topic-folder page, counts tag frequencies, and prints a draft
    'tags:' block (valid YAML) covering every in-use tag — grouping obviously
    related tags (shared token / prefix) as alias candidates and showing each
    tag's frequency. Prints only by default; --write merges the draft into
    wiki.yaml via the round-trip writer. String heuristics only, no ML."""
    from agent_wiki.config import (
        load_tag_vocabulary, load_vault_config, parse_tag_vocabulary,
    )
    from agent_wiki.tag_suggest import (
        cluster_tags, merge_clusters, render_suggestion_block, scan_tag_counts,
    )
    from agent_wiki.tag_yaml import update_tags_block

    vault = get_vault_path()
    config = load_vault_config(vault)
    counts = scan_tag_counts(vault, config)
    clusters = cluster_tags(counts)

    existing = parse_tag_vocabulary(config)
    # 'off' / absent block → draft in warn so the suggestion is actionable.
    mode = existing.mode if existing.mode in ("warn", "strict") else "warn"

    if write:
        merged = merge_clusters(existing.vocabulary, clusters)
        update_tags_block(vault / "wiki.yaml", merged)
        click.echo(f"Merged {len(clusters)} suggested term(s) into wiki.yaml.")
        # --write preserves an existing 'mode: off', so the merged vocabulary is
        # not enforced. The preview drafts in warn; flag the gap so the write is
        # not a silent no-op. (An absent block is created as warn — no hint.)
        if load_tag_vocabulary(vault).mode == "off":
            click.echo("Note: tag mode is 'off' — set 'mode: warn' in wiki.yaml "
                       "to enforce the vocabulary.")
        return

    click.echo(render_suggestion_block(clusters, counts, mode=mode), nl=False)


@tag_group.command("fix")
@click.argument("path", required=False)
@click.option("--topic", default=None,
              help="Narrow the pass to a single topic folder.")
@click.option("--write", is_flag=True, default=False,
              help="Rewrite page frontmatter tags in place (default: preview only).")
def tag_fix(path, topic, write):
    """Canonicalize frontmatter tags across the vault against the vocabulary.

    Preview by default: report every page whose tags would canonicalize and write
    nothing. --write rewrites only each page's frontmatter tag list — the page body
    stays byte-identical and raw/ is never touched. Known aliases are rewritten to
    their preferred term; novel out-of-vocabulary tags are reported but left for a
    human (adopt via 'tag add', or remove). Inert when no vocabulary is configured.

    --topic <t> narrows the pass to one topic; a PATH argument (a vault-relative or
    absolute file/directory) narrows it to that subtree. The default is the whole
    vault."""
    from agent_wiki.config import load_tag_vocabulary, load_vault_config
    from agent_wiki.tag_fix import apply_tag_fix, collect_tag_fixes

    vault = get_vault_path()
    config = load_vault_config(vault)
    topics = config.get("topics", [])

    if topic and path:
        raise click.ClickException("give either --topic or a PATH, not both.")

    root = None
    if topic:
        if topic not in topics:
            raise click.ClickException(
                f"unknown topic '{topic}'; known topics: {', '.join(topics)}")
        topics = [topic]
    elif path:
        p = Path(path)
        root = (p if p.is_absolute() else vault / p).resolve()
        try:
            root.relative_to(vault.resolve())
        except ValueError:
            raise click.ClickException(f"path '{path}' is outside the vault.")
        if not root.exists():
            raise click.ClickException(f"path '{path}' does not exist.")

    vocab = load_tag_vocabulary(vault)
    fixes = collect_tag_fixes(vault, vocab, topics, root=root)

    if not fixes:
        click.echo("No tag fixes needed.")
        return

    changed = 0
    for fix in fixes:
        if fix.changed:
            changed += 1
            if write:
                apply_tag_fix(vault, fix)
            verb = "fixed" if write else "would fix"
            click.echo(f"{fix.path}: {fix.before} -> {fix.after} ({verb})")
        else:
            click.echo(f"{fix.path}: {fix.before} (novel tags only; unchanged)")
        for alias, preferred in fix.remaps:
            click.echo(f"    alias '{alias}' -> '{preferred}'")
        for tag in fix.novel:
            click.echo(f"    novel '{tag}' (left unchanged; adopt with 'tag add' "
                       f"or remove)")

    if write:
        click.echo(f"Rewrote {changed} page(s).")
    else:
        click.echo(f"{changed} page(s) would change. Re-run with --write to apply.")


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
