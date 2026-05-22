# Next Steps

## Immediate next steps

- [ ] PR10: add magic-link `/web` flow and read-only API/PWA MVP.
  - Context: SQLite now owns access, quotas, sessions, documents, files, and
    correction rules. The next product step is a minimal web read surface.
  - Expected outcome: Telegram `/web` creates a short-lived magic link;
    `magic_links` and `web_sessions` become runtime-backed; API/PWA supports
    receipt list, detail, items, and image access through DB/document files.
  - Depends on: DB-backed documents/files and current storage backend.

- [ ] Keep docs context updated after every substantial PR.
  - Context: `docs/` now acts as persistent project memory.
  - Expected outcome: future sessions can read `PROJECT_STATE`, `DECISIONS`, and
    `NEXT_STEPS` before doing nontrivial work.
  - Depends on: Developer discipline.

## Open questions

- [x] What is the final image retention default?
  - Context: Original images are valuable for audit and user trust, but storage
    cost grows with users.
  - Decision: Keep both raw `original_image` and optimized `stored_image` for
    new DB documents. Production may store both in private S3/B2; local/dev uses
    `APP_STORAGE_DIR`.

- [x] Should `OCR_VERIFIED` continue as a permanent file?
  - Context: Manual review now happens on Russian fields, not Armenian OCR, so
    permanent `OCR_VERIFIED` may be a misleading duplicate.
  - Decision: `OCR_VERIFIED` remains a legacy Obsidian artifact only. New
    DB-first documents do not create permanent `OCR_VERIFIED`; canonical OCR
    lives in app storage and SQLite `document_files`.

- [ ] What should the first PWA/API include?
  - Context: PWA/API should consume SQLite, not Markdown.
  - Options: Read-only list/detail/image; filters/search; export/delete;
    correction rule management.
  - Current leaning: Magic-link login plus read-only document list, detail,
    items, and image endpoint.
  - Needed to decide: Finish DB-backed documents/files and choose the first
    mobile workflow.

## Backlog

- [ ] Add storage repair/backfill tooling.
  - Why it matters: Health checks now report drift, but repair should remain a
    separate dry-run-first operation.
  - Priority: medium

- [ ] PR11: add FTS/search for merchants, summaries, and item names.
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
- Legacy receipt migration from manifest files into SQLite if needed.
