from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class UserStatus(str, Enum):
    ALLOWED = "allowed"
    PENDING = "pending"
    REJECTED = "rejected"
    REVOKED = "revoked"


class UserRole(str, Enum):
    ADMIN = "admin"
    PRIVILEGED = "privileged"
    REGULAR = "regular"


@dataclass(frozen=True, slots=True)
class AccessRequest:
    user_id: int
    full_name: str
    username: str
    created_at: datetime

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "AccessRequest":
        return cls(
            user_id=int(data["user_id"]),
            full_name=str(data.get("full_name", "")),
            username=str(data.get("username", "")),
            created_at=datetime.fromisoformat(str(data["created_at"])),
        )


@dataclass(frozen=True, slots=True)
class UserProfile:
    user_id: int
    full_name: str
    username: str
    status: UserStatus
    role: UserRole
    vault_root: str
    created_at: datetime
    updated_at: datetime
    approved_by: int | None = None
    source: str = "runtime"

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["role"] = self.role.value
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "UserProfile":
        return cls(
            user_id=int(data["user_id"]),
            full_name=str(data.get("full_name", "")),
            username=str(data.get("username", "")),
            status=UserStatus(str(data.get("status", UserStatus.REJECTED.value))),
            role=UserRole(str(data.get("role", UserRole.REGULAR.value))),
            vault_root=str(data.get("vault_root", "")),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            updated_at=datetime.fromisoformat(str(data["updated_at"])),
            approved_by=int(data["approved_by"]) if data.get("approved_by") is not None else None,
            source=str(data.get("source", "runtime")),
        )


def profile_vault_root(user_id: int, root: str = "Users") -> str:
    return f"{root.strip('/')}/{user_id}"

