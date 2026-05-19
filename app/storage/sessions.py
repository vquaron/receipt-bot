from __future__ import annotations

import json
from pathlib import Path

from app.review.models import ReceiptSession
from app.storage.paths import ensure_parent


class SessionStore:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> dict[int, ReceiptSession]:
        sessions: dict[int, ReceiptSession] = {}
        for path in self.root.glob("*.json"):
            try:
                session = ReceiptSession.from_json(json.loads(path.read_text(encoding="utf-8")))
            except (KeyError, ValueError, json.JSONDecodeError):
                continue
            sessions[session.user_id] = session
        return sessions

    def save(self, session: ReceiptSession) -> None:
        path = self._path(session.user_id)
        ensure_parent(path)
        path.write_text(
            json.dumps(session.to_json(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def delete(self, user_id: int) -> None:
        path = self._path(user_id)
        if path.exists():
            path.unlink()

    def _path(self, user_id: int) -> Path:
        return self.root / f"{user_id}.json"
