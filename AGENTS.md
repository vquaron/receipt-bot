# Receipt bot storage policy

- Inbox: `data/inbox/codex`; Sources: `app`, `docs`, `tests`; Work: `data/tmp`; Outputs: `data/exports`; Archive: `data/archive`.
- Never overwrite or automatically delete receipts, recovered attachments, exports, or archives.
- Temporary Codex attachment paths and managed worktrees are not permanent storage.
- Keep personal receipt data, secrets, and environment files out of Git.
- Verify every user-facing export opens before linking it.
