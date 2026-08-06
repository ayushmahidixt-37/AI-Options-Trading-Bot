"""Local password-protected web UI for paper-mode operations."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from .actions import close_paper_position_action, run_health_action, run_paper_scan_action, status_snapshot
from .backtest import BacktestResult, export_backtest_csv, run_momentum_backtest
from .connections import ConnectionActionError, ConnectionManager, PaperTradeProposal
from .domain import PaperOrderRequest
from .runner import build_application
from .config import Settings
from .health import healthcheck
from .paper_monitor import PaperPositionMonitor
from .risk import RiskRejected

security = HTTPBasic()
TEMPLATE_DIR = Path(__file__).with_name("templates")


def create_web_app(
    settings: Settings,
    password: str,
    connection_manager: ConnectionManager | None = None,
    start_background_monitor: bool = True,
) -> FastAPI:
    if not password:
        raise ValueError("A web UI password is required")
    application = build_application(settings)
    connections = connection_manager or ConnectionManager(settings)
    paper_monitor = PaperPositionMonitor(application, connections)
    connections.register_paper_cycle(paper_monitor.run_cycle)
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    latest_backtest: BacktestResult | None = None
    latest_proposal: PaperTradeProposal | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_background_monitor:
            connections.start_background_monitor()
        try:
            yield
        finally:
            if start_background_monitor:
                connections.stop_background_monitor()

    app = FastAPI(
        title="AI Options Trading Bot",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

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
        now = datetime.now(settings.timezone)
        return {
            "request": request,
            "settings": settings,
            "account": snapshot["account"],
            "positions": snapshot["positions"],
            "health": report,
            "connections": connections.snapshot(),
            "backtest": latest_backtest,
            "proposal": latest_proposal,
            "paper_monitor": paper_monitor.snapshot(),
            "daily_summary": application.ledger.daily_summary(
                application.clock.trading_date(now)
            ),
            "recent_events": application.ledger.recent_events(),
            "performance_windows": (
                ("Today", application.ledger.performance_summary(now.date().isoformat())),
                (
                    "Last 7 days",
                    application.ledger.performance_summary(
                        (now.date() - timedelta(days=6)).isoformat()
                    ),
                ),
                (
                    "Last 30 days",
                    application.ledger.performance_summary(
                        (now.date() - timedelta(days=29)).isoformat()
                    ),
                ),
                ("All time", application.ledger.performance_summary()),
            ),
            "trade_journal": application.ledger.trade_journal(25),
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

    @app.post("/actions/instruments-refresh", response_class=HTMLResponse)
    def refresh_instruments(
        request: Request, _user: str = Depends(require_login)
    ) -> HTMLResponse:
        try:
            connections.refresh_instrument_archive()
            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context=dashboard_context(request, "NIFTY instruments archived", True),
            )
        except ConnectionActionError as exc:
            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context=dashboard_context(request, str(exc), False),
            )

    @app.get("/archive/candles.csv", response_class=FileResponse)
    def download_archive(_user: str = Depends(require_login)) -> FileResponse:
        target = settings.data_dir / "exports" / "nifty-five-minute-candles.csv"
        connections.archive.export_candles_csv(target)
        return FileResponse(target, filename=target.name, media_type="text/csv")

    @app.post("/actions/archive-backup", response_class=FileResponse)
    def backup_archive(_user: str = Depends(require_login)) -> FileResponse:
        stamp = datetime.now(settings.timezone).strftime("%Y%m%d-%H%M%S")
        target = settings.data_dir / "backups" / f"market-data-{stamp}.sqlite3"
        connections.archive.backup(target)
        return FileResponse(
            target,
            filename=target.name,
            media_type="application/vnd.sqlite3",
        )

    @app.post("/actions/backtest", response_class=HTMLResponse)
    def run_backtest(
        request: Request,
        start_date: str = Form(""),
        end_date: str = Form(""),
        _user: str = Depends(require_login),
    ) -> HTMLResponse:
        nonlocal latest_backtest
        try:
            start = date.fromisoformat(start_date) if start_date else None
            end = date.fromisoformat(end_date) if end_date else None
            if start and end and start > end:
                raise ValueError("Start date must not be after end date")
        except ValueError as exc:
            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context=dashboard_context(request, str(exc), False),
            )
        latest_backtest = run_momentum_backtest(
            connections.archive, start=start, end=end, settings=settings
        )
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=dashboard_context(
                request,
                "Backtest completed" if latest_backtest.trades else latest_backtest.reason,
                latest_backtest.trades > 0,
            ),
        )

    @app.get("/backtest/trades.csv", response_class=FileResponse)
    def download_backtest(_user: str = Depends(require_login)) -> FileResponse:
        if latest_backtest is None:
            raise HTTPException(status_code=404, detail="Run a backtest first")
        target = settings.data_dir / "exports" / "backtest-trades.csv"
        export_backtest_csv(latest_backtest, target)
        return FileResponse(target, filename=target.name, media_type="text/csv")

    @app.post("/actions/archive-verify", response_class=HTMLResponse)
    def verify_archive(request: Request, _user: str = Depends(require_login)) -> HTMLResponse:
        snapshot = connections.refresh_archive_health()
        ok = snapshot.archive_integrity == "ok"
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=dashboard_context(
                request, f"Archive integrity: {snapshot.archive_integrity}", ok
            ),
        )

    @app.post("/actions/paper-proposal", response_class=HTMLResponse)
    def create_proposal(request: Request, _user: str = Depends(require_login)) -> HTMLResponse:
        nonlocal latest_proposal
        try:
            latest_proposal = connections.create_paper_proposal()
            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context=dashboard_context(
                    request, "Paper proposal created; no order was placed", True
                ),
            )
        except ConnectionActionError as exc:
            latest_proposal = None
            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context=dashboard_context(request, str(exc), False),
            )

    @app.post("/actions/paper-confirm", response_class=HTMLResponse)
    def confirm_proposal(
        request: Request,
        proposal_id: str = Form(),
        _user: str = Depends(require_login),
    ) -> HTMLResponse:
        nonlocal latest_proposal
        if latest_proposal is None or not secrets.compare_digest(
            latest_proposal.proposal_id, proposal_id
        ):
            result_message, result_ok = "Paper proposal is missing or expired", False
        else:
            proposal_to_confirm = latest_proposal
            latest_proposal = None
            try:
                fresh = connections.create_paper_proposal()
                if (
                    fresh.instrument.token != proposal_to_confirm.instrument.token
                    or fresh.direction != proposal_to_confirm.direction
                ):
                    raise ConnectionActionError("Signal or ATM contract changed; review a new proposal")
                order_id = application.paper_broker.buy(
                    PaperOrderRequest(
                        instrument=fresh.instrument,
                        lots=1,
                        quote=fresh.quote,
                        stop_price=fresh.stop_price,
                        strategy="momentum-v1",
                        reason=f"UI-confirmed {fresh.direction} proposal",
                    ),
                    fresh.quote.observed_at,
                )
                paper_monitor.record_proposal_context(order_id, fresh)
                result_message, result_ok = f"Opened paper position {order_id}", True
                application.ledger.record_event(
                    fresh.quote.observed_at.isoformat(),
                    "INFO",
                    "paper_entry",
                    f"{fresh.instrument.symbol} one lot",
                )
                try:
                    connections.send_alert(
                        f"Paper entry confirmed: {fresh.instrument.symbol}, quote {fresh.quote.price:.2f}, stop {fresh.stop_price:.2f}"
                    )
                except Exception:
                    pass
            except (ConnectionActionError, RiskRejected, ValueError) as exc:
                result_message, result_ok = str(exc), False
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=dashboard_context(request, result_message, result_ok),
        )

    @app.post("/actions/paper-close-all", response_class=HTMLResponse)
    def close_all_paper(
        request: Request,
        confirmation: str = Form(),
        _user: str = Depends(require_login),
    ) -> HTMLResponse:
        try:
            result = paper_monitor.close_all(confirmation)
            message = f"Paper kill switch complete; closed {result.positions_closed} position(s)"
            ok = True
        except (ConnectionActionError, RuntimeError, ValueError) as exc:
            message, ok = str(exc), False
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=dashboard_context(request, message, ok),
        )

    @app.post("/actions/auto-paper", response_class=HTMLResponse)
    def configure_auto_paper(
        request: Request,
        enabled: bool = Form(),
        confirmation: str = Form(),
        _user: str = Depends(require_login),
    ) -> HTMLResponse:
        try:
            snapshot = paper_monitor.set_auto_entry(enabled, confirmation)
            message, ok = snapshot.auto_entry_last_action, True
        except ValueError as exc:
            message, ok = str(exc), False
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=dashboard_context(request, message, ok),
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
