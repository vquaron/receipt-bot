from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import initialize_database
from app.db.connection import connect_database
from app.users.access_service import AccessControl
from app.web.app import create_app
from app.web.auth import WebAuthRepository


def test_api_requires_authentication(tmp_path: Path) -> None:
    client = TestClient(create_app(_settings(tmp_path)))

    response = client.get("/api/me")

    assert response.status_code == 401


def test_magic_login_sets_cookie_and_api_lists_db_documents_only(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    _insert_document(app_settings, user_id=222, document_id="doc-1", file_stem="receipt-one")
    _write_legacy_manifest(app_settings, user_id=222)
    auth = WebAuthRepository(app_settings)
    link = auth.create_magic_link(222)
    client = TestClient(create_app(app_settings), base_url="https://receipts.example")

    login_response = client.get(f"/auth/magic?token={link.token}", follow_redirects=False)
    receipts_response = client.get("/api/receipts")
    me_response = client.get("/api/me")

    assert login_response.status_code == 302
    assert app_settings.web_session_cookie_name in login_response.headers["set-cookie"]
    assert login_response.headers["referrer-policy"] == "no-referrer"
    assert me_response.json()["telegram_user_id"] == 222
    receipts = receipts_response.json()["receipts"]
    assert [receipt["id"] for receipt in receipts] == ["doc-1"]
    assert receipts[0]["receipt_id"] == "receipt-one"


def test_magic_login_redirects_to_safe_next_path(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    auth = WebAuthRepository(app_settings)
    link = auth.create_magic_link(222)
    client = TestClient(create_app(app_settings), base_url="https://receipts.example")

    response = client.get(f"/auth/magic?token={link.token}&next=/receipts/doc-1", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/receipts/doc-1"


def test_magic_login_rejects_unsafe_next_path(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    auth = WebAuthRepository(app_settings)
    link = auth.create_magic_link(222)
    protocol_relative_link = auth.create_magic_link(222)
    client = TestClient(create_app(app_settings), base_url="https://receipts.example")

    response = client.get(
        f"/auth/magic?token={link.token}&next=https://evil.example/receipts/doc-1",
        follow_redirects=False,
    )
    protocol_relative_response = client.get(
        f"/auth/magic?token={protocol_relative_link.token}&next=//evil.example/receipts/doc-1",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/"
    assert protocol_relative_response.status_code == 302
    assert protocol_relative_response.headers["location"] == "/"


def test_receipt_route_requires_auth_and_serves_shell(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    anonymous = TestClient(create_app(app_settings))
    authenticated = _authenticated_client(app_settings, user_id=222)

    anonymous_response = anonymous.get("/receipts/doc-1", follow_redirects=False)
    authenticated_response = authenticated.get("/receipts/doc-1")

    assert anonymous_response.status_code == 302
    assert anonymous_response.headers["location"] == "/login"
    assert authenticated_response.status_code == 200
    assert "receipt-list" in authenticated_response.text


def test_api_detail_returns_items_and_owner_scopes_documents(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    _insert_document(app_settings, user_id=222, document_id="doc-1", file_stem="receipt-one")
    _insert_document(app_settings, user_id=333, document_id="doc-2", file_stem="receipt-two")
    client = _authenticated_client(app_settings, user_id=222)

    detail_response = client.get("/api/receipts/receipt-one")
    other_user_response = client.get("/api/receipts/doc-2")

    assert detail_response.status_code == 200
    payload = detail_response.json()
    assert payload["receipt"]["id"] == "doc-1"
    assert payload["parsed"]["merchant"] == "Store"
    assert payload["items"][0]["name_ru"] == "Пакет"
    assert other_user_response.status_code == 404


def test_api_hides_deleted_documents(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    _insert_document(app_settings, user_id=222, document_id="doc-1", file_stem="receipt-one", status="deleted")
    client = _authenticated_client(app_settings, user_id=222)

    response = client.get("/api/receipts")

    assert response.json()["receipts"] == []


def test_image_endpoint_prefers_stored_image_and_falls_back_to_original(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    _insert_document(app_settings, user_id=222, document_id="doc-1", file_stem="receipt-one")
    client = _authenticated_client(app_settings, user_id=222)

    stored_response = client.get("/api/receipts/doc-1/image")
    (app_settings.app_storage_dir / "documents/doc-1/stored.jpg").unlink()
    with connect_database(app_settings) as connection:
        connection.execute("delete from document_files where document_id = ? and kind = ?", ("doc-1", "stored_image"))
    original_response = client.get("/api/receipts/doc-1/image")

    assert stored_response.status_code == 200
    assert stored_response.content == b"stored"
    assert stored_response.headers["cache-control"] == "private, max-age=300"
    assert original_response.status_code == 200
    assert original_response.content == b"original"


def test_logout_revokes_session(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    client = _authenticated_client(app_settings, user_id=222)

    logout_response = client.post("/logout")
    me_response = client.get("/api/me")

    assert logout_response.status_code == 200
    assert me_response.status_code == 401


def test_revoked_user_session_is_rejected(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    client = _authenticated_client(app_settings, user_id=222)
    with connect_database(app_settings) as connection:
        connection.execute(
            """
            update users
            set status = 'revoked', updated_at = ?
            where telegram_user_id = 222
            """,
            (datetime.now().isoformat(),),
        )

    response = client.get("/api/me")
    shell_response = client.get("/", follow_redirects=False)

    assert response.status_code == 401
    assert shell_response.status_code == 302
    assert shell_response.headers["location"] == "/login"


def test_magic_login_rejects_revoked_user(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    AccessControl(app_settings)
    auth = WebAuthRepository(app_settings)
    link = auth.create_magic_link(222)
    with connect_database(app_settings) as connection:
        connection.execute(
            """
            update users
            set status = 'revoked', updated_at = ?
            where telegram_user_id = 222
            """,
            (datetime.now().isoformat(),),
        )
    client = TestClient(create_app(app_settings), base_url="https://receipts.example")

    response = client.get(f"/auth/magic?token={link.token}")
    me_response = client.get("/api/me")

    assert response.status_code == 401
    assert app_settings.web_session_cookie_name not in response.cookies
    assert me_response.status_code == 401


def _authenticated_client(app_settings: Settings, *, user_id: int) -> TestClient:
    auth = WebAuthRepository(app_settings)
    link = auth.create_magic_link(user_id)
    session = auth.redeem_magic_token(link.token)
    client = TestClient(create_app(app_settings))
    client.cookies.set(app_settings.web_session_cookie_name, session.token)
    return client


def _insert_document(
    app_settings: Settings,
    *,
    user_id: int,
    document_id: str,
    file_stem: str,
    status: str = "confirmed",
) -> None:
    initialize_database(app_settings)
    document_root = app_settings.app_storage_dir / "documents" / document_id
    document_root.mkdir(parents=True, exist_ok=True)
    (document_root / "original.jpg").write_bytes(b"original")
    (document_root / "stored.jpg").write_bytes(b"stored")
    parsed = {
        "date": "2026-05-20",
        "time": "12:00",
        "merchant": "Store",
        "amount": "100",
        "currency": "AMD",
        "category": "Grocery",
        "summary_ru": "Покупка",
        "items": [
            {
                "name_original": "bag",
                "name_ru": "Пакет",
                "name_en": "Bag",
                "unit_price": "100",
                "quantity": "1",
                "unit": "шт",
                "line_total": "100",
            }
        ],
        "possible_errors": [],
    }
    now = datetime.now().isoformat()
    with connect_database(app_settings) as connection:
        connection.execute(
            """
            insert into documents(
                id,
                owner_telegram_user_id,
                document_type,
                status,
                date,
                time,
                merchant,
                amount,
                currency,
                category,
                summary_ru,
                parsed_json,
                review_payload_json,
                possible_errors_json,
                file_stem,
                created_at,
                updated_at
            )
            values (?, ?, 'receipt', ?, '2026-05-20', '12:00', 'Store', '100', 'AMD', 'Grocery', 'Покупка', ?, ?, '[]', ?, ?, ?)
            """,
            (document_id, user_id, status, json.dumps(parsed, ensure_ascii=False), json.dumps(parsed, ensure_ascii=False), file_stem, now, now),
        )
        connection.execute(
            """
            insert into document_items(
                document_id,
                position,
                name_original,
                name_ru,
                name_en,
                unit_price,
                quantity,
                unit,
                line_total,
                created_at
            )
            values (?, 1, 'bag', 'Пакет', 'Bag', '100', '1', 'шт', '100', ?)
            """,
            (document_id, now),
        )
        for kind, name, size in (("original_image", "original.jpg", 8), ("stored_image", "stored.jpg", 6)):
            connection.execute(
                """
                insert into document_files(
                    document_id,
                    kind,
                    path,
                    storage_backend,
                    storage_key,
                    is_canonical,
                    mime_type,
                    size_bytes,
                    sha256,
                    created_at
                )
                values (?, ?, ?, 'local', ?, 1, 'image/jpeg', ?, 'sha', ?)
                """,
                (document_id, kind, f"documents/{document_id}/{name}", f"documents/{document_id}/{name}", size, now),
            )


def _write_legacy_manifest(app_settings: Settings, *, user_id: int) -> None:
    manifest = app_settings.obsidian_vault / f"Users/{user_id}/MANIFEST/receipts/2026/05/legacy.manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "receipt_id": "legacy",
                "owner_user_id": user_id,
                "date": "2026-05-20",
                "merchant": "Legacy",
                "amount": "1",
                "currency": "AMD",
                "note": f"Users/{user_id}/Receipts/2026/05/legacy.md",
                "files": [],
            }
        ),
        encoding="utf-8",
    )


def _settings(tmp_path: Path, **overrides) -> Settings:
    data_dir = tmp_path / "data"
    values = {
        "telegram_bot_token": "token",
        "openai_api_key": "key",
        "obsidian_vault": tmp_path / "vault",
        "data_dir": data_dir,
        "admin_telegram_user_ids": frozenset(),
        "allowed_telegram_user_ids": frozenset({222, 333}),
        "database_url": f"sqlite:///{(data_dir / 'app.db').as_posix()}",
        "app_storage_dir": data_dir / "storage",
        "tmp_storage_dir": data_dir / "tmp",
        "export_storage_dir": data_dir / "exports",
        "debug_storage_dir": data_dir / "debug",
        "web_base_url": "https://receipts.example",
    }
    values.update(overrides)
    values["obsidian_vault"].mkdir(parents=True, exist_ok=True)
    return Settings(**values)
