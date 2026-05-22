from __future__ import annotations

from datetime import datetime

from app.config import Settings
from app.db import initialize_database
from app.db.connection import connect_database
from app.repositories.access_requests import AccessRequestRepository
from app.users.models import AccessRequest, UserProfile, UserRole, UserStatus, profile_vault_root


class UserRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        initialize_database(settings)
        self.requests = AccessRequestRepository(settings)

    def get(self, user_id: int) -> UserProfile | None:
        with connect_database(self.settings) as connection:
            row = connection.execute(
                """
                select telegram_user_id, username, full_name, status, role, created_at, updated_at, approved_by, source
                from users
                where telegram_user_id = ?
                """,
                (user_id,),
            ).fetchone()
        return self._profile_from_row(row) if row is not None else None

    def list_users(self) -> list[UserProfile]:
        with connect_database(self.settings) as connection:
            rows = connection.execute(
                """
                select telegram_user_id, username, full_name, status, role, created_at, updated_at, approved_by, source
                from users
                order by telegram_user_id
                """
            ).fetchall()
        return [self._profile_from_row(row) for row in rows]

    def save_profile(self, profile: UserProfile) -> None:
        rejected_at = profile.updated_at.isoformat() if profile.status == UserStatus.REJECTED else None
        revoked_at = profile.updated_at.isoformat() if profile.status == UserStatus.REVOKED else None
        with connect_database(self.settings) as connection:
            connection.execute(
                """
                insert into users(
                    telegram_user_id,
                    username,
                    full_name,
                    role,
                    status,
                    created_at,
                    updated_at,
                    approved_by,
                    rejected_at,
                    revoked_at,
                    source
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(telegram_user_id) do update set
                    username = excluded.username,
                    full_name = excluded.full_name,
                    role = excluded.role,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    approved_by = excluded.approved_by,
                    rejected_at = excluded.rejected_at,
                    revoked_at = excluded.revoked_at,
                    source = excluded.source
                """,
                (
                    profile.user_id,
                    profile.username,
                    profile.full_name,
                    profile.role.value,
                    profile.status.value,
                    profile.created_at.isoformat(),
                    profile.updated_at.isoformat(),
                    profile.approved_by,
                    rejected_at,
                    revoked_at,
                    profile.source,
                ),
            )

    def delete_profile(self, user_id: int) -> None:
        with connect_database(self.settings) as connection:
            connection.execute("delete from users where telegram_user_id = ?", (user_id,))

    def load_users(self) -> dict[str, UserProfile]:
        return {str(profile.user_id): profile for profile in self.list_users()}

    def pending_request(self, user_id: int) -> AccessRequest | None:
        return self.requests.pending_request(user_id)

    def rejected_request(self, user_id: int) -> AccessRequest | None:
        return self.requests.rejected_request(user_id)

    def save_pending_request(self, request: AccessRequest) -> bool:
        return self.requests.save_pending_request(request)

    def resolve_pending_request(
        self,
        user_id: int,
        *,
        status: str,
        resolved_by: int | None = None,
        decision_reason: str = "",
    ) -> AccessRequest | None:
        return self.requests.resolve_pending_request(
            user_id,
            status=status,
            resolved_by=resolved_by,
            decision_reason=decision_reason,
        )

    def pop_pending_request(self, user_id: int) -> AccessRequest | None:
        return self.resolve_pending_request(user_id, status="cancelled")

    def save_rejected_request(self, request: AccessRequest, *, resolved_by: int | None = None) -> None:
        self.requests.save_rejected_request(request, resolved_by=resolved_by)

    def load_requests(self) -> dict[str, dict[str, AccessRequest]]:
        return self.requests.load_requests()

    def _profile_from_row(self, row) -> UserProfile:
        user_id = int(row["telegram_user_id"])
        return UserProfile(
            user_id=user_id,
            full_name=str(row["full_name"] or ""),
            username=str(row["username"] or ""),
            status=UserStatus(str(row["status"])),
            role=UserRole(str(row["role"])),
            vault_root=profile_vault_root(user_id, self.settings.user_vault_root),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            approved_by=int(row["approved_by"]) if row["approved_by"] is not None else None,
            source=str(row["source"]),
        )
