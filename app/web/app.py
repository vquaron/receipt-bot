from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.config import Settings, load_settings
from app.receipts.document_types import document_type_label
from app.receipts.models import ReceiptFileRecord
from app.repositories.documents import (
    FILE_KIND_ORIGINAL_IMAGE,
    FILE_KIND_STORED_IMAGE,
    DocumentRepository,
    DocumentStorageError,
)
from app.storage.retention import cleanup_runtime_storage
from app.users.access_service import AccessControl
from app.users.models import UserProfile
from app.web.auth import WebAuthError, WebAuthRepository, WebSession, WebSessionProfile


STATIC_DIR = Path(__file__).with_name("static")


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or load_settings()
    auth_repository = WebAuthRepository(app_settings)
    document_repository = DocumentRepository(app_settings)
    access_control = AccessControl(app_settings)
    cleanup_runtime_storage(app_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        shutil.rmtree(app_settings.tmp_storage_dir / "materialized" / "web", ignore_errors=True)

    app = FastAPI(title="Receipt Bot Web MVP", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.auth = auth_repository
    app.state.documents = document_repository
    app.state.access = access_control

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    def optional_session(request: Request) -> WebSessionProfile | None:
        token = request.cookies.get(app_settings.web_session_cookie_name, "")
        session = auth_repository.get_session(token)
        if session is None or not access_control.is_allowed(session.telegram_user_id):
            return None
        return session

    def require_session(request: Request) -> WebSessionProfile:
        token = request.cookies.get(app_settings.web_session_cookie_name, "")
        session = auth_repository.get_session(token)
        if session is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        if not access_control.is_allowed(session.telegram_user_id):
            raise HTTPException(status_code=401, detail="Access revoked.")
        return session

    @app.get("/", response_class=HTMLResponse)
    def index(session: WebSessionProfile | None = Depends(optional_session)):
        if session is None:
            return RedirectResponse("/login", status_code=302)
        return HTMLResponse(_static_text("index.html"))

    @app.get("/receipts/{receipt_id}", response_class=HTMLResponse)
    def receipt_shell(receipt_id: str, session: WebSessionProfile | None = Depends(optional_session)):
        if session is None:
            return RedirectResponse("/login", status_code=302)
        return HTMLResponse(_static_text("index.html"))

    @app.get("/login", response_class=HTMLResponse)
    def login() -> HTMLResponse:
        return HTMLResponse(_static_text("login.html"))

    @app.get("/app.webmanifest")
    def manifest() -> Response:
        return Response(_static_text("app.webmanifest"), media_type="application/manifest+json")

    @app.get("/sw.js")
    def service_worker() -> Response:
        return Response(_static_text("sw.js"), media_type="application/javascript")

    @app.get("/static/app.js")
    def app_js() -> Response:
        return Response(_static_text("app.js"), media_type="application/javascript")

    @app.get("/static/styles.css")
    def styles() -> Response:
        return Response(_static_text("styles.css"), media_type="text/css")

    @app.get("/auth/magic")
    def auth_magic(token: str, request: Request, next_path: str = Query("/", alias="next")):
        try:
            web_session = auth_repository.redeem_magic_token(
                token,
                user_agent=request.headers.get("user-agent", ""),
                ip_address=request.client.host if request.client else "",
            )
        except WebAuthError:
            return HTMLResponse(_static_text("login_failed.html"), status_code=401)
        if not access_control.is_allowed(web_session.telegram_user_id):
            auth_repository.revoke_session(web_session.token)
            return HTMLResponse(_static_text("login_failed.html"), status_code=401)
        response = RedirectResponse(_safe_next_path(next_path), status_code=302)
        _set_session_cookie(response, app_settings, web_session)
        return response

    @app.post("/logout")
    def logout(
        request: Request,
        response: Response,
    ) -> dict[str, bool]:
        token = request.cookies.get(app_settings.web_session_cookie_name, "")
        auth_repository.revoke_session(token)
        _clear_session_cookie(response, app_settings)
        return {"ok": True}

    @app.get("/api/me")
    def api_me(session: WebSessionProfile = Depends(require_session)) -> dict[str, object]:
        profile = access_control.profile_for(session.telegram_user_id)
        if profile is None and access_control.is_admin(session.telegram_user_id):
            return {
                "telegram_user_id": session.telegram_user_id,
                "role": "admin",
                "status": "allowed",
            }
        if profile is None or not access_control.is_allowed(session.telegram_user_id):
            raise HTTPException(status_code=401, detail="Access revoked.")
        return _profile_json(profile)

    @app.get("/api/receipts")
    def api_receipts(session: WebSessionProfile = Depends(require_session)) -> dict[str, object]:
        records = document_repository.list_user_documents(session.telegram_user_id)
        return {"receipts": [_record_json(record) for record in records]}

    @app.get("/api/receipts/{receipt_id}")
    def api_receipt(receipt_id: str, session: WebSessionProfile = Depends(require_session)) -> dict[str, object]:
        detail = document_repository.get_user_document_detail(session.telegram_user_id, receipt_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Receipt not found.")
        return {
            "receipt": _record_json(detail.record),
            "parsed": detail.parsed,
            "items": [
                {
                    "position": item.position,
                    "name_original": item.name_original,
                    "name_ru": item.name_ru,
                    "name_en": item.name_en,
                    "unit_price": item.unit_price,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "line_total": item.line_total,
                    "possible_error": item.possible_error,
                }
                for item in detail.items
            ],
        }

    @app.get("/api/receipts/{receipt_id}/image")
    def api_receipt_image(receipt_id: str, session: WebSessionProfile = Depends(require_session)):
        detail = document_repository.get_user_document_detail(session.telegram_user_id, receipt_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Receipt not found.")
        image_file = _first_file(detail.record.file_records, FILE_KIND_STORED_IMAGE) or _first_file(
            detail.record.file_records,
            FILE_KIND_ORIGINAL_IMAGE,
        )
        if image_file is None:
            raise HTTPException(status_code=404, detail="Receipt image not found.")
        try:
            path = _materialized_image_path(document_repository, app_settings, detail.record.document_id, image_file)
        except (DocumentStorageError, ValueError, OSError) as exc:
            raise HTTPException(status_code=404, detail="Receipt image is unavailable.") from exc
        return FileResponse(
            path,
            media_type=image_file.mime_type or "image/jpeg",
            headers={"Cache-Control": "private, max-age=300"},
        )
    return app


def _profile_json(profile: UserProfile) -> dict[str, object]:
    return {
        "telegram_user_id": profile.user_id,
        "username": profile.username,
        "full_name": profile.full_name,
        "role": profile.role.value,
        "status": profile.status.value,
    }


def _record_json(record) -> dict[str, object]:
    return {
        "id": record.document_id,
        "receipt_id": record.receipt_id,
        "document_type": record.document_type,
        "document_type_label": document_type_label(record.document_type),
        "date": record.date,
        "merchant": record.merchant,
        "amount": record.amount,
        "currency": record.currency,
        "created_at": record.created_at,
        "has_image": any(file.kind in {FILE_KIND_STORED_IMAGE, FILE_KIND_ORIGINAL_IMAGE} for file in record.file_records),
    }


def _first_file(files: tuple[ReceiptFileRecord, ...], kind: str) -> ReceiptFileRecord | None:
    return next((file for file in files if file.kind == kind), None)


def _materialized_image_path(
    repository: DocumentRepository,
    settings: Settings,
    document_id: str,
    file_record: ReceiptFileRecord,
) -> Path:
    target_dir = settings.tmp_storage_dir / "materialized" / "web" / document_id
    return repository.materialize_file(file_record, target_dir)


def _set_session_cookie(response: Response, settings: Settings, session: WebSession) -> None:
    response.set_cookie(
        settings.web_session_cookie_name,
        session.token,
        max_age=max(1, settings.web_session_ttl_days) * 24 * 60 * 60,
        httponly=True,
        secure=settings.web_base_url.startswith("https://"),
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.web_session_cookie_name,
        httponly=True,
        secure=settings.web_base_url.startswith("https://"),
        samesite="lax",
        path="/",
    )


def _static_text(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def _safe_next_path(value: str) -> str:
    parsed = urlsplit(value or "/")
    if parsed.scheme or parsed.netloc:
        return "/"
    path = parsed.path or "/"
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    query = f"?{parsed.query}" if parsed.query else ""
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return f"{path}{query}{fragment}"
