# Task Log

## 2026-05-22

### Move quotas to SQLite usage events

**Summary:** Replaced JSON quota counters with SQLite `usage_events` for
`receipt_attempt` enforcement and audit.
**Files changed:**

- `app/repositories/usage.py`
- `app/users/quotas.py`
- `app/telegram/handlers/receipt.py`
- `tests/test_usage_events.py`
- `README.md`
- `docs/PROJECT_STATE.md`
- `docs/DECISIONS.md`
- `docs/TASK_LOG.md`
- `docs/NEXT_STEPS.md`

**Details:**

- Added `UsageRepository` with event recording, event counting, atomic
  `record_attempt_if_allowed`, and safe cleanup of legacy `data/usage/*.json`.
- Updated `QuotaService` to read/write SQLite usage events while preserving
  compatibility methods.
- Telegram photo handling now checks and records quota attempts atomically before
  downloading images or calling OCR/OpenAI.
- Admin and privileged attempts are recorded for audit even when unlimited.

**Reason:** Quotas are cost-control and audit data, so they belong in SQLite
with the rest of structured application state.
**Validation:** `./.venv/bin/python -m pytest -q` passed.
**Follow-ups:** Move processing sessions to SQLite and move temporary files out
of the Obsidian vault.
**Related decisions:** `2026-05-22 - Event-based quotas in SQLite`;
`2026-05-21 - SQLite as source of truth`.

### Add persistent project context docs

**Summary:** Added repository-native context files for future Codex sessions and
maintainers.  
**Files changed:**

- `docs/README.md`
- `docs/PROJECT_STATE.md`
- `docs/DECISIONS.md`
- `docs/TASK_LOG.md`
- `docs/NEXT_STEPS.md`
- `README.md`

**Details:**

- Documented current architecture, source-of-truth model, storage state,
  authorization model, integrations, invariants, limitations, tests, and runtime
  notes.
- Recorded durable decisions around SQLite, Obsidian export, processing stages,
  Telegram review, correction rules, access persistence, atomic migrations, and
  docs context.
- Added next-step priorities for continuing the SQLite storage migration.
- Added a root README rule requiring docs context updates for meaningful
  project changes.

**Reason:** Future sessions need a stable project context outside chat history
to avoid repeating decisions or breaking architectural invariants.  
**Validation:** Documentation-only change; test suite not required.  
**Follow-ups:** Keep these files updated after each substantial PR.  
**Related decisions:** `2026-05-22 - Persistent project context in docs`.

## 2026-05-21

### Move users and access requests to SQLite

**Summary:** Migrated user profiles and access request state from JSON-oriented
storage to SQLite-backed repositories.  
**Files changed:**

- `app/repositories/users.py`
- `app/repositories/access_requests.py`
- `app/users/repository.py`
- `app/users/access_service.py`
- `app/db/schema.py`
- `app/db/migrations.py`
- `tests/test_access_control.py`
- `tests/test_db_foundation.py`
- `README.md`

**Details:**

- Added DB-backed user and access request repositories.
- Kept legacy `data/access.json` as a one-time import source.
- Added a unique SQLite partial index so each user can have only one pending
  access request.
- Kept unauthorized users from triggering downloads, OCR, OpenAI, or file writes.
- Fixed direct `Settings(...)` construction so test data dirs produce isolated
  SQLite/storage paths.
- Improved migration atomicity and added failed migration rollback coverage.

**Reason:** Access state is security-sensitive and should be durable,
constraint-backed, and compatible with future web authorization.  
**Validation:** `./.venv/bin/python -m pytest -q` passed before merge.  
**Follow-ups:** Move quotas to `usage_events` and move processing sessions to
SQLite.  
**Related decisions:** `2026-05-21 - Access requests in SQLite`;
`2026-05-21 - Atomic per-migration SQLite changes`.

### Add SQLite storage foundation

**Summary:** Added the initial SQLite schema, connection helpers, and migration
framework for DB-first storage.  
**Files changed:**

- `app/config.py`
- `app/db/connection.py`
- `app/db/schema.py`
- `app/db/migrations.py`
- `tests/test_db_foundation.py`
- `.env.example`
- `README.md`

**Details:**

- Added `DATABASE_URL`, `DB_BUSY_TIMEOUT_MS`, and storage directory settings.
- Enabled SQLite foreign keys, WAL, and busy timeout.
- Added schema foundations for users, access requests, documents, document
  items, document files, processing sessions, usage events, correction rules,
  magic links, and web sessions.
- Added idempotent migrations and tests for DB creation and schema availability.

**Reason:** The project needs structured storage before PWA/API, DB-first
deletion, quotas, session persistence, and document search can be implemented
safely.  
**Validation:** `./.venv/bin/python -m pytest -q` passed before merge.  
**Follow-ups:** Implement repositories and migrate runtime subsystems one PR at
a time.  
**Related decisions:** `2026-05-21 - SQLite as source of truth`;
`2026-05-21 - Obsidian as export / representation`.

<!--
### Short task title

**Summary:**  
**Files changed:**
- ``
**Details:**
- 
**Reason:**  
**Validation:** Not run.  
**Follow-ups:**  
**Related decisions:**  
-->
