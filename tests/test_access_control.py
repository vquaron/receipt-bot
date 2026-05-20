import json
from pathlib import Path

from app.config import Settings
from app.security.access_control import AccessControl


class FakeUser:
    id = 333
    full_name = "Test User"
    username = "tester"


def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="token",
        openai_api_key="key",
        obsidian_vault=tmp_path,
        data_dir=tmp_path / "data",
        admin_telegram_user_ids=frozenset({111}),
        allowed_telegram_user_ids=frozenset({222}),
    )


def test_env_users_are_allowed(tmp_path: Path) -> None:
    access = AccessControl(settings(tmp_path))
    assert access.is_allowed(111)
    assert access.is_allowed(222)
    assert not access.is_allowed(333)


def test_pending_request_is_not_duplicated(tmp_path: Path) -> None:
    access = AccessControl(settings(tmp_path))
    _, created = access.create_request(FakeUser())
    assert created
    _, created = access.create_request(FakeUser())
    assert not created
    assert access.is_pending(333)


def test_approve_and_revoke(tmp_path: Path) -> None:
    access = AccessControl(settings(tmp_path))
    access.create_request(FakeUser())
    access.approve(333)
    assert access.is_allowed(333)
    assert access.revoke(333)
    assert not access.is_allowed(333)


def test_legacy_migration_skips_invalid_user_ids(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    app_settings.data_dir.mkdir(parents=True, exist_ok=True)
    (app_settings.data_dir / "access.json").write_text(
        json.dumps(
            {
                "allowed": {"not-a-number": {}, "333": {}},
                "pending": {"bad": {}, "444": {}},
                "rejected": {"oops": {}, "555": {}},
            }
        ),
        encoding="utf-8",
    )

    access = AccessControl(app_settings)

    assert access.is_allowed(333)
    assert access.is_pending(444)
    assert access.repository.rejected_request(555) is not None
