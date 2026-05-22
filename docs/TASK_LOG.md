# Task Log

## 2026-05-22

### Add storage hygiene and retention cleanup

**Summary:** Added runtime cleanup for non-canonical storage artifacts and moved
OpenAI debug output out of the Obsidian vault.
**Files changed:**

- `app/config.py`
- `app/storage/retention.py`
- `app/receipts/repository.py`
- `app/obsidian/writer.py`
- `app/telegram/bot.py`
- `tests/test_storage_retention.py`
- `tests/test_documents_repository.py`
- `.env.example`
- `README.md`
- `docs/PROJECT_STATE.md`
- `docs/DECISIONS.md`
- `docs/NEXT_STEPS.md`
- `docs/TASK_LOG.md`

**Details:**

- `/export_receipts` now writes ZIP files under `EXPORT_STORAGE_DIR`.
- Invalid OpenAI JSON debug files now write under `DEBUG_STORAGE_DIR/openai/`
  instead of `Users/.../DEBUG/openai` in the Obsidian vault.
- Startup cleanup removes expired files from `EXPORT_STORAGE_DIR`,
  `DEBUG_STORAGE_DIR`, `TMP_STORAGE_DIR/materialized`, `TMP_STORAGE_DIR/exports`,
  and `TMP_STORAGE_DIR/telegram`.
- Cleanup is path-safe, skips canonical document storage, and does not touch
  `TMP_STORAGE_DIR/processing`, which remains owned by SQLite processing
  session cleanup.
- `OCR_VERIFIED` is now documented as legacy-only; new DB-first documents keep
  canonical OCR in app storage and SQLite file records.

**Reason:** After S3/B2 storage, export ZIPs, raw debug outputs, and
materialized local S3 copies are non-canonical artifacts that should not grow
without retention policy.
**Validation:** `./.venv/bin/python -m pytest -q` passed with 97 tests.
**Follow-ups:** PR8: storage health checks and optional dry-run-first
repair/backfill tooling.
**Related decisions:** `2026-05-22 - Runtime retention cleanup for
non-canonical artifacts`; `2026-05-22 - OCR_VERIFIED is legacy-only`;
`2026-05-22 - Generic storage refs for canonical images`.

### Add generic S3/B2 image storage

**Summary:** Added generic local/S3 storage references for canonical receipt
images and introduced `stored_image` alongside `original_image`.
**Files changed:**

- `app/config.py`
- `app/db/schema.py`
- `app/db/migrations.py`
- `app/repositories/documents.py`
- `app/storage/object_store.py`
- `app/storage/images.py`
- `app/receipts/models.py`
- `app/receipts/repository.py`
- `app/telegram/handlers/receipt.py`
- `app/telegram/handlers/receipts.py`
- `tests/test_db_foundation.py`
- `tests/test_documents_repository.py`
- `.env.example`
- `README.md`
- `docs/PROJECT_STATE.md`
- `docs/DECISIONS.md`
- `docs/NEXT_STEPS.md`
- `docs/TASK_LOG.md`

**Details:**

- Added `storage_backend`, `storage_key`, `bucket`, `is_canonical`, and `etag`
  metadata to `document_files`.
- Added local and S3-compatible object storage abstraction for canonical images.
- New confirm flows create `documents.status='storing_files'`, store
  `original_image` and optimized `stored_image`, then mark the document
  `confirmed`.
- `stored_image` is preferred for Obsidian export and `/receipt` display.
- DB copy/export/delete can operate on S3-backed canonical images.
- Storage failures mark the document `storage_failed` and keep the review
  session active for retry.

**Reason:** Receipt images are large immutable binaries and fit private
S3-compatible storage, while SQLite should remain the source of truth for
ownership and file metadata.
**Validation:** `./.venv/bin/python -m pytest -q` passed.
**Follow-ups:** PR7: exports/debug cleanup and local cache retention.
**Related decisions:** `2026-05-21 - SQLite as source of truth`;
`2026-05-21 - Obsidian as export / representation`;
`2026-05-22 - Generic storage refs for canonical images`.

### Make delete, grant, and export DB-first

**Summary:** Moved receipt deletion, admin grant/copy, and user ZIP export to
DB-first behavior for new documents while keeping manifest/Markdown fallback for
legacy receipts.
**Files changed:**

- `app/repositories/documents.py`
- `app/receipts/repository.py`
- `app/telegram/handlers/delete.py`
- `tests/test_documents_repository.py`
- `docs/PROJECT_STATE.md`
- `docs/DECISIONS.md`
- `docs/TASK_LOG.md`
- `docs/NEXT_STEPS.md`
- `README.md`

**Details:**

- DB delete validates recorded file paths, deletes existing app/vault files,
  counts missing files, and soft-deletes the document row with `deleted_at`.
- Admin global delete can use exact document ids; ambiguous `file_stem` matches
  are rejected.
- DB grant deep-copies canonical files and item/document rows to a new document
  id for the target user, then regenerates Obsidian export.
- User ZIP export now includes legacy Obsidian files plus DB canonical files
  under `Canonical/<receipt_id>/`.
- Legacy manifest-backed delete/copy/export remains as fallback.

**Reason:** After DB-first document creation, receipt management commands should
operate from SQLite and `document_files` instead of relying on manifests that
new receipts no longer create.
**Validation:** `./.venv/bin/python -m pytest -q` passed.
**Follow-ups:** Implement file retention/image policy and migrate correction
rules to SQLite.
**Related decisions:** `2026-05-21 - SQLite as source of truth`;
`2026-05-21 - Obsidian as export / representation`;
`2026-05-22 - Canonical document files in app storage`;
`2026-05-22 - Soft-delete DB documents after file deletion`.

### Make documents, items, and files DB-first

**Summary:** Moved confirmed receipt/order persistence to SQLite documents,
items, and file records, with canonical files stored under app storage and
Obsidian generated as an export.
**Files changed:**

- `app/repositories/documents.py`
- `app/receipts/models.py`
- `app/receipts/repository.py`
- `app/obsidian/writer.py`
- `app/telegram/handlers/receipt.py`
- `app/telegram/handlers/receipts.py`
- `tests/test_documents_repository.py`
- `README.md`
- `docs/PROJECT_STATE.md`
- `docs/DECISIONS.md`
- `docs/TASK_LOG.md`
- `docs/NEXT_STEPS.md`

**Details:**

- Confirmed review now creates `documents`, `document_items`, and
  `document_files` rows with parsed JSON, review payload JSON, possible errors,
  parser/schema/prompt versions, and file metadata.
- Temp image/OCR files move into
  `data/storage/documents/<document_id>/original.jpg`,
  `clean.hy.txt`, and `source.hy.txt`.
- Obsidian Markdown and exported image are generated from DB/parsed JSON and
  recorded as export file rows.
- New manifest JSON files are not created by the confirm flow.
- `/my_receipts` and `/receipt` read DB-first documents before falling back to
  legacy manifests.

**Reason:** Documents, items, and files are structured application data and need
SQLite as their source of truth before PWA/API, DB-first delete, and reliable
file retention work.
**Validation:** `./.venv/bin/python -m pytest -q` passed.
**Follow-ups:** Make `/delete_receipt`, `/grant_receipt`, and
`/export_receipts` DB-first with manifest fallback for old receipts; implement
file retention/image policy.
**Related decisions:** `2026-05-21 - SQLite as source of truth`;
`2026-05-21 - Obsidian as export / representation`;
`2026-05-21 - Store processing stages`;
`2026-05-22 - Canonical document files in app storage`.

### Move processing sessions to SQLite

**Summary:** Moved active Telegram review sessions from file JSON storage to
SQLite `processing_sessions` and moved temporary processing files out of the
Obsidian vault.
**Files changed:**

- `app/review/models.py`
- `app/storage/sessions.py`
- `app/telegram/handlers/receipt.py`
- `app/telegram/handlers/common.py`
- `app/telegram/bot.py`
- `tests/test_processing_sessions.py`
- `README.md`
- `docs/PROJECT_STATE.md`
- `docs/DECISIONS.md`
- `docs/TASK_LOG.md`
- `docs/NEXT_STEPS.md`

**Details:**

- Added session ids and final/processing states to `ReceiptSession`.
- Replaced runtime session JSON files with SQLite-backed `SessionStore`.
- New temp image/OCR files are written to `data/tmp/processing/<session_id>/`
  and cleaned after confirm/cancel/failure.
- Waiting review/correction sessions restore after restart; stale OCR/OpenAI
  sessions fail closed on startup.
- Active waiting sessions block new photo processing before quota/download/OCR.

**Reason:** Review state should survive restarts through SQLite, and temporary
files should not be synced or retained as Obsidian export artifacts.
**Validation:** `./.venv/bin/python -m pytest -q` passed during implementation.
**Follow-ups:** Continue with PR5: make documents, items, and files DB-first.
**Related decisions:** `2026-05-22 - SQLite processing sessions and temp outside
vault`; `2026-05-21 - SQLite as source of truth`;
`2026-05-21 - Obsidian as export / representation`.

### Cleanup storage PR1-PR3 decisions and quota audit events

**Summary:** Tightened usage event audit data and removed runtime legacy access
JSON import after the SQLite access/quota migration.
**Files changed:**

- `app/repositories/usage.py`
- `app/users/quotas.py`
- `app/telegram/handlers/receipt.py`
- `app/repositories/users.py`
- `app/users/access_service.py`
- `tests/test_access_control.py`
- `tests/test_usage_events.py`
- `README.md`
- `docs/PROJECT_STATE.md`
- `docs/DECISIONS.md`
- `docs/TASK_LOG.md`

**Details:**

- `receipt_attempt` quota events now return their SQLite id, store a role
  snapshot in `metadata_json`, and can be updated to the final document type
  after OCR-based classification.
- Automatic `data/access.json` import was removed from runtime access startup.
- Documented decisions from PR1/PR2 review: schema-ahead DB foundations,
  Telegram owner ids without users FK, local server timestamps, and `file_stem`
  as export identity.

**Reason:** Follow-up review found small but important audit/documentation gaps
after PR1-PR3.
**Validation:** `./.venv/bin/python -m pytest -q` passed; `git diff --check`
passed.
**Follow-ups:** Continue with PR4: move processing sessions to SQLite and move
temporary files out of the Obsidian vault.
**Related decisions:** `2026-05-22 - Event-based quotas in SQLite`;
`2026-05-22 - SQLite schema can lead runtime implementation`;
`2026-05-22 - Telegram owner id without users foreign key`;
`2026-05-22 - Local server timestamps for MVP`;
`2026-05-22 - File stem as export identity`.

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
