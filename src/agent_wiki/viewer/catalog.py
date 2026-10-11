"""The viewer's catalog: which vaults are served, and each vault's pages.

`build_vault_catalog` is pure: it decides from a parsed registry and an
optional override entry alone, so tests drive it without any config.
`load_vault_catalog` feeds it the real registry and override readers from
`agent_wiki.config`. `served_vault_name` names the one vault an API process
serves: its registry name, or 'local' for a path outside the registry.

`index_vault` lists one vault's pages per topic plus its title-to-URL map, and
`resolve_page` maps a URL path back to a page file, admitting only files under
a wiki.yaml topic folder. The conversations topic is not served: its session
pages are not listed and do not resolve. A page's URL path is its vault-relative path without
`.md` (`research/foo`, `research/sub/bar`); the routes serve it under
`/vaults/<name>/`.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agent_wiki.config import load_vault_config
from agent_wiki.page import parse_page


# The name a bare-path override is served under: it has no registry name.
OVERRIDE_PATH_NAME = "local"


@dataclass(frozen=True)
class VaultCatalog:
    """The served vaults ({name: path}, sorted by name), the number of remote
    entries skipped (the viewer reads local files only), and the names of
    local vaults whose directory does not exist."""

    vaults: dict = field(default_factory=dict)
    skipped_remote: int = 0
    unavailable: tuple = ()


def build_vault_catalog(registry: dict, override=None) -> VaultCatalog:
    """Build the catalog from a registry ({name: VaultEntry}) and an override.

    With no override every registry entry is considered. An override narrows
    to that one entry: a name-matched override (the registry's own entry) keeps
    its registry name, a path override is served as 'local'. An entry with a
    url is remote and skipped, even with a path beside it (url wins, as at
    backend selection); a local entry whose path is missing is unavailable."""
    if override is None:
        entries = [registry[name] for name in sorted(registry)]
        names = [entry.name for entry in entries]
    elif registry.get(override.name) == override:
        entries, names = [override], [override.name]
    else:
        entries, names = [override], [OVERRIDE_PATH_NAME]

    vaults = {}
    skipped_remote = 0
    unavailable = []
    for name, entry in zip(names, entries):
        if entry.is_remote:
            skipped_remote += 1
        elif not entry.path.is_dir():
            unavailable.append(name)
        else:
            vaults[name] = entry.path
    return VaultCatalog(vaults=vaults, skipped_remote=skipped_remote,
                        unavailable=tuple(unavailable))


def load_vault_catalog() -> VaultCatalog:
    """Build the catalog from the effective registry and the active override
    (--vault or AWIKI_VAULT), read once at startup."""
    from agent_wiki.config import load_registry, resolve_vault_override
    return build_vault_catalog(load_registry(), resolve_vault_override())


def vault_name_for_path(registry: dict, vault_path) -> str:
    """The name the vault at `vault_path` is served under: the first registry
    name, in sorted order, whose entry has that path (both resolved), else
    'local', the name a vault outside the registry is served under. Pure:
    tests pass a registry ({name: VaultEntry}) directly."""
    target = _resolved(Path(vault_path))
    for name in sorted(registry):
        entry = registry[name]
        if entry.path is not None and _resolved(Path(entry.path)) == target:
            return name
    return OVERRIDE_PATH_NAME


def served_vault_name(vault_path) -> str:
    """`vault_name_for_path` against the effective registry, read once."""
    from agent_wiki.config import load_registry
    return vault_name_for_path(load_registry(), vault_path)


def _resolved(path: Path) -> Path:
    """`path` with ~ expanded and symlinks followed; as given when it cannot
    be resolved, such as a symlink loop."""
    try:
        return path.expanduser().resolve()
    except (RuntimeError, OSError):
        return path.expanduser()


@dataclass(frozen=True)
class PageRecord:
    """One page as the listings show it. Dates are strings ('' when absent);
    `url_path` is vault-relative without .md, `path` the file on disk."""

    title: str
    topic: str
    tags: tuple
    created: str
    updated: str
    url_path: str
    path: Path


@dataclass(frozen=True)
class VaultIndex:
    """A vault's pages: {topic: (PageRecord, ...)} for each wiki.yaml topic
    that exists as a folder, in wiki.yaml order, pages sorted by path; and
    {title: url_path}, where the first page in that order wins a shared
    title."""

    topics: dict = field(default_factory=dict)
    titles: dict = field(default_factory=dict)


def _inside(path: Path, topic_root: Path) -> bool:
    """Whether `path`, with symlinks followed, is a file under `topic_root`.
    `topic_root` is the resolved vault joined with the topic name, so a topic
    folder that is itself a symlink out of the vault admits nothing. A path
    that cannot be resolved, such as a symlink loop, is not inside."""
    try:
        real = path.resolve()
    except (RuntimeError, OSError):
        return False
    return real.is_file() and real.is_relative_to(topic_root)


def is_utf8(text: str) -> bool:
    """Whether `text` encodes as UTF-8. A file name that is not UTF-8 reaches
    Python with surrogate escapes, and the viewer cannot put it in a URL or a
    page."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def served_topics(vault_dir: Path) -> list:
    """The wiki.yaml topics the viewer serves, in wiki.yaml order: every topic
    except the conversations topic, read with the default conversation ingest
    uses ('sessions')."""
    config = load_vault_config(vault_dir)
    sessions = (config.get("conversations") or {}).get("topic", "sessions")
    return [topic for topic in config.get("topics", []) if topic != sessions]


def index_vault(vault_dir: Path) -> VaultIndex:
    """Index one vault's topic pages. The page rule is rebuild_index's (a .md
    file with frontmatter anywhere under a wiki.yaml topic folder, title
    falling back to the file stem), plus the safety rule `resolve_page`
    applies, so every listed page is one the viewer will serve."""
    vault_dir = Path(vault_dir)
    root = vault_dir.resolve()
    topics = {}
    titles = {}
    for topic in served_topics(vault_dir):
        topic_dir = vault_dir / topic
        if not topic_dir.is_dir():
            continue
        records = []
        for md_file in sorted(topic_dir.rglob("*.md")):
            url_path = md_file.relative_to(vault_dir).as_posix()[:-3]
            if not is_utf8(url_path) or not _inside(md_file, root / topic):
                continue
            # A page whose frontmatter cannot be read (bad YAML, a block that
            # is not a mapping, not UTF-8, no read permission) is left out,
            # so one broken file does not take every listing and page view
            # down with it.
            try:
                meta = parse_page(md_file)["meta"]
            except (yaml.YAMLError, UnicodeDecodeError, OSError):
                continue
            if not meta or not isinstance(meta, dict):
                continue
            tags = meta.get("tags") or []
            record = PageRecord(
                title=str(meta.get("title", md_file.stem)),
                topic=topic,
                tags=(tuple(str(t) for t in tags)
                      if isinstance(tags, list) else ()),
                created=str(meta.get("created", "")),
                updated=str(meta.get("updated", "")),
                url_path=url_path,
                path=md_file,
            )
            records.append(record)
            titles.setdefault(record.title, record.url_path)
        topics[topic] = tuple(records)
    return VaultIndex(topics=topics, titles=titles)


def resolve_page(vault_dir: Path, url_path: str) -> "Path | None":
    """The page file a URL path names, or None when the viewer must not serve
    it. The first segment must be a served topic and at least one segment
    must follow; empty, '.' and '..' segments and NUL bytes are refused; the
    file is `<url_path>.md` and must stay under that topic folder with
    symlinks followed. So raw/, wiki.yaml, log.md, index.md, non-topic
    folders and non-.md files are never reachable. Frontmatter is not checked
    here."""
    vault_dir = Path(vault_dir)
    parts = url_path.split("/")
    # A NUL byte would make path resolution raise rather than miss.
    if ("\0" in url_path or len(parts) < 2
            or any(part in ("", ".", "..") for part in parts)):
        return None
    topic = parts[0]
    if topic not in served_topics(vault_dir):
        return None
    candidate = vault_dir.joinpath(*parts[:-1], parts[-1] + ".md")
    if not _inside(candidate, vault_dir.resolve() / topic):
        return None
    return candidate
