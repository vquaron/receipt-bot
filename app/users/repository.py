from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.storage.paths import ensure_parent
from app.users.models import AccessRequest, UserProfile


class UserRepository:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "users"
        self.users_path = self.root / "users.json"
        self.requests_path = self.root / "access_requests.json"

    def get(self, user_id: int) -> UserProfile | None:
        return self.load_users().get(str(user_id))

    def list_users(self) -> list[UserProfile]:
        return sorted(self.load_users().values(), key=lambda user: user.user_id)

    def save_profile(self, profile: UserProfile) -> None:
        users = self.load_users()
        users[str(profile.user_id)] = profile
        self._write_json(self.users_path, {key: user.to_json() for key, user in users.items()})

    def delete_profile(self, user_id: int) -> None:
        users = self.load_users()
        if users.pop(str(user_id), None) is not None:
            self._write_json(self.users_path, {key: user.to_json() for key, user in users.items()})

    def load_users(self) -> dict[str, UserProfile]:
        data = self._read_dict(self.users_path)
        users: dict[str, UserProfile] = {}
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            try:
                profile = UserProfile.from_json(value)
            except (KeyError, ValueError):
                continue
            users[str(profile.user_id)] = profile
        return users

    def pending_request(self, user_id: int) -> AccessRequest | None:
        value = self.load_requests()["pending"].get(str(user_id))
        return value if isinstance(value, AccessRequest) else None

    def rejected_request(self, user_id: int) -> AccessRequest | None:
        value = self.load_requests()["rejected"].get(str(user_id))
        return value if isinstance(value, AccessRequest) else None

    def save_pending_request(self, request: AccessRequest) -> None:
        requests = self.load_requests()
        requests["pending"][str(request.user_id)] = request
        requests["rejected"].pop(str(request.user_id), None)
        self._save_requests(requests)

    def pop_pending_request(self, user_id: int) -> AccessRequest | None:
        requests = self.load_requests()
        request = requests["pending"].pop(str(user_id), None)
        self._save_requests(requests)
        return request

    def save_rejected_request(self, request: AccessRequest) -> None:
        requests = self.load_requests()
        requests["pending"].pop(str(request.user_id), None)
        requests["rejected"][str(request.user_id)] = request
        self._save_requests(requests)

    def load_requests(self) -> dict[str, dict[str, AccessRequest]]:
        data = self._read_dict(self.requests_path)
        result: dict[str, dict[str, AccessRequest]] = {"pending": {}, "rejected": {}}
        for bucket in result:
            raw_bucket = data.get(bucket)
            if not isinstance(raw_bucket, dict):
                continue
            for key, value in raw_bucket.items():
                if not isinstance(value, dict):
                    continue
                try:
                    request = AccessRequest.from_json(value)
                except (KeyError, ValueError):
                    continue
                result[bucket][str(request.user_id)] = request
        return result

    def migrate_legacy_access(self, legacy_path: Path, *, user_vault_root: str) -> None:
        if self.users_path.exists() or not legacy_path.exists():
            return
        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(legacy, dict):
            return

        now = datetime.now()
        users: dict[str, dict[str, object]] = {}
        for raw_user_id, payload in _legacy_bucket(legacy, "allowed").items():
            user_id = int(raw_user_id)
            users[str(user_id)] = {
                "user_id": user_id,
                "full_name": _payload_text(payload, "full_name"),
                "username": _payload_text(payload, "username"),
                "status": "allowed",
                "role": "regular",
                "vault_root": f"{user_vault_root.strip('/')}/{user_id}",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "approved_by": None,
                "source": "legacy_access_json",
            }
        self._write_json(self.users_path, users)

        requests = {"pending": {}, "rejected": {}}
        for bucket in ("pending", "rejected"):
            for raw_user_id, payload in _legacy_bucket(legacy, bucket).items():
                user_id = int(raw_user_id)
                requests[bucket][str(user_id)] = {
                    "user_id": user_id,
                    "full_name": _payload_text(payload, "full_name"),
                    "username": _payload_text(payload, "username"),
                    "created_at": now.isoformat(),
                }
        self._write_json(self.requests_path, requests)

    def _save_requests(self, requests: dict[str, dict[str, AccessRequest]]) -> None:
        self._write_json(
            self.requests_path,
            {
                bucket: {key: request.to_json() for key, request in values.items()}
                for bucket, values in requests.items()
            },
        )

    def _read_dict(self, path: Path) -> dict[str, object]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_json(self, path: Path, data: dict[str, object]) -> None:
        ensure_parent(path)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _legacy_bucket(data: dict[str, object], name: str) -> dict[str, object]:
    value = data.get(name)
    if isinstance(value, dict):
        return {str(key): payload for key, payload in value.items()}
    if isinstance(value, list):
        return {str(item): {} for item in value}
    return {}


def _payload_text(payload: object, key: str) -> str:
    if isinstance(payload, dict):
        return str(payload.get(key, ""))
    return ""

