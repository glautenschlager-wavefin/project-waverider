# Remote Repo Indexing — Design Spec

**Issue:** [#3 — Index from remote repos, not local code](https://github.com/glautenschlager-wavefin/project-waverider/issues/3)
**Date:** 2026-06-10
**Status:** Approved design — ready for implementation planning

## Problem

WaveRider's goal is to let an AI agent understand how all of Wave works. Today, indexing
is bound to the engineer's local dev environment: the codebase registry keys each codebase
on a local `path`, and `reindex_if_changed.py` runs `git fetch` inside a clone the engineer
created manually. WaveRider therefore only understands repos the engineer happens to have
cloned.

This change makes WaveRider index **remote** Wave repositories directly, cloning them into a
WaveRider-managed location, so it understands Wave repos whether or not the engineer has them
locally.

## Goals

1. Index remote repos from the `waveaccounting` GitHub org without requiring engineer-owned clones.
2. Auto-discover org repos and register them (disabled by default) for admin governance.
3. Keep the existing CocoIndex indexing pipeline unchanged — only the *source* of code changes.
4. Draw a clean module boundary (clone management, discovery) that lifts cleanly into a future
   central AWS service.

## Non-Goals (YAGNI — deferred to the future "central service" state)

- GitHub App installation auth (PAT is sufficient for the local tool).
- Webhook / SQS push triggers (polling is sufficient).
- Concurrent, shallow, sparse, or bare clones.
- Deleting indexed data on deregister.
- Any migration path for existing DB rows — nothing is deployed; the DB can be reset.

## Decisions (from brainstorming)

| # | Decision |
|---|----------|
| Runtime | Local-first managed clones now; architected toward a central AWS service later |
| Discovery | Hybrid — auto-discover org repos as `enabled=false`; admin enables the keepers |
| Auth | GitHub PAT via `GITHUB_TOKEN`, HTTPS; clean path to a GitHub App later |
| Clone strategy | Full clone into a managed dir; fetch + hard reset on each poll |
| Discovery cadence | Separate command/tool (~daily), distinct from the indexing poll loop |
| Org | `waveaccounting`; filter out archived repos and forks |
| Transition | Clean cutover — `github_repo` is the source of truth; `path` becomes managed |

## Architecture

A clone-management boundary sits between the registry and the existing CocoIndex indexer; a
separate discovery path populates the registry from the org.

```
┌─────────────────────┐     ┌──────────────────────┐
│ discover_repos.py   │────▶│ github_discovery.py  │──▶ GitHub REST API
│ (script + MCP tool) │     │  (list org repos)    │   (list waveaccounting repos)
└─────────────────────┘     └──────────────────────┘
          │ upserts enabled=false rows
          ▼
┌─────────────────────────────────────────────────┐
│           codebase_metadata (registry)          │
│  github_repo = source of truth; path = managed  │
└─────────────────────────────────────────────────┘
          │ get_enabled_codebases()
          ▼
┌─────────────────────┐     ┌──────────────────────┐
│ reindex_if_changed  │────▶│  repo_manager.py     │──▶ git clone/fetch/reset
│ (thin orchestrator) │     │ (ensure_current →SHA)│   (~/.waverider/repos/<name>)
└─────────────────────┘     └──────────────────────┘
          │ build_index.py (unchanged path)
          ▼
   CocoIndex localfs.walk_dir → embeddings → search
```

### New modules (`src/waverider/`)

**`repo_manager.py`** — owns the managed clone lifecycle.

- `local_path(name) -> Path` — resolves `<WAVERIDER_REPO_ROOT>/<name>`
  (default root `~/.waverider/repos`).
- `ensure_current(github_repo, name, branch) -> str`:
  1. If `<path>/.git` is missing → `git clone --branch <branch> <auth_url> <path>`.
  2. Else → `git fetch origin <branch>`, then `git reset --hard origin/<branch>` and
     `git clean -fdx`.
  3. Return `git rev-parse HEAD`.
- Auth URL: `https://x-access-token:<GITHUB_TOKEN>@github.com/<github_repo>.git`. The token is
  **never** persisted to `.git/config`; it is supplied per-invocation (ephemeral `GIT_ASKPASS`
  or `git -c`) and **never logged**.
- Raises typed `RepoSyncError` on any git failure.

**`github_discovery.py`** — pure HTTP + filtering, no DB writes (testable with mocked responses).

- `list_org_repos(org="waveaccounting", token=GITHUB_TOKEN) -> list[RepoInfo]`:
  - `GET /orgs/{org}/repos`, paginated (`per_page=100`), `Authorization: Bearer <token>`.
  - Filters out `archived == true` and `fork == true`.
  - Maps to `RepoInfo(name, github_repo, default_branch, description, language)`; `language`
    lowercased from GitHub's primary-language field, default `mixed`.
  - Raises `DiscoveryError` on non-200 / auth / rate-limit / network failure.

## Data Model

The `codebase_metadata` table already has `github_repo`, `main_branch_name`, `enabled`, and
`last_indexed_commit` from the prior registry feature. Changes:

| Field | Treatment after cutover |
|-------|-------------------------|
| `github_repo` | **Required source of truth** — `waveaccounting/<repo>` |
| `path` | **Managed/derived** — set by `repo_manager` after first clone; relax `NOT NULL` → nullable |
| `enabled` | Discovery inserts new repos as `false`; `ON CONFLICT` preserves an admin's prior choice |
| `main_branch_name` | Populated from the repo's GitHub default branch at discovery time |
| `last_indexed_commit` | Unchanged — advances only on successful index |
| `last_sync_error` (**new** `TEXT`) | Last `RepoSyncError` message; cleared on a successful sync |
| `last_sync_error_at` (**new** `TIMESTAMPTZ`) | Timestamp of the last sync error; cleared on success |

Migration via additional idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements in
`_MIGRATION_SQL`, plus relaxing `path` to nullable. Since nothing is deployed, the DB may also
simply be reset.

### New / changed `DatabaseManager` methods

- `update_codebase_path(name, path)` — write back the managed clone path.
- `record_sync_error(name, message)` — set `last_sync_error` + `last_sync_error_at`.
- On successful sync, `update_last_indexed_commit` (or a paired call) clears both error fields.
- `upsert_codebase_registration` gains an explicit `enabled` parameter (default preserves
  current behavior); `path` becomes optional/nullable.

## Reindex Flow (`scripts/reindex_if_changed.py`)

The per-repo loop becomes a thin orchestrator over `repo_manager`:

```python
for cb in db.get_enabled_codebases():
    try:
        sha = repo_manager.ensure_current(cb["github_repo"], cb["name"], cb["main_branch_name"])
    except RepoSyncError as e:
        log.warning("sync failed for %s: %s", cb["name"], e)
        db.record_sync_error(cb["name"], str(e))
        failed += 1
        continue

    if sha == cb.get("last_indexed_commit"):
        skipped += 1
        continue

    local = repo_manager.local_path(cb["name"])
    db.update_codebase_path(cb["name"], str(local))
    ok = run_reindex(cb, local, project_root, dry_run)   # build_index.py --codebase-path <local>
    if ok and not dry_run:
        db.update_last_indexed_commit(cb["name"], sha)   # also clears last_sync_error
        reindexed += 1
    else:
        failed += 1
```

**Key properties:**
- **Sync-then-compare:** always `fetch + reset` first (cheap when unchanged), then gate the
  expensive indexing step on a SHA diff. The working tree always matches the SHA we record —
  no race between reading the remote SHA and indexing local state.
- Retry semantics preserved: `last_indexed_commit` advances only on success; failures are
  recorded to the DB and retried next cycle.

## Discovery Path & Admin Surface

**`run_discovery(db, org, dry_run) -> summary`** (shared core):
1. `repos = github_discovery.list_org_repos(org)`
2. For each: `upsert_codebase_registration(name, path=None, description, language, github_repo,
   main_branch_name=default_branch, enabled=False)` for new rows; existing `enabled` preserved.
3. Return `{discovered, new, existing}`.

Thin wrappers:
- **`scripts/discover_repos.py`** — `poetry run python scripts/discover_repos.py [--org waveaccounting] [--dry-run]`.
- **`discover_codebases(org="waveaccounting")` MCP tool** — returns a formatted summary.

Discovery never clones or indexes; discovered repos sit disabled until an admin enables them.

### MCP admin tools

| Tool | Change |
|------|--------|
| `discover_codebases(org)` | **new** — populate registry from org |
| `register_codebase(...)` | `github_repo` **required**; validates slug (not local path); `path` optional |
| `list_codebases()` | show `github_repo`, `enabled`, last commit, and any `last_sync_error` |
| `set_codebase_enabled`, `deregister_codebase` | unchanged |

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `GITHUB_TOKEN` | (required) | PAT for clone + REST discovery |
| `WAVERIDER_REPO_ROOT` | `~/.waverider/repos` | Managed clone root |
| `WAVERIDER_GITHUB_ORG` | `waveaccounting` | Org to discover |

Surfaced in `src/waverider/config.py` alongside existing settings, so the future AWS lift only
changes where these values come from.

## Error Handling

- `RepoSyncError` — clone/fetch/reset failure → log, **record to DB** (`last_sync_error` +
  `last_sync_error_at`), skip that repo, continue others; `last_indexed_commit` unchanged
  (auto-retry next cycle).
- `DiscoveryError` — GitHub API failure (auth, rate limit, network) → discovery aborts with a
  clear message; registry untouched. Missing `GITHUB_TOKEN` fails fast with an actionable error.
- Token is never logged and never written to `.git/config`.

## Optional Seed Step

**`scripts/seed_default_repos.py`** + docs. Run after discovery to enable a curated set of
common Wave services (those that exist in the registry); prints the list it enables. Users then
enable their own product repos as appropriate.

`DEFAULT_REPOS`:
`identity`, `reef`, `api`, `javascript-wave-api-client`, `next-wave`, `wave-messages`,
`lighthouse`, `chunnelx`, `nav`, `tuktuk`, `buoyant`.

## Removed / Retired

- `scripts/seed_registry.py` (replaced by discovery + optional default seed).
- `scripts/index_wave_repos.sh` (already deprecated).

## Testing

- **`repo_manager`** — unit tests against a local bare-git-repo fixture (no network):
  clone-fresh, fetch-updates, reset-after-divergence, SHA return, `RepoSyncError` on bad remote.
- **`github_discovery`** — mocked HTTP: pagination, archived/fork filtering, language mapping,
  `DiscoveryError` on non-200.
- **`reindex_if_changed`** — extend `tests/test_reindex.py` with a fake `repo_manager`:
  sync-then-compare, skip on unchanged SHA, path write-back, commit advance only on success,
  sync error recorded to DB.
- **Discovery upsert** — new rows land `enabled=false`; existing `enabled` preserved.

## Docs

- `docs/CODEBASE_REGISTRY.md` — describe the remote-clone model, discovery, and new env vars.
- `AGENTS.md` — add `discover_codebases` to the admin-tools section.
