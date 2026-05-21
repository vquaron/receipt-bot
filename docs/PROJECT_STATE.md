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
- Current processing state: a hybrid model. Users, access requests, and quota
  events are in SQLite; receipt files, review sessions, and correction rules
  still use the compatible file-based MVP paths until later PRs migrate them.

## Source of truth

The durable architectural rule is:

```text
SQLite = source of truth for structured application data
Obsidian Markdown = export / representation
Files = images, OCR artifacts, debug artifacts, and generated exports
```

Current implementation status:

- SQLite is already the source of truth for users, access requests, and quota
  usage events.
- SQLite schema already includes planned tables for documents, items, files,
  processing sessions, usage events, correction rules, magic links, and web
  sessions.
- Receipt document creation still writes directly to Obsidian-compatible files
  and manifest JSON. Migrating document persistence to SQLite is a next step.
- Existing Markdown/manifest files remain operational artifacts and fallback
  data for old receipts, but future application logic should be DB-first.

## Current data flow

Current receipt flow:

```text
Telegram photo
-> access and quota checks
-> download image into user vault _tmp path
-> Google Cloud Vision OCR with language hints hy, ru, en
-> deterministic CLEAN OCR
-> OpenAI structured JSON
-> Russian field review in Telegram
-> user confirm / JSON correction / cancel
-> Obsidian Markdown note + image + OCR files + manifest
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
- `app/repositories/` - DB-backed repositories. Currently users and access
  requests are implemented here.
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
- `app/storage/` - path safety helpers, normalization, file sessions, correction
  store.

## Storage model

Configured storage:

- `DATABASE_URL`, default `sqlite:///data/app.db`.
- `DATA_DIR`, default `data`.
- `APP_STORAGE_DIR`, `TMP_STORAGE_DIR`, `EXPORT_STORAGE_DIR`, `DEBUG_STORAGE_DIR`
  are configured but not fully used by all pipelines yet.
- `OBSIDIAN_VAULT` points to the local/synced vault used for human-readable
  exports.

Current persistent files:

- `Users/<telegram_user_id>/Receipts/YYYY/MM/<file>.md`
- `Users/<telegram_user_id>/Attachments/receipts/YYYY/MM/<file>.jpg`
- `Users/<telegram_user_id>/OCR/YYYY/MM/<file>.clean.hy.txt`
- `Users/<telegram_user_id>/OCR_VERIFIED/YYYY/MM/<file>.verified.hy.txt`
- `Users/<telegram_user_id>/MANIFEST/receipts/YYYY/MM/<file>.manifest.json`
- `Users/<telegram_user_id>/DEBUG/openai/...` only for invalid OpenAI JSON
- `data/app.db` for users, access requests, and `usage_events`
- `data/sessions/` for review sessions
- `data/corrections.json` for scoped correction rules

Target storage direction:

- Keep only value-bearing files permanently.
- Move temporary processing files into `data/tmp/<document_id>/`.
- Store canonical document data, items, files, sessions, quotas, and correction
  rules in SQLite.
- Obsidian exports should be generated from SQLite/parsed JSON, not used as the
  main data source.
- Original receipt images must be preserved unless a later explicit decision
  changes the file retention policy.
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
- Legacy `data/access.json` is imported once when present.

Current quotas:

- Admins are unlimited.
- Privileged users are unlimited by default.
- Regular users have daily/monthly attempt limits.
- Quotas are stored in SQLite `usage_events` as `receipt_attempt` events.
- Attempts are recorded after access and limit checks, before image download,
  OCR, and OpenAI.
- Admin and privileged attempts are also recorded for audit even when their
  limits are unlimited.
- Legacy JSON counters in `data/usage` are not imported and are cleaned up.

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

## Current limitations

- Receipt documents are not yet persisted through DB repositories despite the
  schema existing.
- `document_items`, `document_files`, `processing_sessions`,
  `correction_rules`, `magic_links`, and `web_sessions` are schema-level
  foundations, not fully wired into runtime logic yet.
- Review sessions are still file-based in `data/sessions`.
- Correction rules are still stored in `data/corrections.json`.
- Temporary image/OCR files are still placed under the Obsidian vault `_tmp`
  path during processing.
- Obsidian export still includes `OCR_VERIFIED` even though manual review is now
  on Russian fields, not Armenian OCR.
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
