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

### 2026-05-22 - Permanent image retention policy

**Status:** uncertain  
**Question:** Should the long-term default keep raw original images, optimized
stored images, or both?  
**Context:** The user values original receipt images, but storage cost matters as
users and receipts grow. Earlier planning proposed optimized `stored_image` as
the default and optional `original_image`.  
**Options:** Keep originals always; keep optimized stored images only; keep both
for privileged/admin users; make retention configurable by user/role.  
**Current leaning:** Preserve originals until a DB-backed image policy PR
implements explicit settings and safe migration.  
**Needed to decide:** Measure average image size and OCR/debug value across real
receipts, then choose role/user defaults.

### 2026-05-22 - OCR_VERIFIED retention after Russian-only review

**Status:** uncertain  
**Question:** Should `OCR_VERIFIED` remain a permanent artifact when manual
review is no longer performed on Armenian OCR?  
**Context:** Current writer still creates `OCR_VERIFIED` as a copy of clean OCR,
but the product rule says review happens on Russian note fields.  
**Options:** Stop creating it by default; keep it for compatibility; store only
OCR hash/text in SQLite; make retention configurable.  
**Current leaning:** Stop creating permanent `OCR_VERIFIED` by default once
documents and processing stages are DB-backed.  
**Needed to decide:** Implement DB document storage and define which OCR stages
are valuable for audit/reprocessing.

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
