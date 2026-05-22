# Next Steps

## Immediate next steps

- [ ] PR6: implement file retention and image policy.
  - Context: Storage cost matters. Current files preserve original image, clean
    OCR, source OCR, and Obsidian export artifacts for new receipts.
  - Expected outcome: define `stored_image` vs `original_image`; strip EXIF from
    optimized stored images if enabled; record sha256/size/mime; keep originals
    according to explicit settings; stop keeping no-value temp files.
  - Depends on: DB-first `document_files`.

- [ ] Keep docs context updated after every substantial PR.
  - Context: `docs/` now acts as persistent project memory.
  - Expected outcome: future sessions can read `PROJECT_STATE`, `DECISIONS`, and
    `NEXT_STEPS` before doing nontrivial work.
  - Depends on: Developer discipline.

## Open questions

- [ ] What is the final image retention default?
  - Context: Original images are valuable for audit and user trust, but storage
    cost grows with users.
  - Options: Keep original always; keep optimized `stored_image` only; keep both
    by role/user setting; configurable retention.
  - Current leaning: Preserve originals until an explicit DB-backed image policy
    PR changes this safely.
  - Needed to decide: Measure real average image size and compare OCR/debug value
    against storage cost.

- [ ] Should `OCR_VERIFIED` continue as a permanent file?
  - Context: Manual review now happens on Russian fields, not Armenian OCR, so
    permanent `OCR_VERIFIED` may be a misleading duplicate.
  - Options: Remove by default; keep for compatibility; store OCR text/hash only
    in SQLite; make retention configurable.
  - Current leaning: Stop creating `OCR_VERIFIED` by default after DB-backed
    documents and processing stages are implemented.
  - Needed to decide: Define DB fields and retention for OCR stages.

- [ ] What should the first PWA/API include?
  - Context: PWA/API should consume SQLite, not Markdown.
  - Options: Read-only list/detail/image; filters/search; export/delete;
    correction rule management.
  - Current leaning: Magic-link login plus read-only document list, detail,
    items, and image endpoint.
  - Needed to decide: Finish DB-backed documents/files and choose the first
    mobile workflow.

## Backlog

- [ ] Move correction rules from `data/corrections.json` to SQLite
  `correction_rules`.
  - Why it matters: Rules need scoped uniqueness, usage counts, future editing,
    and safer application.
  - Priority: high

- [ ] Add cleanup for `data/exports` and debug retention.
  - Why it matters: Storage cost and stale sensitive files will grow otherwise.
  - Priority: medium

- [ ] Add magic-link `/web` flow and minimal web session repository.
  - Why it matters: This is the likely bridge to mobile PWA.
  - Priority: medium

- [ ] Add read-only API/PWA after DB-backed documents exist.
  - Why it matters: It makes receipts usable on mobile without relying on
    Obsidian sync.
  - Priority: medium

- [ ] Add FTS/search for merchants, summaries, and item names.
  - Why it matters: Search and analytics become valuable once many receipts are
    stored.
  - Priority: low

## Risks to watch

- DB schema can get ahead of runtime code; docs must distinguish implemented
  behavior from planned schema.
- Legacy JSON access import should not be reintroduced; `.env` bootstrap and
  SQLite-backed admin approval are the supported access paths.
- Markdown/manifest fallback should not quietly become the source of truth again.
- Temporary vault files and debug files can leak storage and private data if not
  cleaned up.
- Telegram handlers can become too stateful unless business logic moves into
  services/repositories.
- Correction rules can corrupt data if they become global replacements instead
  of scoped mappings.
- SQLite migrations must stay atomic and idempotent.

## Parking lot

- Role-specific storage retention settings.
- Admin dashboard or PWA controls for correction rules.
- SQLite FTS5 for item and merchant search.
- Audit log for access decisions, deletions, exports, and web logins.
- Health check for DB/export drift: missing files, orphan files, manifest
  mismatch.
