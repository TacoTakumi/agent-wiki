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


def _stdin_isatty() -> bool:
    """Is stdin an interactive terminal? Module-level so tests can patch it."""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _parse_topics(raw: str) -> list:
    """Split a comma-separated topic list, trimmed, de-duped, each slug-legal."""
    from agent_wiki.page import slugify
    topics = list(dict.fromkeys(t.strip() for t in raw.split(",") if t.strip()))
    for t in topics:
        if slugify(t) != t:
            raise click.UsageError(
                f"topic {t!r} is not slug-legal; use lowercase letters, "
                f"digits, and hyphens."
            )
    return topics


def _dispatch_ref(value, probe):
    """Resolve a possibly vault-qualified reference to (service, ref).

    Single-vault configs and override (--vault/AWIKI_VAULT) invocations keep
    today's behavior exactly: the default service and the untouched value.
    With multiple vaults, a vault: prefix narrows to that vault; an
    unqualified reference resolves across all vaults via the probe — unique
    match wins, ambiguity errors listing qualified candidates."""
    from agent_wiki.config import (
        backend_for_entry, load_registry, resolve_vault_override)
    if resolve_vault_override() is not None:
        return _service(), value
    registry = load_registry()
    if len(registry) <= 1:
        return _service(), value
    from agent_wiki.resolve import resolve_ref
    result = resolve_ref(value, registry, probe)
    if result is None:
        raise click.ClickException(
            f"'{value}' not found in any configured vault")
    entry, resolved = result
    return backend_for_entry(entry), resolved


def _vault_topics(entry):
    """The topics a vault declares, best-effort: a vault that cannot be read
    (unreachable remote, stale path) declares nothing."""
    from agent_wiki.config import backend_for_entry, load_vault_config
    try:
        if entry.url:
            st = backend_for_entry(entry).status()
            return [t["topic"] for t in st.get("topics", [])]
        return load_vault_config(entry.path).get("topics") or []
    except Exception:
        return []


def _ingest_service(topic):
    """Resolve the target vault for an ingest; returns (service, topic).

    Explicit selection always beats topic routing: an override
    (--vault/AWIKI_VAULT) or a vault: prefix on the topic narrows directly.
    Otherwise a topic declared by exactly one vault routes there, a topic
    declared by several is a hard error naming them, and an undeclared topic
    (or no topic) falls back to the default vault — today's semantics."""
    from agent_wiki.config import (
        backend_for_entry, load_registry, resolve_vault_override)
    if resolve_vault_override() is not None:
        return _service(), topic
    registry = load_registry()
    if len(registry) <= 1:
        return _service(), topic
    if topic:
        from agent_wiki.resolve import split_vault_ref
        entry, topic_ref = split_vault_ref(topic, registry)
        if entry is not None:
            return backend_for_entry(entry), topic_ref
        declaring = [
            registry[name] for name in sorted(registry)
            if topic in _vault_topics(registry[name])
        ]
        if len(declaring) > 1:
            names = ", ".join(e.name for e in declaring)
            raise click.UsageError(
                f"topic '{topic}' is declared by multiple vaults ({names}); "
                f"pass --vault NAME or qualify the topic as NAME:{topic}."
            )
        if declaring:
            return backend_for_entry(declaring[0]), topic
    return _service(), topic


def _show_probe(entry, ref):
    """Does `ref` exist (as a showable file) in this vault? An extensionless
    page path counts when `ref + '.md'` exists — the ref is returned unchanged
    so the show command's fallback resolves (and warns about) it in one place.
    Unreachable or denying vaults are skipped with a one-line stderr note."""
    from agent_wiki.config import backend_for_entry
    svc = backend_for_entry(entry)
    candidates = [ref] if ref.endswith(".md") else [ref, ref + ".md"]
    for candidate in candidates:
        try:
            svc.show(candidate)
            return ref
        except (FileNotFoundError, ValueError):
            continue
        except Exception as e:
            click.echo(f"skipping vault '{entry.name}': {e}", err=True)
            return None
    return None


def _show_with_md_fallback(svc, ref):
    """Read `ref` from the vault, falling back once to `ref + '.md'` for
    extensionless page paths. Returns (content, ref actually read); when both
    miss, the original error is re-raised so it names what the user typed."""
    try:
        return svc.show(ref), ref
    except FileNotFoundError as original:
        if ref.endswith(".md"):
            raise
        try:
            return svc.show(ref + ".md"), ref + ".md"
        except (FileNotFoundError, ValueError):
            raise original


def _raw_name_probe(entry, ref):
    """Does a raw file matching `ref` exist in this LOCAL vault? Remote vaults
    are skipped for unqualified raw names — their raws live server-side;
    qualify (vault:name) to target one. A name ambiguous within one vault
    propagates its ValueError."""
    from agent_wiki.ingest import resolve_raw
    if entry.url:
        return None
    try:
        resolve_raw(entry.path, ref)
        return ref
    except FileNotFoundError:
        return None


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
    entry = resolve_vault_override()
    override = entry.path if entry is not None else None
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
              metavar="NAME|PATH",
              help="Use this vault for this invocation, overriding the configured "
                   "default (also settable via AWIKI_VAULT). A configured vault "
                   "name narrows to that vault (local or remote); any other "
                   "value is a local vault at that path (./ or an absolute "
                   "path forces path interpretation).")
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
@click.option("--name", "vault_name", default=None,
              help="Register the new vault under this name in the vaults: "
                   "schema (bare init registers as main).")
@click.option("--topics", "topics_opt", default=None,
              help="Comma-separated topics for the new vault; the first "
                   "becomes its default_topic. Without this, the first vault "
                   "gets the standard defaults; a vault added beside existing "
                   "ones prompts (or gets no topics when non-interactive).")
def init(path, url, token, clear, vault_name, topics_opt):
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

    if vault_name is not None:
        from agent_wiki.page import slugify
        from agent_wiki.registry import parse_registry
        if not vault_name or slugify(vault_name) != vault_name:
            raise click.UsageError(
                f"vault name {vault_name!r} is not slug-legal; use lowercase "
                f"letters, digits, and hyphens."
            )
        existing = parse_registry(load_user_config()).get(vault_name)
        if existing is not None:
            # A local entry whose vault is gone (no wiki.yaml at its path) is
            # stale registry weight, not a conflict: the name is reclaimable.
            # Remote entries are never treated as stale — unreachable is not
            # the same as deleted.
            stale = (existing.url is None and existing.path is not None
                     and not (existing.path / "wiki.yaml").is_file())
            if not stale:
                raise click.UsageError(
                    f"vault '{vault_name}' is already configured."
                )
            click.echo(
                f"note: vault '{vault_name}' pointed at a missing vault "
                f"({existing.path}); reclaiming the name.", err=True)

    if url is not None:  # remote
        if topics_opt is not None:
            raise click.UsageError("--topics applies to local vaults only.")
        if not token:
            token = click.prompt("Token", hide_input=True)
        config = load_user_config()
        if (vault_name is not None or config.get("vaults")
                or config.get("trusted_dirs") or config.get("vault_path")):
            # A named init, or a config holding anything a wipe would lose
            # (a registry, a trust allowlist, a local vault_path): the remote
            # lands as a vaults: entry via the REQ-24 migration, preserving
            # every existing entry and trusted_dirs. Merging onto an existing
            # entry keeps its path beside the url (the hybrid form: url wins
            # at backend selection, the path serves local resolution).
            from agent_wiki.config import migrate_to_vaults_schema
            config = migrate_to_vaults_schema(config)
            name = vault_name or "main"
            entry = dict((config.get("vaults") or {}).get(name) or {})
            entry.update({"url": url, "token": token})
            config.setdefault("vaults", {})[name] = entry
            save_user_config(config)
        else:
            # Legacy form, byte-equivalent to the current release (REQ-24).
            save_user_config({"server": {"url": url, "token": token}})
        click.echo(f"Connected to remote vault at {url}")
        return

    # local
    vault_path = Path(path).resolve()
    topic_list = _parse_topics(topics_opt) if topics_opt is not None else None

    # A vault that will coexist with other registered vaults must not clone
    # DEFAULT_TOPICS — duplicated topics make every unqualified --topic
    # ambiguous across vaults. "Others" excludes the registry name this init
    # claims, so a legacy bare re-init (which replaces the sole vault) keeps
    # the out-of-box defaults.
    from agent_wiki.config import load_registry
    target_name = vault_name or "main"
    others = {n: e for n, e in load_registry().items() if n != target_name}
    if topic_list is None and others:
        if _stdin_isatty():
            answer = click.prompt(
                "Topics for this vault (comma-separated, empty for none)",
                default="", show_default=False)
            topic_list = _parse_topics(answer)
        else:
            topic_list = []

    try:
        # also registers in user config
        init_vault(vault_path, name=vault_name, topics=topic_list)
        click.echo(f"Vault initialized at {vault_path}")
    except FileExistsError as e:
        raise click.ClickException(str(e))

    if topic_list == []:
        click.echo(
            f"warning: vault created with no topics. Add topics under the "
            f"'topics:' list in {vault_path}/wiki.yaml (and create the "
            f"matching folder); until a default_topic is set there, ingest "
            f"into this vault needs --topic.", err=True)
    elif topic_list:
        for t in topic_list:
            declaring = sorted(
                n for n, e in others.items() if t in _vault_topics(e))
            if declaring:
                names = ", ".join(declaring)
                click.echo(
                    f"warning: topic '{t}' is also declared by vault(s) "
                    f"{names}; an unqualified --topic {t} will need --vault "
                    f"NAME or a NAME:{t} prefix.", err=True)


@cli.group()
def vault():
    """Manage the named vault registry."""


@vault.command("list")
def vault_list():
    """List configured vaults: name, kind, target, reachability, declaring
    config, and a * marker on the default vault. Read-only."""
    from agent_wiki.config import backend_for_entry, load_effective_config
    from agent_wiki.registry import resolve_default_vault

    registry, default_vault = load_effective_config()
    if not registry:
        click.echo("No vaults configured. Run 'awiki init <path>' first.")
        return

    try:
        default_name = resolve_default_vault(registry, default_vault).name
    except click.UsageError:
        default_name = None  # ambiguous or unset: no marker

    name_width = max(len(n) for n in registry)
    target_width = max(
        len(entry.url or str(entry.path)) for entry in registry.values())
    for name in sorted(registry):
        entry = registry[name]
        if entry.url:
            kind, target = "remote", entry.url
            try:
                backend_for_entry(entry).status()
                reach = "ok"
            except Exception:
                reach = "unreachable"
        else:
            kind, target = "local", str(entry.path)
            reach = (
                "ok" if (entry.path / "wiki.yaml").is_file() else "unreachable"
            )
        marker = "*" if name == default_name else " "
        click.echo(
            f"{marker} {name:<{name_width}}  {kind:<6}  "
            f"{target:<{target_width}}  {reach:<11}  {entry.origin or '-'}"
        )


@vault.command("add")
@click.argument("name")
@click.argument("target")
@click.option("--token", default=None, help="Bearer token for a remote (url) vault.")
def vault_add(name, target, token):
    """Register an existing vault as NAME in the global config.

    TARGET is a local vault directory (must contain wiki.yaml) or a remote
    base URL (http[s]://host[:port], no path). The first write that outgrows
    the legacy single-vault form rewrites the config to the vaults: schema.
    Any validation failure writes nothing."""
    from pathlib import Path
    from urllib.parse import urlparse
    from agent_wiki.page import slugify
    from agent_wiki.config import (
        load_user_config, migrate_to_vaults_schema, save_user_config)
    from agent_wiki.registry import parse_registry

    if not name or slugify(name) != name:
        raise click.UsageError(
            f"vault name {name!r} is not slug-legal; use lowercase letters, "
            f"digits, and hyphens (e.g. {slugify(name) or 'my-vault'!r})."
        )
    config = load_user_config()
    if name in parse_registry(config):
        raise click.UsageError(
            f"vault '{name}' is already configured; pick another name or "
            f"hand-edit the config to change it."
        )

    parsed = urlparse(target)
    if parsed.scheme:
        if (parsed.scheme not in ("http", "https") or not parsed.netloc
                or parsed.path.strip("/") or parsed.query or parsed.fragment):
            raise click.UsageError(
                f"remote vault URL {target!r} is malformed; expected "
                f"http[s]://host[:port] with no path."
            )
        entry = {"url": target.rstrip("/")}
        if token is not None:
            entry["token"] = token
    else:
        path = Path(target).expanduser().resolve()
        if not (path / "wiki.yaml").is_file():
            raise click.UsageError(
                f"{path} is not a vault: no wiki.yaml found. Initialize one "
                f"with 'awiki init' or point at an existing vault."
            )
        if token is not None:
            raise click.UsageError("--token applies only to remote (url) vaults.")
        entry = {"path": str(path)}

    config = migrate_to_vaults_schema(config)
    config.setdefault("vaults", {})[name] = entry
    save_user_config(config)
    click.echo(f"Registered vault '{name}' -> {entry.get('url', entry.get('path'))}")


@vault.command("trust")
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
def vault_trust(directory):
    """Trust DIRECTORY's context-local .agent-wiki/config.yaml.

    A local config is honored only when its directory is on the trust
    allowlist in the global config; this records DIRECTORY there."""
    from pathlib import Path
    from agent_wiki.config import load_user_config, save_user_config

    resolved = str(Path(directory).expanduser().resolve())
    config = load_user_config()
    trusted = [str(t) for t in (config.get("trusted_dirs") or [])]
    if resolved in trusted:
        click.echo(f"{resolved} is already trusted.")
        return
    trusted.append(resolved)
    config["trusted_dirs"] = trusted
    save_user_config(config)
    click.echo(f"Trusted {resolved} for local configs.")


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
    """Ingest files or URLs into the wiki vault.

    With multiple vaults configured, --topic routes to the unique vault
    declaring that topic (a doubly-declared topic is a hard error); --vault
    or a vault: prefix on the topic always beats topic routing; an
    undeclared topic or no topic falls back to the default vault."""
    svc, topic = _ingest_service(topic)
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
    try:
        svc, ref = _dispatch_ref(name, _raw_name_probe)
        out = svc.reingest(ref, force=force)
    except PageDriftError as e:
        if e.diff:
            click.echo(e.diff, err=True)
        raise click.ClickException(str(e))
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))
    click.echo(f"Reingested {ref} -> {out['page']}")
    # Surface where the page landed on stderr (REQ-12): a local absolute path, or
    # for a remote vault the server URL + vault-relative path. stdout stays clean.
    click.echo(svc.describe_location(out["page"]), err=True)


def _merged_search(registry, query, topic, limit, partial_limit=5):
    """Search every configured vault and merge into one coverage-ranked
    result dict (same shape _service().search returns), with every hit path
    vault-qualified. Unreachable vaults are skipped with a stderr note. A
    vault-qualified --topic narrows the sweep to that vault."""
    from agent_wiki.config import backend_for_entry
    from agent_wiki.resolve import split_vault_ref

    entries = [registry[name] for name in sorted(registry)]
    if topic:
        topic_entry, topic_ref = split_vault_ref(topic, registry)
        if topic_entry is not None:
            entries = [topic_entry]
            topic = topic_ref

    all_pool, partial_pool, total = [], [], 0
    for entry in entries:
        try:
            out = backend_for_entry(entry).search(
                query, topic=topic, limit=limit)
        except Exception as e:
            click.echo(f"skipping vault '{entry.name}': {e}", err=True)
            continue
        for r in out["all"] + out["partial"]:
            r["path"] = f"{entry.name}:{r['path']}"
        all_pool.extend(out["all"])
        partial_pool.extend(out["partial"])
        total += out["total"]

    # The same ordering _rank uses, applied across vaults.
    def rank_key(r):
        return (-r["coverage"], -len(r["matches"]), r["title"])

    all_pool.sort(key=rank_key)
    partial_pool.sort(key=rank_key)
    shown_all = all_pool[:limit]
    shown_partial = partial_pool[:partial_limit]
    shown = len(shown_all) + len(shown_partial)
    return {
        "all": shown_all,
        "partial": shown_partial,
        "total": total,
        "shown": shown,
        "truncated": shown < total,
    }


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
    """Search the wiki vault.

    With multiple vaults configured the search spans all of them and prints
    one merged coverage-ranked list whose paths carry a vault: qualifier that
    pastes straight into show/raw/reingest; --topic accepts the same
    qualifier to narrow the sweep to one vault's topic."""
    from agent_wiki.config import load_registry, resolve_vault_override

    registry = load_registry() if resolve_vault_override() is None else {}
    if len(registry) > 1:
        out = _merged_search(registry, query, topic, limit)
    else:
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
    """Print a wiki page (or any vault file) by its vault-relative path.

    With multiple vaults configured the path may carry a vault: prefix; an
    unqualified path resolves across all vaults (unique match wins, ambiguity
    is a hard error listing the qualified candidates). An extensionless page
    path falls back to <path>.md, with a warning on stderr."""
    svc, ref = _dispatch_ref(path, _show_probe)
    try:
        content, shown = _show_with_md_fallback(svc, ref)
    except (ValueError, FileNotFoundError) as e:
        raise click.ClickException(str(e))
    if shown != ref:
        click.echo(
            f"warning: '{ref}' resolved to '{shown}'; wiki page paths "
            f"include the .md extension.", err=True)
    click.echo(content, nl=False)
    # Surface where the content was read from on stderr (REQ-13): a local absolute
    # path, or for a remote vault the server URL + vault-relative path. stdout stays
    # byte-identical to the file so skills that parse show output verbatim are unaffected.
    click.echo(svc.describe_location(shown), err=True)


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
    try:
        svc, ref = _dispatch_ref(name, _raw_name_probe)
        if isinstance(svc, RemoteVaultService):
            click.echo(svc.describe_location(f"raw/{ref}"))
            click.echo(
                "remote vault: this raw source lives on the server and is not "
                "directly editable locally.", err=True)
            return
        raw_path = resolve_raw(svc.vault_path, ref)
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
    """Rebuild the wiki index.

    With multiple vaults configured, every vault's index is rebuilt serially
    with output sectioned per vault; --vault narrows to one. A vault that
    cannot support the operation is skipped with a printed notice."""
    from agent_wiki.config import (
        backend_for_entry, load_registry, resolve_vault_override)

    registry = load_registry() if resolve_vault_override() is None else {}
    if len(registry) <= 1:
        _service().rebuild_index()
        click.echo("Index rebuilt.")
        return

    for position, name in enumerate(sorted(registry)):
        if position:
            click.echo("")
        click.echo(f"vault: {name}")
        try:
            backend_for_entry(registry[name]).rebuild_index()
        except Exception as e:
            click.echo(f"  skipped: {' '.join(str(e).split())}")
            continue
        click.echo("  Index rebuilt.")


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
    """Audit the wiki vault for issues.

    With multiple vaults configured, every vault is checked serially with
    output sectioned per vault; --vault narrows to one. A vault that cannot
    support the operation is skipped with a printed notice — skips alone
    never change the exit code."""
    from agent_wiki.config import (
        backend_for_entry, load_registry, resolve_vault_override)

    registry = load_registry() if resolve_vault_override() is None else {}
    if len(registry) <= 1:
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
        return

    strict_hit = False
    for position, name in enumerate(sorted(registry)):
        if position:
            click.echo("")
        click.echo(f"vault: {name}")
        try:
            issues = backend_for_entry(registry[name]).lint(refetch=refetch)
        except Exception as e:
            msg = " ".join(str(e).split())
            click.echo(f"  skipped: {msg}")
            continue
        if not issues:
            click.echo("  No issues found.")
            continue
        for issue in issues:
            label = LINT_LABELS.get(issue["type"], issue["type"].upper())
            click.echo(f"  [{label}] {issue['detail']}  ({issue['path']})")
        click.echo(f"  {len(issues)} issue(s) found.")
        strict_hit = strict_hit or any(
            i["type"] == "tag_audit" for i in issues)

    if strict and strict_hit:
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
@click.option("--detach", is_flag=True, default=False,
              help="Run the sync in a background process and return at once; "
                   "its output goes to a per-vault log in the awiki state dir")
@click.option("--detached-worker", "detached_worker", is_flag=True, default=False,
              hidden=True,
              help="Internal: this process is the background half of --detach; "
                   "try the vault lock once and skip if another sync holds it")
def sync(source, since, dry_run, include_live, detach, detached_worker):
    """Discover new conversations from configured sources and ingest them."""
    if detach:
        _detach_sync(source=source, since=since, dry_run=dry_run,
                     include_live=include_live)
        return
    kwargs = {}
    if detached_worker:
        # Try the lock once, and reset this run's log only once we hold it,
        # so a sweep that skips (or a later parent) never wipes a running
        # sweep's output. The parent opened our stdout in append mode.
        kwargs["try_once"] = True
        kwargs["on_locked"] = _reset_detached_log
    try:
        out = _service().sync(
            source=source, since=since, dry_run=dry_run, include_live=include_live,
            **kwargs,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    if out.get("busy"):
        click.echo("sync already running for this vault, or another awiki process holds "
                   "its lock; skipping this sweep")
        return

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


def _reset_detached_log() -> None:
    """Truncate this vault's sync log and stamp the run header.

    Called by the detached worker once it holds the vault locks. stdout is the
    same file opened in append mode, so after truncation the run's output
    lands right behind the header.
    """
    from datetime import datetime
    from agent_wiki.locking import run_log_path

    try:
        vault_path = get_vault_path()
        log = run_log_path(vault_path, "sync")
        sys.stdout.flush()
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(
                f"awiki sync started {datetime.now().isoformat(timespec='seconds')} "
                f"vault={vault_path}\n"
            )
    except Exception:
        pass  # log housekeeping must never abort the sync itself


def _detach_sync(source, since, dry_run, include_live) -> None:
    """Spawn ``awiki sync`` as a detached background process and return.

    The child runs in its own session with stdin closed and stdout/stderr
    appended to a per-vault log file in the awiki state dir. The log is reset
    only by whoever can prove no sweep is writing it: the parent truncates it
    if it can take the vault lock for an instant (so runs that never reach the
    lock, such as dry runs or config errors, still replace rather than grow
    it), and the worker truncates it again once it holds the lock for real.
    A worker that finds the lock held only appends its one-line notice.

    Everything this prints goes to stderr: agent startup hooks call this, and
    Claude Code feeds a SessionStart hook's stdout into the model's context.
    This function must not raise either, since a failed spawn must not become
    a failed agent start: any error is reported on stderr and the command
    still exits 0.
    """
    try:
        import subprocess
        from agent_wiki.config import (
            _default_entry, _override_entry_or_raise, _raw_vault_override,
        )
        from agent_wiki.locking import file_lock, run_log_path

        entry = _override_entry_or_raise() or _default_entry()
        if entry.url:  # url wins over a path, as in config.backend_for_entry
            click.echo(
                f"sync --detach: vault '{entry.name}' is remote ({entry.url}); the "
                "server owns its session sources, so there is nothing to sweep from "
                "this machine. Run `awiki sync` for a blocking server-side sync.",
                err=True,
            )
            return
        vault_path = get_vault_path()
        log = run_log_path(vault_path, "sync")
        argv = [sys.executable, "-c", "from agent_wiki.cli import cli; cli()"]
        override = _raw_vault_override()
        if override:
            argv += ["--vault", override]
        argv += ["sync", "--detached-worker"]
        if source:
            argv += ["--source", source]
        if since:
            argv += ["--since", since]
        if dry_run:
            argv.append("--dry-run")
        if include_live:
            argv.append("--include-live")
        try:
            with file_lock(vault_path, "log", timeout=0):
                open(log, "w", encoding="utf-8").close()
        except TimeoutError:
            pass  # a sweep is writing it; leave its output alone
        with open(log, "a", encoding="utf-8") as fh:
            # PYTHONSAFEPATH keeps the agent's cwd off the child's sys.path,
            # so a project's own yaml.py/json.py cannot shadow a dependency.
            env = {**os.environ, "PYTHONSAFEPATH": "1"}
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True, env=env,
            )
        click.echo(f"sync detached (pid {proc.pid}); log: {log}", err=True)
    except Exception as e:
        click.echo(f"sync --detach could not start a background sync: {e}", err=True)


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
    """Inspect the vault and offer to fix drift from current schema.

    With multiple vaults configured, a plain doctor runs a read-only
    diagnostics sweep across every vault (sectioned output, announced skips,
    exit 0). Fix intent (--fix/--dry-run/--reconcile-raw) keeps today's flow
    against the default vault; --vault narrows to any one vault."""
    _repair_stale_config_if_needed(fix, dry_run)
    from agent_wiki.config import load_registry, resolve_vault_override
    registry = load_registry() if resolve_vault_override() is None else {}
    if len(registry) > 1 and not (fix or dry_run or reconcile_raw):
        _doctor_diagnostics_sweep(registry)
        return
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


def _doctor_diagnostics_sweep(registry):
    """Read-only doctor diagnostics across every configured vault: one
    labeled section per vault, findings listed but never fixed, unsupported
    or unreachable vaults skipped with a printed notice. Always exits 0."""
    from agent_wiki.config import backend_for_entry
    from agent_wiki.doctor import run_checks
    from agent_wiki.remote import RemoteVaultService

    for position, name in enumerate(sorted(registry)):
        if position:
            click.echo("")
        click.echo(f"vault: {name}")
        try:
            svc = backend_for_entry(registry[name])
            if isinstance(svc, RemoteVaultService):
                out = svc.doctor(fix=False, dry_run=True)
                findings = [
                    (f["name"], f["detail"], f["description"])
                    for f in out["findings"]
                ]
            else:
                findings = [
                    (f.check.name, f.detail, f.check.description)
                    for f in run_checks(svc.vault_path)
                ]
        except Exception as e:
            click.echo(f"  skipped: {' '.join(str(e).split())}")
            continue
        if not findings:
            click.echo("  No issues found.")
            continue
        click.echo(f"  Found {len(findings)} issue(s):")
        for check_name, detail, description in findings:
            click.echo(f"  [{check_name}] {detail}")
            click.echo(f"    → {description}")
        click.echo("  (diagnostics only — pass --vault NAME to fix this vault)")


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
        from agent_wiki.config import load_registry, resolve_vault_override
        registry = load_registry() if resolve_vault_override() is None else {}
        if len(registry) > 1:
            from agent_wiki.context import run_context_multi
            block = run_context_multi(prompt, registry)
        else:
            block = _service().context(prompt)
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
    """Install / uninstall / inspect awiki's hooks (startup sweep, auto-context) for an agent CLI."""
    pass


@hook_group.command("install")
@click.option("--agent", default="claude", help="Target agent CLI (claude, pi, opencode, manual).")
@click.option("--config-path", default=None, type=click.Path(),
              help="Override the agent's settings file (claude), extension file (pi) or "
                   "plugin file (opencode) path, for tests or non-default installs.")
@click.option("--only", default=None, type=click.Choice(["context", "sweep"]),
              help="Install just one hook: 'context' (auto-context on each prompt) "
                   "or 'sweep' (detached session sync on agent start). Default: both.")
def hook_install(agent, config_path, only):
    """Wire awiki's auto-context hook and startup session sweep into an agent."""
    from agent_wiki.hooks import get_backend
    try:
        backend = get_backend(agent)
    except KeyError as exc:
        raise click.ClickException(str(exc))
    path = Path(config_path) if config_path else None
    try:
        msg = backend["install"](config_path=path, only=only)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    click.echo(msg)


@hook_group.command("uninstall")
@click.option("--agent", default="claude")
@click.option("--config-path", default=None, type=click.Path())
@click.option("--only", default=None, type=click.Choice(["context", "sweep"]),
              help="Remove just one hook: 'context' or 'sweep'. Default: both.")
def hook_uninstall(agent, config_path, only):
    """Remove awiki's hooks from the target agent's settings."""
    from agent_wiki.hooks import get_backend
    try:
        backend = get_backend(agent)
    except KeyError as exc:
        raise click.ClickException(str(exc))
    path = Path(config_path) if config_path else None
    try:
        msg = backend["uninstall"](config_path=path, only=only)
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
