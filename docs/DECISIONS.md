# Decisions

## Active decisions

### 2026-05-22 - Persistent project context in docs

**Status:** active  
**Decision:** Keep durable project context in `docs/PROJECT_STATE.md`,
`docs/DECISIONS.md`, `docs/TASK_LOG.md`, and `docs/NEXT_STEPS.md`.  
**Context:** Long Codex sessions, compaction, PR handoffs, and multi-tool work
make it easy to lose architectural reasoning that is not in the repository.  
**Reason:** Future sessions need a compact, repository-native context that
records current state, durable decisions, meaningful work, and next actions.  
**Alternatives considered:** Rely only on chat history, README, or Git history.
Chat history can be unavailable or compacted; README is user-facing; Git history
does not explain all product constraints.  
**Impact:** Any meaningful architecture, storage, authorization, data model,
business logic, API, integration, deployment, or testing change should update
the relevant docs file.  
**Review trigger:** Revisit if the docs become noisy, stale, or are replaced by a
formal ADR/project management workflow.

### 2026-05-21 - SQLite as source of truth

**Status:** active  
**Decision:** SQLite is the primary source of truth for structured application
data.  
**Context:** The project began as a file/Obsidian-oriented MVP, then grew toward
multi-user access, quotas, sessions, future PWA/API, and structured search.  
**Reason:** Obsidian is useful for readable exports, but it is not a reliable
primary store for authorization, quotas, sessions, item search, API access, or
future analytics. SQLite gives strong enough structure without adding a separate
database service.  
**Alternatives considered:** Continue with JSON files and Markdown manifests;
move directly to PostgreSQL; keep Obsidian as the only source. JSON/Markdown do
not scale well for the planned model; PostgreSQL is unnecessary for the MVP.  
**Impact:** Users, access requests, documents, items, files, sessions, usage
events, correction rules, magic links, and web sessions should be designed
around SQLite repositories.  
**Review trigger:** Revisit if concurrent writes, analytics load, or multi-node
deployment exceed SQLite's practical limits.

### 2026-05-21 - Obsidian as export / representation

**Status:** active  
**Decision:** Obsidian Markdown is a generated export and human-readable
representation, not primary application storage.  
**Context:** Users still want Obsidian notes, but future UI, API, search, and
sharing require structured data.  
**Reason:** Markdown is excellent for reading and manual archival work, but
parsing it as app state would make schema evolution, permissions, and PWA work
fragile.  
**Alternatives considered:** Keep parsing Markdown and manifests for app state;
drop Obsidian entirely. Parsing Markdown is brittle; dropping Obsidian would
remove a useful user-facing archive.  
**Impact:** New document features should write/read SQLite first and generate
Obsidian notes as export artifacts. Delete should become DB-first with manifest
fallback for legacy receipts.  
**Review trigger:** Revisit if Obsidian export is no longer needed by users.

### 2026-05-21 - Store processing stages

**Status:** active  
**Decision:** Receipt processing should store meaningful intermediate states, not
only final note fields.  
**Context:** OCR, LLM parsing, Russian review, user corrections, and export can
all fail or improve over time.  
**Reason:** Intermediate state enables auditability, safer retries, correction
rule learning, prompt/schema version analysis, and future reprocessing.  
**Alternatives considered:** Store only final Markdown or final parsed JSON. That
would be smaller but would make debugging and quality improvements much harder.  
**Impact:** The DB model should account for original image, OCR text/hash,
review payload, parsed JSON, possible errors, document files, parser version,
schema version, prompt version, and processing session state.  
**Review trigger:** Revisit when storage pressure requires a stricter retention
policy for OCR/debug artifacts.

### 2026-05-22 - SQLite processing sessions and temp outside vault

**Status:** active
**Decision:** Active Telegram review/correction sessions are stored in SQLite
`processing_sessions`, while temporary image/OCR files live under
`data/tmp/processing/<session_id>/` instead of the Obsidian vault.
**Context:** File sessions in `data/sessions` and vault `_tmp` files made
restart recovery and cleanup weaker, and placed temporary private artifacts in
the human-readable export tree.
**Reason:** SQLite sessions make review state durable and queryable without
turning Markdown/manifest files back into application state. Keeping temp files
outside Obsidian prevents temporary processing artifacts from syncing as archive
data.
**Alternatives considered:** Continue using `data/sessions`; migrate legacy
sessions automatically; keep temp files under vault `_tmp`. JSON sessions are
less reliable for the DB-first model; legacy sessions are transient enough to
ignore; vault temp files are noisy and easy to leak through sync.
**Impact:** New photo handling blocks if a waiting review/correction session is
active, before quota/download/OCR/OpenAI. Waiting review/correction sessions are
restored after restart; stale OCR/OpenAI sessions are marked failed and cleaned.
Legacy `data/sessions/*.json` is not imported.
**Review trigger:** Revisit if the project later adds resumable background
processing or multi-session review UX.

### 2026-05-22 - Canonical document files outside Obsidian

**Status:** active
**Decision:** For newly confirmed documents, canonical receipt/order files are
recorded in SQLite `document_files` and live outside the Obsidian vault.
Canonical OCR files currently live under `APP_STORAGE_DIR/documents/<document_id>/`;
canonical images use the configured local or S3-compatible image backend.
Obsidian files are export artifacts.
**Context:** DB-first document persistence needs stable document ids and
queryable file metadata without treating the Obsidian vault as primary storage.
**Reason:** Application-controlled storage keeps canonical private artifacts
outside the human-readable export tree, while Obsidian can remain a readable
export that can be regenerated or omitted later.
**Alternatives considered:** Keep moving canonical files directly into the vault;
store only paths without hashes/metadata; move all exports out of Obsidian.
Keeping canonical files in the vault would preserve the old source-of-truth
ambiguity; path-only records are weaker for audit; dropping Obsidian export is
not desired for the MVP.
**Impact:** New confirm flows record `original_image`, `stored_image`,
`clean_ocr`, and `source_ocr` file rows, and create Obsidian note/image rows
only as export files. New manifest JSON files are not created.
**Review trigger:** Revisit in the file retention/image policy PR if optimized
images, EXIF stripping, or original-image retention settings change the storage
contract.

### 2026-05-22 - Generic storage refs for canonical images

**Status:** active
**Decision:** `document_files` records use generic storage references
(`storage_backend`, `storage_key`, `bucket`, checksum metadata, and canonical
flag) for new document files. Canonical receipt images may live in local storage
for development or private S3-compatible storage such as Backblaze B2 in
production.
**Context:** Receipt images are large immutable binaries and fit object storage
well, while SQLite should remain the source of truth for ownership, metadata,
and file references.
**Reason:** This keeps DB-first architecture intact without treating S3 as a
mounted filesystem or making public URLs canonical application state.
**Alternatives considered:** Keep `data/storage` as the only canonical image
location; mount S3 via rclone/FUSE; store public URLs in SQLite. Local-only
storage does not scale as well; mounted object storage has weak filesystem
semantics; public URLs are brittle and less private.
**Impact:** New image flows use a storage abstraction. `original_image` and
`stored_image` can be stored with `storage_backend='s3'`; OCR files remain local
for now; Obsidian images remain export artifacts.
**Review trigger:** Revisit if OCR artifacts also move to object storage or if
the project needs multi-provider storage policies per user/role.

### 2026-05-22 - Soft-delete DB documents after file deletion

**Status:** active
**Decision:** DB-first document deletion removes files recorded in
`document_files`, but keeps the document, item, and file rows with
`documents.status='deleted'` and `deleted_at` set.
**Context:** New receipts no longer have manifest JSON as their source of truth,
so deletion must use SQLite file records while still preserving enough audit
history to understand what happened.
**Reason:** Soft-deleted rows keep document identity, ownership, and file
metadata available for audit and future health checks, while removing the
private file payloads from storage.
**Alternatives considered:** Hard-delete DB rows with cascade; only mark rows
deleted and keep files. Hard delete loses audit/debug context; soft-only delete
does not address storage and privacy cleanup.
**Impact:** Normal listings hide deleted documents. Admin/user delete validates
all recorded file paths before removing files, treats missing files as
non-fatal, and only then marks the DB row deleted.
**Review trigger:** Revisit if a future formal audit log makes soft-deleted
document rows redundant or if retention policy requires delayed deletion.

### 2026-05-22 - Runtime retention cleanup for non-canonical artifacts

**Status:** active
**Decision:** Runtime cleanup removes old export ZIPs, debug artifacts, and
materialized temp/cache files, but does not delete canonical document files.
**Context:** S3-backed images introduced temporary materialized local copies, and
export/debug files can contain private receipt data while growing without bound.
**Reason:** Non-canonical artifacts should not become accidental long-term
storage. Canonical files remain controlled by `document_files` delete/retention
policy instead of generic age-based cleanup.
**Alternatives considered:** Leave cleanup manual; delete all files under
`data/tmp`; keep OpenAI debug in Obsidian. Manual cleanup is easy to forget;
broad tmp cleanup risks active sessions; vault debug files can sync private raw
LLM output.
**Impact:** Startup cleanup applies configured retention to
`EXPORT_STORAGE_DIR`, `DEBUG_STORAGE_DIR`, and selected temp cache directories:
`materialized`, `exports`, and `telegram`.
**Review trigger:** Revisit when role-specific retention settings or admin
storage controls are implemented.

### 2026-05-22 - OCR_VERIFIED is legacy-only

**Status:** active
**Decision:** `OCR_VERIFIED` remains supported for legacy Obsidian/manifest
receipts, but new DB-first documents do not create permanent `OCR_VERIFIED`
files.
**Context:** Manual review now happens on Russian note/export fields, while new
DB-first documents store canonical OCR artifacts in app storage and record them
in SQLite `document_files`.
**Reason:** A permanent `OCR_VERIFIED` copy would imply Armenian OCR was manually
verified, which is no longer the product workflow. Keeping legacy fallback avoids
breaking old receipts.
**Alternatives considered:** Continue creating `OCR_VERIFIED` for every new
receipt; remove all legacy support; store only OCR hashes. Continuing creates
misleading duplicates; removing legacy support would break old archives; hashes
alone are not enough for reprocessing/debug.
**Impact:** Legacy delete/copy/export can still see `OCR_VERIFIED`; DB-first
export uses canonical OCR file records and does not expose OCR in Markdown by
default.
**Review trigger:** Revisit if future review UX starts explicitly verifying raw
OCR text again.

### 2026-05-21 - Telegram as current review UI

**Status:** active  
**Decision:** Telegram remains the current upload and manual review interface for
the MVP.  
**Context:** Telegram provides the fastest usable flow for sending receipt
photos and confirming/correcting extracted Russian fields.  
**Reason:** It avoids building a web UI before the data model is stable while
still allowing human-in-the-loop review.  
**Alternatives considered:** Build PWA first; use Obsidian-only manual edits.
Both would slow the MVP and complicate access/security work.  
**Impact:** Telegram handlers should not own core business rules permanently;
logic should be factored into services/repositories that future API/PWA code can
reuse.  
**Review trigger:** Revisit after DB-backed documents and magic-link web auth are
implemented.

### 2026-05-21 - Correction rules as durable scoped data

**Status:** active  
**Decision:** Correction rules are durable, scoped data rather than prompt text
or unsafe global replacement.  
**Context:** Manual Russian field corrections should improve later receipts, for
example normalizing merchant names, units, and product names.  
**Reason:** Rules need to be inspectable, reusable, counted, constrained by
scope, and eventually editable. Prompt-only memory is not reliable and global
string replacement can corrupt unrelated fields.  
**Alternatives considered:** Put all corrections into OpenAI prompts; perform
global string replacement; do not learn corrections. These approaches are less
safe and less debuggable.  
**Impact:** Current rules live in `data/corrections.json`, but the schema already
contains `correction_rules` with scoped uniqueness. A future PR should migrate
runtime correction storage to SQLite.  
**Review trigger:** Revisit when correction rules need merchant-specific,
language-specific, or user-specific behavior.

### 2026-05-21 - Access requests in SQLite

**Status:** active  
**Decision:** Users and Telegram access requests are stored in SQLite instead of
new JSON user/access files.  
**Context:** Access control gates expensive and private operations, so it must be
consistent and restart-safe.  
**Reason:** SQLite allows uniqueness, status queries, pending request constraints,
and later integration with web sessions and audit logic.  
**Alternatives considered:** Keep `data/access.json` or `data/users/*.json`.
Those files are simpler but are weak for multi-user growth and state
transitions.  
**Impact:** `app.repositories.users` and `app.repositories.access_requests`
back the current access service. Legacy `data/access.json` is not imported
automatically; `.env` bootstrap and Telegram admin actions are the supported
runtime paths.
**Review trigger:** Revisit if roles/permissions expand enough to require a more
formal policy layer.

### 2026-05-22 - Event-based quotas in SQLite

**Status:** active
**Decision:** Quotas are enforced through SQLite `usage_events` using
`event_type='receipt_attempt'`. Old JSON quota counters in `data/usage` are not
imported and are removed safely when quota storage initializes.
**Context:** Runtime quota logic used JSON counters, while the DB schema already
had `usage_events` for durable usage tracking.
**Reason:** Event rows are easier to audit, extend, and query than mutable JSON
counters. Resetting old counters keeps PR3 simple and explicit.
**Alternatives considered:** Import legacy JSON counters; keep JSON fallback;
count only successful receipts. Import/fallback would complicate the MVP;
success-only counting would not protect OCR/OpenAI cost.
**Impact:** Telegram photo handling must use an atomic quota check-and-record
operation before downloading the image. Admin and privileged attempts are
recorded for audit even when unlimited. Usage events store a role snapshot and
should be updated to the final `document_type` if OCR-based classification
changes the initial type.
**Review trigger:** Revisit if users need historical usage migration or if
future pricing requires separate limits for OCR/OpenAI calls.

### 2026-05-22 - SQLite schema can lead runtime implementation

**Status:** active
**Decision:** The SQLite schema may include planned tables/columns before the
runtime pipeline fully uses them, as long as docs distinguish implemented
behavior from target architecture.
**Context:** PR1 introduced document, file, session, correction, magic-link, and
web-session foundations before runtime migration reached those layers.
**Reason:** A schema-ahead foundation keeps later PRs smaller while preserving a
coherent storage direction.
**Alternatives considered:** Add tables only when each runtime feature lands.
That would reduce unused schema but make each storage PR noisier.
**Impact:** Future docs must keep "current implementation" and "target storage"
separate so planned schema is not mistaken for wired behavior.
**Review trigger:** Revisit if schema drift becomes confusing or unused tables
block migration changes.

### 2026-05-22 - Telegram owner id without users foreign key

**Status:** active
**Decision:** Document ownership is represented by stable
`owner_telegram_user_id` values rather than a foreign key to `users`.
**Context:** `users` is an access/profile table whose rows can be revoked,
recreated, or bootstrapped from `.env`, while documents should remain tied to
the external Telegram identity.
**Reason:** This avoids coupling receipt ownership to mutable access state and
keeps imported/exported document ownership simple.
**Alternatives considered:** Add a DB foreign key from documents to users. That
would enforce referential integrity but make access/profile cleanup riskier for
document history.
**Impact:** Repository/business logic must enforce user visibility; SQLite will
not enforce document owner existence through a users FK.
**Review trigger:** Revisit if the project introduces internal immutable user
ids distinct from Telegram ids.

### 2026-05-22 - Local server timestamps for MVP

**Status:** active
**Decision:** Runtime DB timestamps use local server time for the current MVP.
**Context:** Existing quota limits and processing timestamps already follow
`datetime.now()` behavior.
**Reason:** This keeps day/month quota windows aligned with current production
behavior and avoids mixing timezone policies mid-migration.
**Alternatives considered:** Store UTC everywhere immediately. UTC is cleaner
long-term, but changing it now would require a wider timestamp policy migration.
**Impact:** Quota windows use half-open local day/month ranges. Future API/PWA
work should revisit display and storage timezone policy before public web auth.
**Review trigger:** Revisit before multi-timezone users, analytics, or public API
date filtering.

### 2026-05-22 - File stem as export identity

**Status:** active
**Decision:** `documents.id` is the canonical document identity; `file_stem` is a
human-readable export/display identity used for Markdown, manifests, and file
paths.
**Context:** Receipt filenames need readable merchant/date/amount stems, but
those values can collide or change after review.
**Reason:** Separating canonical id from export stem keeps DB references stable
while retaining readable exported files.
**Alternatives considered:** Use filename stem as primary identity. That would
make renames/collisions harder and leak display concerns into DB relationships.
**Impact:** Future DB-first document code should use document id for internal
links and `file_stem` only for export/file naming.
**Review trigger:** Revisit if export naming becomes user-editable or if multiple
exports per document are introduced.

### 2026-05-21 - Atomic per-migration SQLite changes

**Status:** active  
**Decision:** Each SQLite migration must be applied atomically and recorded only
after its SQL succeeds.  
**Context:** A failed migration can otherwise leave partial tables/indexes while
claiming success or making retry behavior unclear.  
**Reason:** Atomic migrations make local/server deploys safer and easier to
debug.  
**Alternatives considered:** Rely on autocommit scripts; use a full migration
framework immediately. Autocommit is unsafe; a larger framework is more than the
MVP needs right now.  
**Impact:** `app/db/migrations.py` starts a transaction per migration and tests
cover failed migration rollback.  
**Review trigger:** Revisit if migrations become complex enough to need
Alembic or another dedicated migration tool.

## Superseded decisions

### 2026-05-21 - File JSON as primary access storage

**Status:** superseded  
**Original decision:** Store allowlist, pending requests, rejected requests, and
runtime user data in JSON files under `data/`.  
**Superseded by:** `2026-05-21 - Access requests in SQLite`.  
**Reason for change:** Access state became important enough for durable
constraints, future web auth, and multi-user growth.

### 2026-05-22 - JSON counters as primary quota storage

**Status:** superseded
**Original decision:** Store daily/monthly quota counters in
`data/usage/YYYY-MM/<user_id>.json`.
**Superseded by:** `2026-05-22 - Event-based quotas in SQLite`.
**Reason for change:** Quota usage needs durable event history, auditing, and a
path toward future analytics.

## Uncertain / pending decisions

### 2026-05-22 - Scope of first PWA API

**Status:** uncertain  
**Question:** What is the smallest useful read-only API/PWA surface after
SQLite-backed documents exist?  
**Context:** PWA should not be built on Markdown, but the exact MVP can vary.  
**Options:** List/detail/image only; include filters/search; include export and
delete; include correction rule management.  
**Current leaning:** Start with magic-link login plus read-only document list,
detail, items, and image endpoints.  
**Needed to decide:** Complete document/file repositories and clarify first
mobile use case.

<!--
### YYYY-MM-DD - Short decision title

**Status:** active / superseded / uncertain  
**Decision:**  
**Context:**  
**Reason:**  
**Alternatives considered:**  
**Impact:**  
**Review trigger:**  
-->
