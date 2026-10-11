# Changelog

All notable changes to Agent Wiki are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The single source of truth for the version is `__version__` in
`src/agent_wiki/__init__.py`; `awiki --version` and the build metadata both
derive from it. Release tags begin at `v0.5.0`; earlier versions and dates
below are reconstructed from the commits that bumped `__version__`.

## [0.12.1]

### Added
- **Content-Security-Policy on viewer pages.** Every viewer page is sent with
  `default-src 'none'`, `img-src 'self' data: https:`, `base-uri 'none'`,
  `form-action 'self'` and `frame-ancestors 'none'`. The inline style block
  carries a nonce that is new on each response, and the three `text-align`
  attributes markdown tables use are allowed by hash. The `/v1` API is
  unchanged.

### Changed
- **The top bar shows the user name and a Settings link.** The name is plain
  text; a separate Settings link leads to the user page.
- **`auth_trusted_proxies` counts with proxy sign-in off.** It now decides
  whose `X-Forwarded-Proto: https` marks the session cookie `Secure` whether
  or not `auth_proxy` is on, so a TLS proxy on another host gets Secure
  cookies. `serve` checks the list whenever it is set and refuses a wildcard
  even with `auth_proxy` off.

### Fixed
- **Login CSRF.** The sign-in form carries a token that matches a
  short-lived `awiki_signin` cookie (SameSite=Strict); a sign-in post without
  a matching pair is refused with 403 before any password check, so another
  site cannot sign a browser in to an account of its choosing.
- **Signed-in pages are not cached.** Every page that carries the session
  secret in its forms is sent with `Cache-Control: private, no-store`.
- **An unknown stored role grants nothing, everywhere.** A grant or scope row
  holding a role the ladder does not know reads as `none` in one place, so
  the viewer no longer answers 400 for it and the API and the viewer agree.
- **A grant on `local` is not reported as stale.** Under a bare-path
  override the served vault is `local`; `serve` and `awiki user list` now
  count that name as known.
- **`create_app` names a vault by its own path.** An app built for a path
  other than the configured vault is served as `local` and no longer borrows
  the configured vault's name and grants. The README now states the naming
  rule: every path override is `local`, even a registered vault's path.

## [0.12.0]

**Breaking:** `awiki serve` now has named users, and tokens belong to them.
The `awiki token` command group is removed. Tokens kept in `server.yaml` must
be moved into the new user store with `awiki user token migrate --to <user>`,
and `awiki serve` refuses to start until that is done. Tokens no longer carry a
role of their own: a token acts with its owner's per-vault grants, optionally
narrowed by a scope. The web viewer now requires sign-in, and `/docs`,
`/redoc` and `/openapi.json` are no longer served. To upgrade, on the server
host run `awiki user add <you>`, `awiki user grant <you> '*' admin` and
`awiki user token migrate --to <you>`, then restart `awiki serve`. Migrated
tokens keep their secrets, so remote clients need no change.

### Added
- **Users and per-vault grants.** `awiki user add|list|passwd|remove` manages
  the server's users, and `awiki user grant|ungrant` sets each user's grant
  map of vault name to role on the ladder `none < reader < writer < admin`.
  The entry `'*'` matches every vault, including ones registered later, and
  a vault's own entry wins over it. A password is only ever typed at a
  prompt; a user without one signs in only through a reverse proxy. Users,
  grants, tokens, sessions and sign-in failures live in one SQLite file,
  `state.db`, in the config dir, and a change made with the CLI applies to a
  running server at its next request. `user list` and `serve` flag a grant
  or scope that names a vault not in the registry, and `serve` warns when
  the store has no users. No new dependency: passwords are hashed with
  `hashlib.scrypt`.
- **Per-user tokens with scopes.** `awiki user token add <user> <name>
  [--scope VAULT=ROLE ...]` prints an `awp_` secret once and keeps only its
  SHA-256 hash and last 4 characters; `user token list` and `user token
  revoke` manage them. A scope can only narrow: the token's role on a vault
  is the lower of its owner's grant and its scope there, so
  `--scope '*=writer' --scope personal=none` writes everywhere except
  `personal`, and `--scope '*=reader'` is read-only. A token with no scope
  acts with its owner's grants.
- **`awiki user token migrate --to <user>`** moves the tokens in
  `server.yaml` into the user store with their names and secrets unchanged
  and the scope `'*'` set to their old role, then removes the `tokens` key
  and keeps the rest of the file. It imports nothing twice.
- **Viewer sign-in.** The viewer has a password sign-in page and server-side
  sessions in the `awiki_session` cookie (HttpOnly, SameSite=Lax, Secure over
  https), which end 12 hours after their last use. Five failed password
  sign-ins for one user within 15 minutes lock that user's password sign-in
  for 15 minutes; the lock survives a restart and does not affect tokens or
  proxy sign-in. Every page shows who is signed in, a link to the user page
  and a sign-out button, and every form that changes state carries the
  session secret.
- **Reverse-proxy sign-in.** With `auth_proxy: true` in `server.yaml`, the
  viewer signs in the user named in `auth_proxy_header` (default
  `X-WEBAUTH-USER`) when the socket peer is in `auth_trusted_proxies`
  (default `127.0.0.1` and `::1`). The header must name an existing user; no
  account is created. It never authenticates the `/v1` API. `serve` refuses
  to start on an empty trusted list, `*`, a range that holds every address,
  or an entry that does not parse.
- **User page.** `/user` holds the theme choice, the signed-in user's own
  tokens (make with an optional scope, list, revoke) and, for a password
  session, a change-password form.
- **Viewer favicon.** Every viewer page shows the Agent Wiki icon: a bracket
  pair around two text lines, white on an indigo tile. The viewer serves
  `/favicon.svg`, `/favicon.ico` (16, 32 and 48 px) and
  `/apple-touch-icon.png` (180 px) with no token. The source SVGs of the mark
  and the tile are in `docs/brand/`.

### Changed
- **New header mark.** The viewer header shows the bracketed page mark `[=]`
  in place of `[[ ]]`.
- **The web viewer hides session pages.** The conversations topic
  (`conversations.topic` in wiki.yaml, `sessions` by default) is left out of
  the viewer's topic tabs, page counts, tag pages and search, and its page and
  topic URLs return 404.
- **The API checks each vault's role.** Every `/v1` route keeps its minimum
  role and checks it against the token's effective role on the served vault,
  matched by its registry name (`local` for a bare-path override). No token
  or an unknown one gets 401; too low a role, including `none`, gets 403.
- **The viewer requires sign-in.** There is no anonymous access: every viewer
  page except the sign-in page and the favicons redirects to `/signin`
  without a session. A signed-in user sees only the vaults they can read; any
  other vault answers 404 like one that does not exist.
- **The theme choice moved** from the viewer header to the user page.
- `serve` no longer warns when the viewer is on and the bind address is not
  loopback, since the viewer now requires sign-in.
- uvicorn runs with forwarded headers off, so a client cannot claim another
  address; `X-Forwarded-Proto` is read only from a trusted proxy peer.

### Removed
- **`awiki token add|list|revoke`.** Use `awiki user token` and, for an
  existing install, `awiki user token migrate`. `serve` refuses to start
  while `server.yaml` still holds tokens.
- **`/docs`, `/redoc` and `/openapi.json`.** The server no longer serves
  FastAPI's generated API pages.

## [0.11.0]

### Added
- **Read-only web viewer.** `awiki serve --viewer` also serves an HTML viewer
  beside the JSON API, for every local vault in the registry (or only the
  `--vault` / `AWIKI_VAULT` override, served as `local` when it is a bare
  path). `/` lists the vaults and counts the skipped remote ones. Every page
  has a header with the Agent Wiki mark and, on a vault's pages, the vault
  name and a search box for that vault. A vault's pages show topic tabs with
  page counts and a breadcrumb. The vault home lists the 20 most recent pages
  beside a search and About sidebar; topic and tag pages list 50 pages per
  screen; a page view shows the rendered markdown in a box headed by the
  page's path, with About, Tags and Sources in a sidebar, and leaves out a
  first body heading that repeats the title. `[[Title]]` links resolve by
  frontmatter title, the same rule lint uses, and an unknown title is marked
  missing. `/search` searches the chosen vaults with the same ranking as
  `awiki search`, marks the query terms in titles and snippets, and shows
  only pages the viewer serves, so no result leads to a 404.
- **Viewer theme.** The viewer has its own token stylesheet: every colour is
  one light and dark pair, with an indigo accent, and the rendered body
  (github-markdown-css, MIT, with its two colour sets merged onto the same
  tokens) follows it. A theme switch in the header picks Auto, Light or Dark;
  the choice is kept for a year in the `awiki_theme` cookie, and Auto follows
  the browser. Page bodies are sanitised, pages use no JavaScript and carry
  their CSS inline, and only topic-folder pages are served: `raw/`, config
  files and paths outside the vault return 404. The viewer needs no token,
  so `serve` warns when it is on and the bind address is not loopback. The
  `/v1` API is unchanged.

### Changed
- `awiki search` across several vaults now ranks through one shared function.
  Its output is unchanged.

### Fixed
- **Claude Code session titles.** A session page was titled from the first
  user turn, which Claude Code often fills with its local-command caveat or a
  slash command, so most titles read `<local-command-caveat>Caveat: ...`. Sync
  now skips those harness turns and lines that hold only a tag, and with no
  typed prompt uses the first slash command (`/name args`). The new `awiki
  doctor` check `session-titles` retitles existing sessions: it derives the
  title again from the turns in each `raw/sessions/` bundle and rewrites the
  bundle's title field and the session page's title and heading. It touches
  only claude-code bundles whose stored title starts with a tag, and it runs
  under a plain `--fix` and from a remote client.

## [0.10.0]

### Added
- **Sliced page reads.** `awiki show` gains four flags so a long log or watch
  page can be read in parts instead of whole. `--outline` prints only the
  page's heading lines, verbatim and in file order. `--section TEXT` prints the
  first section whose heading contains TEXT (case-insensitive substring),
  through the line before the next heading of the same or a higher level, so
  subsections come with it; other matching headings are listed on stderr, and
  no match exits non-zero. `--head N` and `--tail N` keep the first or last N
  child sections of that selection, or of the page's top-level sections when no
  section is given, always keeping the heading and the text above the first
  child. Headings inside fenced code blocks are never counted, any of the four
  flags drops the YAML frontmatter, and slicing runs client-side so local and
  remote vaults print identically. Flagless `awiki show` is unchanged and stays
  byte-identical to the file.
- **Configurable SIZE lint threshold.** An optional `lint: page_max_lines`
  integer in `wiki.yaml` sets the page length that trips a **SIZE** finding,
  read through the one vault-config reader. A malformed block, an unrecognised
  key inside it, or a value that is not a whole number of at least 1 makes
  `awiki lint` fail with a message naming the key - in a multi-vault run too,
  where a config error is fatal rather than a skip.

### Changed
- The **SIZE** lint default rose from 200 to 500 lines, so the finding lands on
  genuine split candidates rather than on ordinary pages.
- The agent-facing docs point at the sliced reads. The packaged `awiki-search`
  skill and the block `awiki guide` prints now name `--outline`, `--section`,
  `--head` and `--tail` and carry a sliced-read example, where both previously
  told an agent only to read a page whole. Run `awiki skills update` to refresh
  an installed skill; the guide block is static, so an already-pasted copy in an
  agent-memory file keeps working and can be replaced from `awiki guide --raw`.

## [0.9.1]

### Fixed
- Code comments and docstrings in `src/` and `tests/` no longer carry internal
  planning IDs (requirement, task, and decision references). The 0.9.0 build
  failed the release leak scan on them. No behaviour change.

## [0.9.0]

### Added
- **Ongoing session ingestion.** `awiki hook install --agent claude|pi|opencode`
  now wires a startup sweep into the agent: every agent start runs
  `awiki sync --detach`, so finished sessions from all sources flow into the
  vault without anyone running sync by hand. Claude Code gets a `SessionStart`
  hook (all sources, no matcher) beside the existing `UserPromptSubmit`
  context hook; pi gets an awiki-managed extension in
  `~/.pi/agent/extensions/` that handles `session_start` via `pi.exec`;
  OpenCode gets an awiki-managed plugin in `~/.config/opencode/plugins/` that
  handles `session.created` via Bun's `$`. Both file backends are
  marker-tagged, idempotent, and never delete a file they did not write.
  `--only context|sweep` narrows install/uninstall to one hook;
  `hook status` reports each hook separately; the `manual` backend prints the
  wiring for both hooks on all three hosts.
- **pi adapter.** `sources.pi` (default path `~/.pi/agent/sessions`) ingests
  pi session files (format v3): one file is one session, keyed by the header
  uuid, titled from the session name or first user message, with the last
  `model_change` as the model and tool calls counted. It appears everywhere
  sources are enumerated: `sync --source pi`, `adapt pi`, `awiki init`
  defaults, and doctor's source-path check.
- **`awiki sync --detach`.** Forks the sync into a detached background process
  and returns at once. The child's output goes to a per-vault log in the awiki
  state dir (beside the lock files, overwritten each run); it tries the vault
  lock once and exits cleanly if another sync holds it. Without `--detach` the
  blocking behaviour (10 s lock timeout, error on expiry) is unchanged. The
  sweep targets the default vault unless narrowed with `--vault`.
- **Cheap session keys.** Every adapter exposes `session_key(ref)` derived
  without parsing the transcript (filename for Claude Code and pi, row id for
  OpenCode, frontmatter header only for the drop zone), and the sync loop
  consults the state file by that key before calling the parser. An unchanged
  session now costs one `stat`; the state file format is unchanged and
  existing state is honoured without migration.

### Changed
- **`awiki hook install --agent claude` installs two hooks by default** (the
  new startup sweep plus the existing context hook). Pass `--only context` for
  the previous behaviour. `hook uninstall` likewise removes both unless
  narrowed.
- **OpenCode DB path resolution.** With no `sources.opencode.db_path`, the
  adapter now asks `opencode db path` (when the binary is on `PATH`) before
  falling back to `~/.local/share/opencode/opencode.db`, so the rename to
  `opencode-prod.db` in newer OpenCode releases no longer yields zero sessions.
  `awiki init` no longer writes a `db_path` for OpenCode, so fresh vaults get
  the resolved path; existing vaults keep whatever `db_path` they have.

### Fixed
- **`awiki sync --since 2026-04-01` no longer crashes.** A bare date parsed
  as a naive datetime and raised `TypeError` against the adapters' tz-aware
  mtimes; it is now read as local time.
- **`awiki sync --include-live` now reaches the adapters.** It used to
  mutate a config copy the sync loop never read, so the flag was a no-op for
  every source.
- **Doctor no longer flags or clobbers session pages.** The
  `raw-content-drift`, `render-hash-unstamped`, and `render-hash-divergent`
  checks skip pages whose frontmatter `type` is `conversation` (a session page
  is a pointer to its transcript by design), and `doctor --reconcile-raw`
  leaves `raw/sessions/` bundles byte-identical instead of overwriting each
  transcript with its pointer page.

## [0.8.1]

### Added
- **`awiki show` accepts extensionless page paths.** `awiki show topic/page`
  now falls back to `topic/page.md` when the literal path does not exist -
  in single-vault, multi-vault (unqualified and `vault:`-qualified), and
  remote configurations alike. A warning on stderr names the resolved path
  and reminds that page paths include the `.md` extension, so both humans
  and agents learn the canonical form; stdout stays byte-identical to the
  file. A miss on both forms still errors with the path as typed.

## [0.8.0]

### Added
- **Multiple named vaults.** `config.yaml` gains a `vaults:` map: each entry
  declares `path:` (local) or `url:` plus optional `token:` (remote), mixed
  freely in one config. A legacy `vault_path`/`server` config keeps working
  unchanged, read as a single vault named `main`; config writes stay in legacy
  form until the first write that needs more (a second vault, a named init, a
  remote entry), which rewrites the file to the `vaults:` schema.
- **Default vault resolution.** `default_vault` in the config picks the
  default; absent that, the vault named `main`, else a sole configured vault.
  No command writes the key - repoint it by hand-editing the config. There is
  deliberately no `awiki use`.
- **`--vault` / `AWIKI_VAULT` accept a vault name or a path.** A bare value
  matching a configured name narrows to that vault (local or remote); any
  other value is a config-free local vault at that path (`./` or an absolute
  path forces path interpretation).
- **Per-project vaults via `.agent-wiki/config.yaml`.** Discovered by walking
  up from the current directory to `$HOME`; the nearest one merges additively
  over the global config (local wins on name collision). Honored only after
  `awiki vault trust <dir>`; an untrusted local config is ignored with a
  one-line stderr notice. A relative `path:` in a local config resolves
  against the directory containing `.agent-wiki`, not the process cwd.
- **Cross-vault reads.** `search` spans all configured vaults with one merged
  coverage-ranked list whose paths carry a `vault:` qualifier that pastes
  straight into `show`/`raw`/`reingest`; those commands accept qualified and
  unqualified references (unique match wins, ambiguity is a hard error listing
  the candidates); an unqualified `raw`/`reingest` name probes local vaults
  only - qualify it to target a remote vault's server-side raws. The
  auto-context hook spans vaults, skips unreachable ones, and honors a
  per-vault `auto_context: false` opt-out (local vaults only - the wire
  contract exposes no such flag, so a reachable remote vault is always
  included).
- **Topic-routed writes.** `ingest --topic` lands in the unique vault whose
  `wiki.yaml` declares that topic; a doubly-declared topic is a hard error
  that `--vault` or a `vault:` prefix on the topic resolves.
- **Multi-vault maintenance.** `lint`, `doctor` diagnostics, and `index`
  sweep every vault with per-vault sections and announced skips;
  `lint --strict` exits nonzero on a TAG finding in any vault. Mutating
  maintenance (`tag`, `sync`, fixes) touches only the default vault unless
  narrowed.
- **Vault management.** `awiki vault list` (read-only registry view:
  name, kind, target, reachability, declaring config, default marker),
  `awiki vault add <name> <path|url>`, `awiki vault trust <dir>`, and
  `awiki init --name <name>`.
- **`awiki init --topics "a, b"`** seeds a new vault's topic list (the first
  listed topic becomes its `default_topic`). An init beside existing vaults no
  longer clones the standard default topics - duplicated topics made every
  unqualified `--topic` ambiguous across vaults; instead it prompts for a
  topic list on a terminal, or creates the vault with no topics plus a
  how-to-add-them warning when non-interactive. A first vault (and a legacy
  bare re-init, which replaces the sole vault rather than adding one) keeps
  the standard defaults. Choosing a topic another vault already declares
  prints a warning naming the vault.
- With one configured vault every command's output is byte-identical to the
  previous release; `vault:` qualifiers appear only in multi-vault configs.
  `awiki serve` still serves exactly one vault per instance (pick it with
  `--vault`); a single multiplexed server is a planned evolution.

### Changed
- **README restructured for onboarding.** The front door now reads in newcomer
  order: pitch + badges, a PyPI-first quick start, a "Using awiki with your
  agent" section carrying the memory-file block itself, then the why, how the
  vault works, and a command reference grouped by what you are trying to do
  (setup / getting knowledge in / getting knowledge out / maintenance /
  serving). Nothing was dropped: `awiki serve`, `token`, `skills` and
  `init --remote` are now reachable from the reference instead of only from
  their own sections, `awiki --version` is documented for the first time, and
  the LICENSE / CHANGELOG / `Doc/` links are absolute so they resolve on PyPI.
- **The README owns the memory-file block.** The "Knowledge base: the Agent
  Wiki" block that `awiki guide` prints is embedded verbatim in the README, and
  a test asserts the two stay byte-identical, so editing one copy alone fails
  the suite instead of shipping two different blurbs.
- **Published docs and the packaged block are ASCII-only.** `README.md`,
  `CHANGELOG.md` and `data/guide.md` lost their em dashes, en dashes, arrows
  and ellipses, and `tests/test_docs_ascii.py` keeps them out. The guide
  block's wording is otherwise unchanged.
- **Untargeted ingest into a vault without a `default_topic` is a hard
  error.** Previously it silently fell back to `research`, minting an
  undeclared topic folder; now it asks for `--topic` or a `default_topic` in
  `wiki.yaml`. Unreachable for existing vaults - init has always written a
  `default_topic`.

### Fixed
- **`init --name` reclaims a stale registry name.** A registry entry whose
  local vault directory no longer holds a `wiki.yaml` (deleted or moved
  vault) no longer blocks re-initing under the same name; a stderr note
  reports the reclaim. A live local vault or a remote entry still refuses
  the duplicate name.
- **`awiki init` no longer wipes the config file.** A bare `init <path>`
  merges `vault_path` over the existing config, so `trusted_dirs` and any
  `server` key survive; `init --remote` against a config holding `vaults:`,
  `trusted_dirs`, or a `vault_path` preserves them all, landing the remote as
  a `vaults:` entry - a local `vault_path` is kept beside the new url on
  `main` (previously each rewrote the file wholesale).
- **Migration keeps a hybrid `main`.** A legacy config holding both
  `vault_path` and `server` migrates to a `main` entry preserving both keys
  (the url wins at backend selection, the path serves local resolution), so
  local `serve`/`tag`/`doctor` keep working after any migrating write.
- **Stale-vault errors name the real key.** With a `vaults:` schema config,
  a missing vault is reported by entry name and its `path:` key in the
  declaring config file; legacy configs keep the `vault_path` wording.
- **The README no longer describes marker-based guide installs.** It claimed
  the block was wrapped in `<!-- awiki:begin vX.Y.Z -->` / `<!-- awiki:end -->`
  markers carrying a version, and that an agent should re-run `awiki guide` and
  re-adapt the block whenever a newer version shipped. None of that has been
  true since the block became static; `tests/test_guide.py` has asserted the
  markers' absence the whole time.

## [0.7.2]

### Changed
- **Published to PyPI as `agent-wiki-kb`.** First public PyPI release. The
  natural names are unavailable: `agent-wiki` is owned by an unrelated,
  actively-maintained project, and `agentwiki` is rejected by PyPI's
  name-similarity guard (it collapses to the same normalized form as
  `agent-wiki`). It ships under `agent-wiki-kb` (`pip install agent-wiki-kb`).
  Only the distribution name is affected - the import package is still
  `agent_wiki` and the `awiki` / `aw` commands are unchanged.

## [0.7.1]

### Changed
- Attempted to publish as `agentwiki`; blocked before publish by PyPI's
  name-similarity guard. Superseded by 0.7.2 (`agent-wiki-kb`).

## [0.7.0]

### Added
- **`awiki skills` command group.** A new subcommand group installs the three
  bundled agent skills (`awiki-search`, `awiki-save`, `awiki-ingest`) into
  whatever agent harness is detected (Claude Code, pi, Hermes, opencode),
  backed by the AgentSquire library. `awiki skills install` copies them in with
  a provenance stamp; `status`, `update`, and `uninstall` manage them;
  `--scope user|project` selects the target and `--harness NAME` narrows to one
  harness. The skills now ship inside the wheel as package data under
  `src/agent_wiki/skills/`, so no source checkout is needed to install them.
- **Proactive skill-staleness notice.** Every `awiki` invocation runs a safe
  startup check: when your installed skills are older than the bundled copies
  it prints one stderr line naming `awiki skills update`, without ever
  prompting, reading stdin, or touching stdout or the exit code. Agents see the
  notice too - it is not gated on an interactive terminal - and `CI` or
  `AGENTSQUIRE_NO_UPDATE_CHECK` suppress it.

### Changed
- **`agentsquire` now resolves from PyPI.** Now that `agentsquire` is published
  to PyPI, the dev-time `[tool.uv.sources]` editable override that pinned it to
  the sibling `../AgentSquire` checkout has been removed from `pyproject.toml`.
  A fresh install or `uv sync` pulls `agentsquire>=0.2.1` (currently 0.3.0)
  straight from the index - no side-by-side source checkout required.

### Migration
- If you previously installed the skills by hand-copying **or symlinking** the
  old repo-root `skills/` directory into a harness (e.g. `~/.claude/skills/`),
  run `awiki skills update --force` once to adopt them under provenance. Both
  unstamped copies and symlinks are classified as locally modified and are never
  silently overwritten, so a plain `install`/`update` skips them with a reason
  (it will not crash on a pre-existing symlink); `--force` replaces them with a
  stamped copy (a symlink is removed, not followed). Alternatively, delete the
  old skill directories or symlinks and run `awiki skills install`. Note that
  this release deleted the old repo-root `skills/` tree, so any symlink pointing
  into it is now dangling - `--force` it or remove it.

## [0.6.0]

### Changed
- **`awiki guide` output is trimmed and de-versioned.** The self-installing
  block is now a static, self-contained ~15-line block: it frames the wiki as
  the first stop for durable project/domain knowledge (no longer pitched as a
  general web-search replacement), keeps the search->show and edit-raw->reingest
  habits inline, and carries the `awiki-save` nudge. The default `awiki guide`
  preamble is a short "add this once; leave it if already present" instruction.

### Removed
- **BREAKING: the `<!-- awiki:begin vX.Y.Z -->` / `<!-- awiki:end -->` markers
  and the version-staleness note are gone** from the `awiki guide` output. The
  installed block is now static, so consumers add it once and re-adapt only when
  they choose to - there is no marker to detect staleness or re-sync against.
  Pre-1.0 this rides a minor bump, but it changes the `awiki guide` artifact
  format, so it is called out explicitly.

## [0.5.0] - 2026-07-08

### Changed
- **Renamed `awiki directions` -> `awiki guide`.** The command that prints the
  self-installing agent-onboarding block now reads `awiki guide`, which reads
  naturally as a "point your agent here and run this" call to action (and now
  headlines the README's get-started line). The old name still works as a
  **hidden, deprecated alias**, so existing muscle memory and any half-installed
  memory-file blocks keep functioning. The `<!-- awiki:begin vX.Y.Z -->` marker
  is unchanged, so installed blocks still detect staleness and re-sync normally.

## [0.4.0] - 2026-07-06

### Added
- **`render_hash` drift guard.** Every rendered page now carries a `render_hash`
  fingerprint of its body in frontmatter, letting awiki tell an intended,
  raw-driven update apart from an out-of-band hand-edit of the page:
  - Editing `raw/<name>` and running `awiki reingest <name>` rebuilds the page
    cleanly - **a raw edit no longer trips the guard**, so the canonical
    edit-the-raw loop needs no `--force`.
  - The guard now fires only when the *page itself* was hand-edited out of band;
    `reingest` (and `ingest --update`) then print a page-vs-raw diff and stop
    until you review and re-run with `--force`.
  - Lazy trust-on-first-use: pre-existing pages without a `render_hash` are
    trusted the first time they're touched and stamped going forward, so
    upgrading an existing vault needs no migration step.
- **`awiki raw <name>`** - resolve a page to its `raw/<name>` source path,
  printed to stdout so it drops straight into command substitution
  (`$EDITOR "$(awiki raw my-notes.md)"`). Errors exactly as `reingest` does on a
  missing or ambiguous name; on a remote vault it prints the server-side
  reference and notes on stderr that the raw isn't locally editable.
- **`awiki doctor` render-hash checks** - stamps `render_hash` on un-hashed but
  faithful pages, and reports un-hashed pages whose body has diverged from their
  `raw/` source.

### Changed
- `awiki reingest` and `awiki show` now print the resolved read/write location
  (a local absolute path, or the server URL + vault-relative path for a remote
  vault) on **stderr**. stdout stays byte-identical, so skills that parse command
  output verbatim are unaffected.

## [0.3.1] - 2026-07-01

### Fixed
- `awiki directions`: corrected the raw-editing guidance so the installed block
  tells agents to edit the `raw/` source and run `awiki reingest`, never
  hand-edit a rendered page.
- Documented the page-update path - edit raw -> `reingest`, or `ingest --update`
  for an external file - in the directions block and the `awiki-save` skill,
  including the remote-vault case.

## [0.3.0] - 2026-06-27

### Added
- **Tag vocabulary system.** An optional `tags:` block in `wiki.yaml`
  (`mode: off | warn | strict` plus a preferred -> aliases map) canonicalizes
  tags across the vault:
  - `awiki tag add <preferred> [--alias ...]` - persist vocabulary entries through
    a comment-preserving `wiki.yaml` writer (idempotent; refuses to steal an
    alias already bound to another term).
  - `awiki tag suggest [--write]` - draft a vocabulary from the tags already in
    use, grouping related tags as alias candidates.
  - `awiki tag fix [--write] [--topic T] [PATH]` - canonicalize existing pages'
    frontmatter tags (preview by default; page frontmatter only, never `raw/` or
    the page body).
  - Ingest canonicalizes tags at the single write boundary; `--tag-mode
    off|warn|strict` overrides the mode per ingest, and `strict` rejects
    out-of-vocabulary tags pre-flight.
  - `awiki lint` gained a read-only `TAG` (tag-audit) check; `lint --strict`
    turns it into a CI gate (non-zero exit on any TAG finding).

### Fixed
- Bare `mode: off` tag blocks round-trip safely through the `wiki.yaml` writer.
- `canonicalize_tags` hardened against non-string tag values.

## [0.2.1] - 2026-06-26

### Added
- **`--vault PATH` / `AWIKI_VAULT`** - override the configured vault for a single
  invocation (forces a local vault).
- `awiki doctor` now repairs a stale local `vault_path` (config pointing at a
  vault that no longer exists) instead of hard-stopping.

## [0.2.0] - 2026-06-25

### Added
- **URL ingest across the network server.** Remote clients fetch page content
  locally, so `awiki ingest <url>` works whether the vault is local or served
  over HTTP.
- New `awiki lint` checks:
  - `SOURCE` - a `raw/` file edited in place (drifted from its recorded sha256).
  - `STALE` - a page whose body lags its newest source.
  - `SIZE` - pages over 200 lines, flagged as split candidates.
  - `INDEX` - pages missing from `index.md`.
  - `lint --refetch` - re-fetch URL sources and flag any whose upstream content
    changed (`UPSTREAM`; network, off by default, local vaults only).

### Fixed
- `awiki doctor --reconcile-raw` refreshes the provenance sidecar's sha256.

## [0.1.1] - 2026-06-20

### Added
- **URL ingestion.** `awiki ingest <url>` fetches and ingests web pages:
  - HTML extracted via trafilatura; PDFs via pymupdf4llm (pdfplumber selectable).
  - Every ingest writes a sha256 **provenance sidecar** (`raw/<name>.meta.yaml`);
    the original fetched artifact is archived byte-identically under `raw/assets/`.
  - Normalized-URL dedup and a sha256 skip avoid re-ingesting unchanged URLs.
  - `source_url` is emitted inline on fetched pages; non-text content types are
    rejected with a friendly one-line error.
- Single-source-of-truth versioning: everything derives from `__version__`, and
  `awiki directions` re-adapts an installed block when a newer version ships
  (shared-vault framing).

### Changed
- Licensed under **GPL-3.0-or-later**.

### Fixed
- `awiki lint` no longer flags provenance sidecars as un-ingested `raw/` files.

## [0.1.0] - 2026-04-14

Initial release.

### Added
- **Core vault + CLI** (`awiki` / `aw`): `init`, `ingest` (files), `search`,
  `show`, `index`, `lint`, `status`, `log` over a plain-markdown vault with YAML
  frontmatter and `[[wikilinks]]`.
- **Multi-word search** - AND-across-the-page matching with coverage-ranked
  results and a lower-ranked partial-match tier.
- **`awiki show <path>`** - print any vault file verbatim by its vault-relative
  path.
- **`ingest --update`** plus a collision guard that refuses to clobber an
  existing `raw/` basename (per-file skip/continue in globs).
- **`awiki reingest <name>`** - rebuild a page from its edited `raw/<name>`
  source (diff-and-stop unless `--force`); the canonical page-edit loop.
- **Conversation ingest** - adapters for Claude Code, OpenCode, and a drop-zone,
  a canonical Conversation Bundle format, `awiki sync` (state-tracked,
  idempotent), `awiki adapt`, `awiki ingest-conversation`, and optional
  summarization (`none` / `claude-p` / `local-openai`).
- **Auto-context hook** - `awiki context` (YAKE keyword extraction -> search ->
  compact pointer block) and `awiki hook install|uninstall|status` to wire it
  into an agent CLI's `UserPromptSubmit` hook.
- **`awiki directions`** - a self-installing wiki-usage block for agent memory
  files (`CLAUDE.md`, `AGENTS.md`, ...).
- **Network server** - `awiki serve` (FastAPI, bearer auth, role-gated
  reader/writer/admin), `awiki token add|list|revoke`, a transparent remote
  client (`awiki init --remote ... --token ...`), per-vault file locks, and
  `awiki doctor` for schema-drift repair.
- **Claude Code skills** - `awiki-search`, `awiki-save`, `awiki-ingest`.
