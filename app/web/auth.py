from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from app.config import Settings
from app.db import initialize_database
from app.db.connection import connect_database


MAGIC_LINK_PURPOSE_WEB_LOGIN = "web_login"


class WebAuthError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MagicLink:
    id: str
    telegram_user_id: int
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class WebSession:
    id: str
    telegram_user_id: int
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class WebSessionProfile:
    id: str
    telegram_user_id: int
    expires_at: datetime
    revoked_at: datetime | None


class WebAuthRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        initialize_database(settings)

    def create_magic_link(
        self,
        telegram_user_id: int,
        *,
        ip_created: str = "",
        created_at: datetime | None = None,
    ) -> MagicLink:
        now = created_at or datetime.now()
        token = secrets.token_urlsafe(32)
        link = MagicLink(
            id=uuid4().hex,
            telegram_user_id=telegram_user_id,
            token=token,
            expires_at=now + timedelta(minutes=max(1, self.settings.web_magic_link_ttl_minutes)),
        )
        with connect_database(self.settings) as connection:
            connection.execute(
                """
                insert into magic_links(
                    id,
                    telegram_user_id,
                    token_hash,
                    purpose,
                    created_at,
                    expires_at,
                    ip_created
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link.id,
                    telegram_user_id,
                    token_hash(token),
                    MAGIC_LINK_PURPOSE_WEB_LOGIN,
                    now.isoformat(),
                    link.expires_at.isoformat(),
                    ip_created,
                ),
            )
        return link

    def redeem_magic_token(
        self,
        token: str,
        *,
        user_agent: str = "",
        ip_address: str = "",
        now: datetime | None = None,
    ) -> WebSession:
        now = now or datetime.now()
        session_token = secrets.token_urlsafe(32)
        session = WebSession(
            id=uuid4().hex,
            telegram_user_id=0,
            token=session_token,
            expires_at=now + timedelta(days=max(1, self.settings.web_session_ttl_days)),
        )
        with connect_database(self.settings) as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                """
                select *
                from magic_links
                where token_hash = ? and purpose = ?
                limit 1
                """,
                (token_hash(token), MAGIC_LINK_PURPOSE_WEB_LOGIN),
            ).fetchone()
            if row is None:
                raise WebAuthError("Magic link is invalid.")
            if row["used_at"] or row["revoked_at"]:
                raise WebAuthError("Magic link has already been used.")
            if datetime.fromisoformat(str(row["expires_at"])) <= now:
                raise WebAuthError("Magic link has expired.")
            user_id = int(row["telegram_user_id"])
            session = WebSession(
                id=session.id,
                telegram_user_id=user_id,
                token=session_token,
                expires_at=session.expires_at,
            )
            connection.execute(
                """
                update magic_links
                set used_at = ?, user_agent_used = ?
                where id = ?
                """,
                (now.isoformat(), user_agent, str(row["id"])),
            )
            connection.execute(
                """
                insert into web_sessions(
                    id,
                    telegram_user_id,
                    session_hash,
                    created_at,
                    expires_at,
                    last_seen_at,
                    user_agent,
                    ip_address
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    user_id,
                    token_hash(session_token),
                    now.isoformat(),
                    session.expires_at.isoformat(),
                    now.isoformat(),
                    user_agent,
                    ip_address,
                ),
            )
        return session

    def get_session(self, token: str, *, now: datetime | None = None, touch: bool = True) -> WebSessionProfile | None:
        if not token:
            return None
        now = now or datetime.now()
        with connect_database(self.settings) as connection:
            row = connection.execute(
                """
                select id, telegram_user_id, expires_at, revoked_at
                from web_sessions
                where session_hash = ?
                limit 1
                """,
                (token_hash(token),),
            ).fetchone()
            if row is None:
                return None
            expires_at = datetime.fromisoformat(str(row["expires_at"]))
            revoked_at = datetime.fromisoformat(str(row["revoked_at"])) if row["revoked_at"] else None
            if revoked_at is not None or expires_at <= now:
                return None
            if touch:
                connection.execute(
                    """
                    update web_sessions
                    set last_seen_at = ?
                    where id = ?
                    """,
                    (now.isoformat(), str(row["id"])),
                )
        return WebSessionProfile(
            id=str(row["id"]),
            telegram_user_id=int(row["telegram_user_id"]),
            expires_at=expires_at,
            revoked_at=revoked_at,
        )

    def revoke_session(self, token: str, *, now: datetime | None = None) -> bool:
        if not token:
            return False
        now = now or datetime.now()
        with connect_database(self.settings) as connection:
            cursor = connection.execute(
                """
                update web_sessions
                set revoked_at = ?
                where session_hash = ? and revoked_at is null
                """,
                (now.isoformat(), token_hash(token)),
            )
        return bool(cursor.rowcount)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
