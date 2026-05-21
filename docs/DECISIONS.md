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
back the current access service. Legacy `data/access.json` is only an import
source.  
**Review trigger:** Revisit if roles/permissions expand enough to require a more
formal policy layer.

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

