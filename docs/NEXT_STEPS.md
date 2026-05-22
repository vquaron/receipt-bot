# Next Steps

## Immediate next steps

- [ ] PR11: add FTS/search for merchants, summaries, and item names.
  - Context: Web MVP provides read-only list/detail/image views, but search is
    still not DB-backed.
  - Expected outcome: SQLite FTS5 indexes merchant, summary, and item names;
    API/PWA exposes search/filtering without parsing Markdown.
  - Depends on: DB-backed documents/items and Web MVP.

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

- [x] What should the first PWA/API include?
  - Context: PWA/API should consume SQLite, not Markdown.
  - Options: Read-only list/detail/image; filters/search; export/delete;
    correction rule management.
  - Decision: Magic-link login plus read-only DB document list, detail, items,
    and image endpoint. Legacy manifest receipts are excluded from Web MVP.

## Backlog

- [ ] Add storage repair/backfill tooling.
  - Why it matters: Health checks now report drift, but repair should remain a
    separate dry-run-first operation.
  - Priority: medium

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
