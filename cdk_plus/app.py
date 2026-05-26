import hmac
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .cloudmail_client import CloudMailClient, load_cloudmail_config
from .config import ADMIN_PASSWORD, CLOUDMAIL_CONFIG_PATH, DB_PATH, REFRESH_SECONDS
from .repository import CdkRepository
from .services import CdkService, CreateBatchCdkOptions, CreateCdkOptions, utc_now


STATIC_DIR = Path(__file__).resolve().parent / "static"


class CreateCdkRequest(BaseModel):
    email: str
    valid_days: int = Field(..., alias="valid_days")
    cdk: str | None = None


class CreateBatchCdkRequest(BaseModel):
    emails: list[str]
    valid_days: int = Field(..., alias="valid_days")


class ResolveCodesRequest(BaseModel):
    cdks: list[str]


class AdminSessionRequest(BaseModel):
    password: str


def create_app(
    db_path: Path = DB_PATH,
    cloudmail_client=None,
    now: Callable = utc_now,
) -> FastAPI:
    app = FastAPI(title="CDK Plus", version="1.0.0")
    service = CdkService(CdkRepository(db_path), cloudmail_client or build_cloudmail_client(), now=now)
    app.state.cdk_service = service
    register_routes(app)
    mount_static(app)
    return app


def build_cloudmail_client() -> CloudMailClient:
    config = load_cloudmail_config(CLOUDMAIL_CONFIG_PATH)
    return CloudMailClient(config)


def register_routes(app: FastAPI) -> None:
    register_admin_routes(app)
    register_public_routes(app)


def register_admin_routes(app: FastAPI) -> None:
    @app.post("/api/admin/session")
    def create_admin_session(request: AdminSessionRequest):
        require_admin_password(request.password)
        return {"ok": True}

    @app.get("/api/admin/cdks")
    def list_cdks(page: int = 1, page_size: int = 20, x_admin_password: str = Header("")):
        require_admin_password(x_admin_password)
        try:
            return app.state.cdk_service.list_cdks(page, page_size)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/admin/cdks", status_code=201)
    def create_cdk(request: CreateCdkRequest, x_admin_password: str = Header("")):
        require_admin_password(x_admin_password)
        try:
            options = CreateCdkOptions(request.email, request.valid_days, request.cdk)
            return app.state.cdk_service.create_cdk(options)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/admin/cdks/batch", status_code=201)
    def create_cdks(request: CreateBatchCdkRequest, x_admin_password: str = Header("")):
        require_admin_password(x_admin_password)
        try:
            options = CreateBatchCdkOptions(request.emails, request.valid_days)
            return {"items": app.state.cdk_service.create_cdks(options)}
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.delete("/api/admin/cdks/{cdk}")
    def delete_cdk(cdk: str, x_admin_password: str = Header("")):
        require_admin_password(x_admin_password)
        try:
            return app.state.cdk_service.delete_cdk(cdk)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/admin/cdks/{cdk}/switch-email")
    def switch_cdk_email(cdk: str, x_admin_password: str = Header("")):
        require_admin_password(x_admin_password)
        try:
            return app.state.cdk_service.switch_email(cdk)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error


def register_public_routes(app: FastAPI) -> None:
    @app.post("/api/codes/resolve")
    def resolve_codes(request: ResolveCodesRequest):
        try:
            return {"items": app.state.cdk_service.resolve_codes(request.cdks)}
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/config")
    def read_config():
        return {"refresh_seconds": REFRESH_SECONDS}


def require_admin_password(password: str) -> None:
    if not hmac.compare_digest(str(password or ""), ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Invalid admin password")


def mount_static(app: FastAPI) -> None:
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/admin")
    def admin():
        return FileResponse(STATIC_DIR / "admin.html")
