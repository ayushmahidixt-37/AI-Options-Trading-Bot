"""Local password-protected web UI for paper-mode operations."""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from .actions import close_paper_position_action, run_health_action, run_paper_scan_action, status_snapshot
from .connections import ConnectionActionError, ConnectionManager
from .runner import build_application
from .config import Settings
from .health import healthcheck

security = HTTPBasic()
TEMPLATE_DIR = Path(__file__).with_name("templates")


def create_web_app(
    settings: Settings,
    password: str,
    connection_manager: ConnectionManager | None = None,
) -> FastAPI:
    if not password:
        raise ValueError("A web UI password is required")
    application = build_application(settings)
    connections = connection_manager or ConnectionManager(settings)
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    app = FastAPI(title="AI Options Trading Bot", docs_url=None, redoc_url=None)

    def require_login(credentials: HTTPBasicCredentials = Depends(security)) -> str:
        valid_user = secrets.compare_digest(credentials.username, "admin")
        valid_password = secrets.compare_digest(credentials.password, password)
        if not (valid_user and valid_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid UI credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
        return credentials.username

    def dashboard_context(request: Request, message: str | None = None, ok: bool | None = None) -> dict[str, object]:
        snapshot = status_snapshot(application)
        report = healthcheck(settings, application.ledger)
        return {
            "request": request,
            "settings": settings,
            "account": snapshot["account"],
            "positions": snapshot["positions"],
            "health": report,
            "connections": connections.snapshot(),
            "message": message,
            "ok": ok,
        }

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, _user: str = Depends(require_login)) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="dashboard.html", context=dashboard_context(request))

    @app.post("/actions/healthcheck", response_class=HTMLResponse)
    def run_health(request: Request, _user: str = Depends(require_login)) -> HTMLResponse:
        result = run_health_action(application)
        return templates.TemplateResponse(request=request, name="dashboard.html", context=dashboard_context(request, result.message, result.ok))

    @app.post("/actions/paper-scan", response_class=HTMLResponse)
    def run_paper_scan(request: Request, _user: str = Depends(require_login)) -> HTMLResponse:
        result = run_paper_scan_action(application)
        return templates.TemplateResponse(request=request, name="dashboard.html", context=dashboard_context(request, result.message, result.ok))

    @app.post("/actions/angel-connect", response_class=HTMLResponse)
    def connect_angel(request: Request, _user: str = Depends(require_login)) -> HTMLResponse:
        try:
            result = connections.connect_angel()
            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context=dashboard_context(request, result.last_message, True),
            )
        except (ConnectionActionError, FileNotFoundError, ValueError) as exc:
            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context=dashboard_context(request, str(exc), False),
            )

    @app.post("/actions/nifty-refresh", response_class=HTMLResponse)
    def refresh_nifty(request: Request, _user: str = Depends(require_login)) -> HTMLResponse:
        try:
            result = connections.refresh_nifty()
            try:
                connections.refresh_intelligence_if_due()
            except ConnectionActionError:
                pass
            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context=dashboard_context(request, result.last_message, True),
            )
        except ConnectionActionError as exc:
            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context=dashboard_context(request, str(exc), False),
            )

    @app.post("/actions/intelligence-refresh", response_class=HTMLResponse)
    def refresh_intelligence(request: Request, _user: str = Depends(require_login)) -> HTMLResponse:
        try:
            result = connections.refresh_intelligence()
            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context=dashboard_context(request, result.last_message, True),
            )
        except ConnectionActionError as exc:
            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context=dashboard_context(request, str(exc), False),
            )

    @app.post("/actions/telegram-test", response_class=HTMLResponse)
    def test_telegram(request: Request, _user: str = Depends(require_login)) -> HTMLResponse:
        try:
            result = connections.test_telegram()
            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context=dashboard_context(request, result.last_message, True),
            )
        except (ConnectionActionError, FileNotFoundError, ValueError) as exc:
            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context=dashboard_context(request, str(exc), False),
            )

    @app.post("/positions/{order_id}/close")
    def close_position(
        request: Request,
        order_id: int,
        _user: str = Depends(require_login),
        exit_price: float = Form(),
        reason: str = Form("ui-close"),
    ) -> HTMLResponse:
        result = close_paper_position_action(
            application,
            order_id=order_id,
            exit_price=exit_price,
            observed_at=datetime.now(settings.timezone),
            reason=reason,
        )
        return templates.TemplateResponse(request=request, name="dashboard.html", context=dashboard_context(request, result.message, result.ok))

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        report = healthcheck(settings, application.ledger)
        return {"ok": report.ok, "checks": report.checks}

    @app.get("/logout")
    def logout() -> RedirectResponse:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    return app
