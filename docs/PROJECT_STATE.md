# Project State

## Purpose

Receipt Bot is a Python Telegram bot for processing Armenian receipts and order
screenshots. A user sends a photo, the bot runs OCR, structures and translates
the content with OpenAI, asks the user to review Russian note fields, and then
creates a readable export for the user's receipt archive.

The current MVP interface is Telegram. Future PWA/API clients should consume the
structured SQLite data rather than parse Markdown.

## Current architecture

- Runtime: Python 3.11+.
- Telegram framework: `python-telegram-bot`.
- OCR: Google Cloud Vision `DOCUMENT_TEXT_DETECTION`.
- LLM parsing: OpenAI Responses API.
- Config: `.env` plus `*_FILE` secret support.
- Current DB: SQLite in `data/app.db` with idempotent migrations, WAL, foreign
  keys, and busy timeout.
- Current human-readable export: Obsidian Markdown in `OBSIDIAN_VAULT`.
- Current processing state: a hybrid model. Users, access requests, quota
  events, review processing sessions, and newly confirmed receipt/order
  documents are in SQLite; correction rules still use compatible file-based MVP
  paths until later PRs migrate them.

## Source of truth

The durable architectural rule is:

```text
SQLite = source of truth for structured application data
Obsidian Markdown = export / representation
Files = images, OCR artifacts, debug artifacts, and generated exports
```

Current implementation status:

- SQLite is already the source of truth for users, access requests, quota usage
  events, active review processing sessions, and newly confirmed documents,
  document items, and document files.
- SQLite schema already includes planned tables for documents, items, files,
  processing sessions, usage events, correction rules, magic links, and web
  sessions.
- Confirmed review creates DB rows first, stores canonical images through the
  configured storage backend, stores OCR files under local app storage, and
  generates Obsidian Markdown as an export artifact.
- Existing Markdown/manifest files remain operational artifacts and fallback
  data for old receipts, but new manifest JSON files are no longer created by
  the confirm flow.
- Delete, grant/copy, and export are DB-first for new documents and keep
  manifest/Markdown fallback for old receipts.

## Current data flow

Current receipt flow:

```text
Telegram photo
-> access and quota checks
-> block if another review/correction session is active
-> create SQLite processing session and temp dir under data/tmp/processing
-> download image into data/tmp/processing/<session_id>/
-> Google Cloud Vision OCR with language hints hy, ru, en
-> deterministic CLEAN OCR
-> OpenAI structured JSON
-> Russian field review in Telegram
-> user confirm / JSON correction / cancel
-> SQLite documents/items/files + canonical files through document_files storage refs
-> Obsidian Markdown note + exported image
```

Important product rule: manual review is performed on Russian fields that will
appear in the note/export, not on raw Armenian OCR alone.

Order screenshot flow:

```text
Telegram photo or /order caption
-> same OCR layer
-> document type classification or explicit order override
-> OpenAI order-oriented extraction
-> review useful product/order fields
-> export
```

## Main modules

- `app/config.py` - typed settings, env and `*_FILE` secrets, storage paths.
- `app/db/` - SQLite connection, schema, and migrations.
- `app/repositories/` - DB-backed repositories. Currently users, access
  requests, and usage events are implemented here.
- `app/users/` - access service, user roles/statuses, quota service, user paths.
- `app/telegram/` - bot startup, logging, handlers for access, receipts, delete,
  and common runtime state.
- `app/ocr/` - Google Vision OCR and deterministic OCR cleaning.
- `app/llm/` - OpenAI parsing, document prompts, strict JSON parsing, receipt
  detection helpers.
- `app/review/` - Telegram review payload rendering/parsing and receipt session
  models.
- `app/receipts/` - document type classification, receipt/order models, legacy
  receipt listing helpers.
- `app/obsidian/` - Markdown writer and manifest-based deletion fallback.
- `app/storage/` - path safety helpers, normalization, session temp storage,
  runtime retention cleanup, read-only storage health checks, and correction
  store.
- `app/storage/sessions.py` - SQLite-backed processing session store and temp
  cleanup for Telegram review state.

## Storage model

Configured storage:

- `DATABASE_URL`, default `sqlite:///data/app.db`.
- `DATA_DIR`, default `data`.
- `APP_STORAGE_DIR`, `TMP_STORAGE_DIR`, `EXPORT_STORAGE_DIR`, `DEBUG_STORAGE_DIR`,
  runtime retention settings, and S3-compatible image storage settings are used
  by the current storage pipelines.
- `OBSIDIAN_VAULT` points to the local/synced vault used for human-readable
  exports.

Current persistent files:

- `Users/<telegram_user_id>/Receipts/YYYY/MM/<file>.md`
- `Users/<telegram_user_id>/Attachments/receipts/YYYY/MM/<file>.jpg`
- `Users/<telegram_user_id>/OCR/YYYY/MM/<file>.clean.hy.txt` for legacy
  manifest-backed receipts only
- `Users/<telegram_user_id>/OCR_VERIFIED/YYYY/MM/<file>.verified.hy.txt` for
  legacy manifest-backed receipts only
- `Users/<telegram_user_id>/MANIFEST/receipts/YYYY/MM/<file>.manifest.json` for
  legacy manifest-backed receipts only
- `data/debug/openai/<telegram_user_id>/YYYY/MM/...` only for invalid OpenAI
  JSON
- `data/exports/<telegram_user_id>/receipts_YYYYMMDD_HHMMSS.zip` for user ZIP
  exports
- `data/app.db` for users, access requests, `usage_events`,
  `processing_sessions`, `documents`, `document_items`, and `document_files`
- `data/storage/documents/<document_id>/original.jpg` when the image backend is
  local
- `data/storage/documents/<document_id>/stored.jpg` when the image backend is
  local
- private S3/B2 objects for `original_image` and `stored_image` when
  `STORAGE_IMAGE_BACKEND=s3`
- `data/storage/documents/<document_id>/clean.hy.txt`
- `data/storage/documents/<document_id>/source.hy.txt`
- `data/tmp/processing/<session_id>/` for temporary image/OCR files during
  active processing
- `data/tmp/materialized/`, `data/tmp/exports/`, and `data/tmp/telegram/` for
  short-lived materialized/cache files
- `data/corrections.json` for scoped correction rules

Target storage direction:

- Keep only value-bearing files permanently.
- Temporary processing files now live under `data/tmp/processing/<session_id>/`
  and are moved into canonical app storage when review is confirmed.
- Store canonical document data, items, file metadata/storage references,
  sessions, quotas, and correction rules in SQLite.
- Obsidian exports should be generated from SQLite/parsed JSON, not used as the
  main data source.
- Original receipt images must be preserved unless a later explicit decision
  changes the file retention policy.
- Runtime cleanup removes only pattern-matched old export ZIPs, OpenAI debug
  artifacts, and materialized temp/cache files according to retention settings;
  it refuses unsafe cleanup roots and does not delete canonical document files.
- Processing stages are valuable: keep enough state to audit OCR, LLM parsing,
  review payloads, user corrections, and export status.

## User access and authorization

Current user access model:

- Admins, allowed users, and privileged users are bootstrapped from `.env`.
- Admins are always allowed.
- Unauthorized users must not trigger photo downloads, Google Vision, OpenAI, or
  file creation.
- Unauthorized users can create a pending access request.
- Admins can approve, reject, list users, and revoke access from Telegram.
- Users and access requests are stored in SQLite.
- Legacy `data/access.json` is not imported automatically.

Current quotas:

- Admins are unlimited.
- Privileged users are unlimited by default.
- Regular users have daily/monthly attempt limits.
- Quotas are stored in SQLite `usage_events` as `receipt_attempt` events.
- Attempts are recorded after access and limit checks, before image download,
  OCR, and OpenAI.
- Admin and privileged attempts are also recorded for audit even when their
  limits are unlimited.
- Usage events store the role snapshot in `metadata_json` and the final
  `document_type` when automatic classification happens after OCR.
- Legacy JSON counters in `data/usage` are not imported and are cleaned up.

Current review sessions:

- Active Telegram review/correction sessions are stored in SQLite
  `processing_sessions`.
- Waiting review/correction sessions are restored after restart.
- Stale OCR/OpenAI processing states are marked failed on startup and their
  temp files are cleaned.
- Legacy `data/sessions/*.json` is not imported.

Current documents:

- Confirmed Telegram review creates `documents`, `document_items`, and
  `document_files` rows.
- `documents.parsed_json` stores the final normalized JSON accepted by review.
- `documents.review_payload_json` stores the Russian review payload shown to or
  accepted from the user.
- `documents.possible_errors_json` stores review-visible possible OCR/parser
  errors.
- Canonical images live through `document_files` storage references:
  `storage_backend='local'` for dev/local storage or `storage_backend='s3'` for
  production S3-compatible storage such as Backblaze B2.
- Canonical OCR files still live under `data/storage/documents/<document_id>/`.
- Each confirmed document stores `original_image` and `stored_image` file
  records. `stored_image` is the optimized image used for Obsidian export and
  receipt viewing when available.
- Obsidian Markdown and its exported attachment are recorded as export files in
  `document_files`.
- New manifest JSON files are not created; manifest parsing remains a fallback
  for old receipts.
- `/delete_receipt` removes files recorded in `document_files`, sets
  `documents.status='deleted'`, and keeps a soft-deleted DB row for audit.
- `/grant_receipt` deep-copies DB documents to a new document id for the target
  user and regenerates the Obsidian export.
- `/export_receipts` includes readable Obsidian files plus canonical DB files
  under `Canonical/<receipt_id>/` in the ZIP archive.
- OpenAI invalid-JSON debug output is stored under `DEBUG_STORAGE_DIR`, not in
  the Obsidian vault.
- `/storage_health` is an admin-only read-only Telegram command that reports
  storage issues from SQLite `documents` / `document_files`, local/vault files,
  and S3 object metadata without repairing or deleting canonical data.

Future web authorization:

- `magic_links` and `web_sessions` tables exist in the schema for PWA login.
- No PWA/API is implemented yet.
- Future magic-link flow should store only token hashes, use short TTLs, and
  issue secure web sessions.

## External integrations

- Telegram Bot API for user interaction, photo download, and inline callbacks.
- Google Cloud Vision API via Application Default Credentials for OCR.
- OpenAI API for strict JSON structuring, Russian/English translation, and
  possible error hints.
- Obsidian vault as a local/synced export destination.
- Optional Docker/Caddy deployment files exist for production-style runtime and
  optional webhook mode.

## Important invariants

- Do not call Google Vision, OpenAI, or create files for unauthorized users.
- Do not call OpenAI if OCR is empty.
- Do not create Markdown when OpenAI returns invalid JSON.
- Do not treat Markdown as the application source of truth for new features.
- Keep Telegram handlers thin over time; business logic should move into
  services/repositories usable by future API/PWA.
- Review only the Russian fields that will be saved/exported.
- Preserve original receipt images unless an explicit retention decision changes
  this.
- Correction rules must remain scoped data, not unsafe global string replace.
- Do not log API keys, raw receipt images, full OCR text, raw magic tokens, or
  long user-private payloads.
- Deletion must be path-safe and must not remove files outside configured roots.
- SQLite migrations must be idempotent and atomic per migration.
- Future DB changes must maintain `parser_version`, `schema_version`, and
  `prompt_version` for parsed documents.
- Do not mount S3/B2 as a filesystem for canonical storage; use object storage
  APIs and SQLite storage references.

## Current limitations

- `correction_rules`, `magic_links`, and `web_sessions` are schema-level
  foundations, not fully wired into runtime logic yet.
- Correction rules are still stored in `data/corrections.json`.
- Legacy Obsidian exports can include `OCR_VERIFIED`; new DB-first exports do
  not create permanent `OCR_VERIFIED` files and store OCR canonically in app
  storage instead.
- PWA/API does not exist yet.
- No database migration framework beyond the simple in-project migrations module.

## Testing and validation

Tests are run with:

```bash
./.venv/bin/python -m pytest -q
```

Current tests cover:

- access control and SQLite-backed access persistence;
- SQLite-backed quota usage events;
- SQLite connection and migration behavior, including rollback on failed
  migration;
- JSON parsing and receipt detection;
- Markdown rendering;
- deletion path safety;
- SQLite-backed processing sessions and temp cleanup;
- DB-first document/item/file creation and Obsidian export without new
  manifests;
- DB-first delete/copy/export with legacy manifest fallback;
- generic local/S3 image storage references for canonical images;
- runtime retention cleanup for exports, debug artifacts, and materialized temp
  files;
- storage health checks for path safety, missing files, checksum drift,
  storage status, S3 metadata, and app-storage orphans;
- correction rules;
- order document parsing/review behavior;
- user-scoped receipt listing.

For code changes, run the test suite before commit. For documentation-only
changes, note explicitly if tests were not run.

## Deployment / runtime notes

- Local MVP runs with `python bot.py`.
- Default production mode is polling.
- Optional webhook mode is configured by `BOT_MODE=webhook`, `WEBHOOK_URL`,
  `WEBHOOK_LISTEN`, `WEBHOOK_PORT`, and `WEBHOOK_SECRET_TOKEN`.
- Google Vision local auth uses ADC:

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project PROJECT_ID
```

- `.env` must not be committed.
- Secrets may be provided by env vars or `*_FILE`.
- Server deployments should keep `.env` readable only by the service user.
- Syncthing may be used to sync the Obsidian export vault, but future PWA/API
  should read SQLite-backed data, not sync Markdown as app data.

## Last updated

2026-05-22
