# Codebase Registry & Git-Driven Auto-Reindex

## Goals

1. Replace the hardcoded `scripts/index_wave_repos.sh` with a DB-backed registry — the single source of truth for which codebases Waverider tracks.
2. Auto-reindex any registered codebase when new commits arrive on its main branch, using git SHA comparison (no external dependencies, no AWS, no webhooks).
3. Expose registry management as MCP admin tools so Copilot can add/remove/list codebases without touching files.

---

## Chosen Approach: Git SHA Polling

A polling script (`scripts/reindex_if_changed.py`) runs on a configurable interval (e.g., cron every 5–15 minutes):

1. Query `codebase_metadata WHERE enabled = true`
2. For each codebase, run `git fetch origin` then read `origin/{main_branch_name}` HEAD SHA
3. Compare to `last_indexed_commit` stored in DB
4. If different → run CocoIndex incremental update → write the new SHA back to DB
5. If git fetch fails or the path doesn't exist, log a warning and skip (will retry on next poll)
6. If CocoIndex fails, log an error but **do not update `last_indexed_commit`** — ensures automatic retry on next poll

This is local-first: the repos are already cloned locally, no network exposure is needed, and the approach upgrades cleanly to a cloud trigger later by simply replacing the polling loop with an SQS consumer.

---

## Schema Changes

Add four columns to `codebase_metadata`:

```sql
ALTER TABLE codebase_metadata
    ADD COLUMN IF NOT EXISTS enabled          BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS github_repo      TEXT,
    ADD COLUMN IF NOT EXISTS main_branch_name TEXT    NOT NULL DEFAULT 'main',
    ADD COLUMN IF NOT EXISTS last_indexed_commit TEXT;
```

| Column | Type | Purpose |
|--------|------|---------|
| `enabled` | `BOOLEAN DEFAULT true` | Soft-disable a codebase without removing it |
| `github_repo` | `TEXT` | Informational — e.g. `wavefin/reef`. Not used by the poller. |
| `main_branch_name` | `TEXT DEFAULT 'main'` | Supports repos still on `master` |
| `last_indexed_commit` | `TEXT` | Full 40-char SHA of the last successfully indexed commit |

The migration is applied inside `DatabaseManager.init_schema()` using `ADD COLUMN IF NOT EXISTS` so it is idempotent and safe to run on existing databases.

---

## Component 1: Schema Migration

**File:** `src/waverider/database.py`

Add a `_MIGRATION_SQL` constant with the four `ALTER TABLE` statements and call it at the end of `init_schema()`, after the existing schema and BM25 setup. Using `IF NOT EXISTS` makes this safe to re-run.

Add one new `DatabaseManager` method:

```python
def update_last_indexed_commit(self, codebase_name: str, commit_sha: str) -> None
```

Updates `last_indexed_commit = %s, updated_at = NOW()` for the named codebase.

---

## Component 2: Registry Seed / Migration Script

**File:** `scripts/seed_registry.py`

A one-time script that reads the seven hardcoded repos from the old `index_wave_repos.sh` logic and calls `db.register_codebase(...)` for each, setting `main_branch_name` appropriately (all are `main` unless known otherwise). Skips entries that already exist.

After running this once, `index_wave_repos.sh` is deprecated. The new canonical way to add a codebase is via the MCP admin tool or the seed script.

---

## Component 3: MCP Admin Tools

**File:** `src/waverider/mcp_server.py`

Four new tools added to the existing `mcp` FastMCP instance:

### `register_codebase`
```
register_codebase(name, path, description, language, github_repo, main_branch_name) -> str
```
Inserts a new row into `codebase_metadata`. Returns confirmation or error. Validates that `path` exists on disk before inserting. Does **not** trigger an immediate index run — the next polling cycle will detect `last_indexed_commit = NULL` and reindex automatically.

### `list_codebases`
```
list_codebases() -> str
```
Returns a formatted table of all registered codebases with their `enabled` status, `last_indexed_commit` (first 8 chars), and `main_branch_name`.

### `set_codebase_enabled`
```
set_codebase_enabled(name, enabled: bool) -> str
```
Toggles `enabled` for a named codebase. Used to pause indexing of a repo without removing it.

### `deregister_codebase`
```
deregister_codebase(name) -> str
```
Deletes the codebase registry entry. Does **not** delete the indexed data — that lives in CocoIndex-managed tables and must be cleared separately if desired. Returns a warning to that effect.

---

## Component 4: Polling Script

**File:** `scripts/reindex_if_changed.py`

```
Usage:
  python scripts/reindex_if_changed.py [--once] [--interval SECONDS] [--dry-run]

Options:
  --once        Run a single check cycle and exit (default: continuous loop)
  --interval N  Seconds to sleep between cycles when running continuously (default: 300)
  --dry-run     Detect changes and log them, but do not reindex or update DB
```

**Algorithm per codebase:**

```python
path = Path(codebase.path)
if not path.exists():
    log.warning(f"{codebase.name}: path not found, skipping")
    continue

# 1. Fetch remote
result = subprocess.run(["git", "fetch", "origin"], cwd=path, ...)
if result.returncode != 0:
    log.warning(f"{codebase.name}: git fetch failed, skipping")
    continue

# 2. Read current remote SHA
branch = codebase.main_branch_name
result = subprocess.run(["git", "rev-parse", f"origin/{branch}"], cwd=path, ...)
current_sha = result.stdout.strip()

# 3. Compare
if current_sha == codebase.last_indexed_commit:
    log.debug(f"{codebase.name}: up to date at {current_sha[:8]}")
    continue

# 4. Reindex
log.info(f"{codebase.name}: new commits detected, reindexing ({current_sha[:8]})")
rc = subprocess.run([sys.executable, "scripts/build_index.py",
                     "--codebase-path", str(path),
                     "--index-name", codebase.name,
                     "--description", codebase.description], ...)
if rc.returncode == 0:
    db.update_last_indexed_commit(codebase.name, current_sha)
    log.info(f"{codebase.name}: reindex complete")
else:
    log.error(f"{codebase.name}: reindex failed, will retry on next poll")
```

**Error handling:**
- Path missing → skip (warn)
- `git fetch` fails → skip (warn). Does not abort other codebases.
- CocoIndex fails → skip SHA update (auto-retry on next poll)
- DB unreachable at start → exit with non-zero code (cron will try again next cycle)

---

## Component 5: Updated `index_wave_repos.sh` (deprecated path)

The script is not deleted but gets a deprecation notice at the top pointing to `seed_registry.py` + `reindex_if_changed.py`. It remains runnable for emergency full-reindexes.

---

## Running the Poller

**One-shot (e.g., test or CI):**
```bash
poetry run python scripts/reindex_if_changed.py --once
```

**Continuous (local dev machine):**
```bash
poetry run python scripts/reindex_if_changed.py --interval 300
```

**Cron (recommended for local setup):**
```cron
*/10 * * * * cd "/Users/yourname/dev/project waverider" && poetry run python scripts/reindex_if_changed.py --once >> /tmp/waverider-reindex.log 2>&1
```

---

## Initial Population

After deploying the schema migration, run once to seed the existing seven repos:

```bash
poetry run python scripts/seed_registry.py
```

Then do an initial reindex (same as today) to populate `last_indexed_commit` for each:

```bash
poetry run python scripts/reindex_if_changed.py --once
```

Because `last_indexed_commit` starts as `NULL`, the poller will treat every enabled codebase as "changed" on first run and index them all. This replaces the manual `index_wave_repos.sh` invocation.

---

## File Change Summary

| File | Change |
|------|--------|
| `src/waverider/database.py` | Add `_MIGRATION_SQL`, call in `init_schema()`, add `update_last_indexed_commit()`, add `get_all_codebases()` |
| `src/waverider/mcp_server.py` | Add 4 admin tools: `register_codebase`, `list_codebases`, `set_codebase_enabled`, `deregister_codebase` |
| `scripts/reindex_if_changed.py` | New file — polling script |
| `scripts/seed_registry.py` | New file — one-time DB seed from hardcoded list |
| `scripts/index_wave_repos.sh` | Add deprecation notice (not deleted) |

**No changes to:** `cocoindex_app.py`, `config.py`, `fusion.py`, `embeddings.py`, `build_index.py`

---

## Future: Cloud Upgrade Path

When Waverider moves to a hosted environment, swap the polling loop for an SQS consumer:

1. GitHub Actions workflow on push to main → `aws sqs send-message` with `{codebase_name, commit_sha}`
2. `reindex_if_changed.py` polling loop → `sqs_consumer.py` long-poll loop
3. DB schema, CocoIndex call, and SHA update logic are **identical**

The registry and the `update_last_indexed_commit` interface are forward-compatible with no changes.
