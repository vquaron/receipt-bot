from __future__ import annotations

from datetime import datetime

from telegram import User

from app.config import Settings
from app.users.models import AccessRequest, UserProfile, UserRole, UserStatus, profile_vault_root
from app.users.repository import UserRepository


class AccessControl:
    def __init__(self, settings: Settings, repository: UserRepository | None = None) -> None:
        self.settings = settings
        self.repository = repository or UserRepository(settings.data_dir)
        self.repository.migrate_legacy_access(settings.data_dir / "access.json", user_vault_root=settings.user_vault_root)
        self._bootstrap_env_users()

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.settings.admin_telegram_user_ids

    def is_privileged(self, user_id: int) -> bool:
        if self.is_admin(user_id) or user_id in self.settings.privileged_telegram_user_ids:
            return True
        profile = self.repository.get(user_id)
        return bool(profile and profile.role == UserRole.PRIVILEGED and profile.status == UserStatus.ALLOWED)

    def role_for(self, user_id: int) -> UserRole:
        if self.is_admin(user_id):
            return UserRole.ADMIN
        profile = self.repository.get(user_id)
        if profile:
            return profile.role
        if user_id in self.settings.privileged_telegram_user_ids:
            return UserRole.PRIVILEGED
        return UserRole.REGULAR

    def profile_for(self, user_id: int) -> UserProfile | None:
        return self.repository.get(user_id)

    def is_allowed(self, user_id: int) -> bool:
        if self.is_admin(user_id):
            return True
        profile = self.repository.get(user_id)
        return bool(profile and profile.status == UserStatus.ALLOWED)

    def is_pending(self, user_id: int) -> bool:
        return self.repository.pending_request(user_id) is not None

    def create_request(self, user: User) -> tuple[AccessRequest, bool]:
        now = datetime.now()
        request = AccessRequest(
            user_id=user.id,
            full_name=user.full_name,
            username=user.username or "",
            created_at=now,
        )
        if self.is_allowed(request.user_id) or self.is_pending(request.user_id):
            return request, False
        self.repository.save_pending_request(request)
        return request, True

    def approve(self, user_id: int, *, approved_by: int | None = None, role: UserRole | None = None) -> None:
        now = datetime.now()
        request = self.repository.pop_pending_request(user_id)
        existing = self.repository.get(user_id)
        resolved_role = role or self._default_role(user_id)
        profile = UserProfile(
            user_id=user_id,
            full_name=request.full_name if request else (existing.full_name if existing else ""),
            username=request.username if request else (existing.username if existing else ""),
            status=UserStatus.ALLOWED,
            role=resolved_role,
            vault_root=profile_vault_root(user_id, self.settings.user_vault_root),
            created_at=existing.created_at if existing else now,
            updated_at=now,
            approved_by=approved_by,
            source="runtime",
        )
        self.repository.save_profile(profile)

    def reject(self, user_id: int) -> None:
        request = self.repository.pop_pending_request(user_id)
        if request is None:
            now = datetime.now()
            profile = self.repository.get(user_id)
            request = AccessRequest(
                user_id=user_id,
                full_name=profile.full_name if profile else "",
                username=profile.username if profile else "",
                created_at=now,
            )
        self.repository.save_rejected_request(request)
        self._set_status(user_id, UserStatus.REJECTED)

    def revoke(self, user_id: int) -> bool:
        if self.is_admin(user_id):
            return False
        profile = self.repository.get(user_id)
        if profile is None or profile.status != UserStatus.ALLOWED:
            return False
        self._set_status(user_id, UserStatus.REVOKED)
        return True

    def allowed_users_text(self) -> str:
        users = [
            user for user in self.repository.list_users()
            if user.status == UserStatus.ALLOWED
        ]
        if not users:
            return "Allowed users: empty"
        lines = ["Allowed users:"]
        for user in users:
            username = f" @{user.username}" if user.username else ""
            lines.append(f"{user.user_id} [{user.role.value}]{username}")
        return "\n".join(lines)

    def _set_status(self, user_id: int, status: UserStatus) -> None:
        profile = self.repository.get(user_id)
        if profile is None:
            return
        self.repository.save_profile(
            UserProfile(
                user_id=profile.user_id,
                full_name=profile.full_name,
                username=profile.username,
                status=status,
                role=profile.role,
                vault_root=profile.vault_root,
                created_at=profile.created_at,
                updated_at=datetime.now(),
                approved_by=profile.approved_by,
                source=profile.source,
            )
        )

    def _bootstrap_env_users(self) -> None:
        for user_id in sorted(
            set(self.settings.admin_telegram_user_ids)
            | set(self.settings.allowed_telegram_user_ids)
            | set(self.settings.privileged_telegram_user_ids)
        ):
            profile = self.repository.get(user_id)
            role = self._default_role(user_id)
            if profile and profile.status == UserStatus.ALLOWED and profile.role == role:
                continue
            now = datetime.now()
            self.repository.save_profile(
                UserProfile(
                    user_id=user_id,
                    full_name=profile.full_name if profile else "",
                    username=profile.username if profile else "",
                    status=UserStatus.ALLOWED,
                    role=role,
                    vault_root=profile_vault_root(user_id, self.settings.user_vault_root),
                    created_at=profile.created_at if profile else now,
                    updated_at=now,
                    approved_by=profile.approved_by if profile else None,
                    source="env",
                )
            )

    def _default_role(self, user_id: int) -> UserRole:
        if user_id in self.settings.admin_telegram_user_ids:
            return UserRole.ADMIN
        if user_id in self.settings.privileged_telegram_user_ids:
            return UserRole.PRIVILEGED
        return UserRole.REGULAR

