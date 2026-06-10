import asyncio
import logging
import os
import re
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import Response, JSONResponse
from fastapi.staticfiles import StaticFiles
import aiohttp
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from pythonjsonlogger import json as jsonlogger

from config import settings, BASE_DIR
from services.limiter import limiter
from models.database import init_db, async_session
from routes import auth_router, bots_router, logs_router, frontend_router, webhook_router, backup_router, ai_router, tg_proxy_router, public_router
from services.self_healer import SelfHealer
from services.container_manager import ContainerManager

MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB

# ── Structured JSON Logging ───────────────────────────────────────
_log_handler = logging.StreamHandler()
_log_handler.setFormatter(jsonlogger.JsonFormatter(
    fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
    rename_fields={"levelname": "level", "asctime": "ts"},
))
logging.root.handlers.clear()
logging.root.addHandler(_log_handler)
logging.root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

logger = logging.getLogger("wolfhost")

WOLF_BANNER = r"""
╔══════════════════════════════════════════════════╗
║        🐺 Wolf Host — استضافة الذب هوست           ║
║     Autonomous Bot Hosting for Arab Developers   ║
║     Developer: الذئب الأبيض 🐺                    ║
║     Developer: @j49_c                            ║
║     Channel:   https://t.me/EQJ_1                 ║
║     Support:   @Wolfhost_1                        ║
║     X:         https://x.com/wolfhost_1          ║
╚══════════════════════════════════════════════════╝
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(WOLF_BANNER)
    logger.info("🐺 Wolf Host starting up...")

    await init_db()
    logger.info("✅ Database initialised (PostgreSQL external)")

    admin_user = os.environ.get("CREATE_ADMIN_USERNAME", "").strip()
    admin_pass = os.environ.get("CREATE_ADMIN_PASSWORD", "").strip()
    if admin_user and admin_pass:
        from services.auth_service import get_password_hash
        from models.user import User
        from sqlalchemy import select
        async with async_session() as session:
            existing = await session.execute(select(User).where(User.username == admin_user))
            if not existing.scalar_one_or_none():
                user = User(
                    username=admin_user,
                    hashed_password=get_password_hash(admin_pass),
                    is_admin=True,
                    device_fingerprint=None,
                )
                session.add(user)
                await session.commit()
                logger.info(f"✅ Admin account '{admin_user}' created via env vars")
            else:
                logger.info(f"ℹ️ Admin '{admin_user}' already exists")
            await session.close()

    await SelfHealer.recover_running_bots()
    await ContainerManager.cleanup_stale()
    SelfHealer.start()
    logger.info("✅ Self-healer activated (30s polling)")

    monitor_task = asyncio.create_task(ContainerManager.monitor_loop())
    logger.info("✅ Resource monitor activated (3s polling)")

    backup_task = None
    backup_script = Path("/app/scripts/db_backup.sh")
    if backup_script.exists():
        async def scheduled_backup():
            while True:
                await asyncio.sleep(settings.backup_interval_hours * 3600)
                logger.info("🐺 Running scheduled database backup...")
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None,
                        lambda: subprocess.run(
                            ["bash", str(backup_script)],
                            capture_output=True, text=True, timeout=180,
                        ),
                    )
                    logger.info("✅ Scheduled backup completed")
                except Exception as e:
                    logger.error(f"❌ Scheduled backup failed: {e}")

        backup_task = asyncio.create_task(scheduled_backup())
        logger.info(f"✅ Auto-backup scheduled every {settings.backup_interval_hours}h to GitHub")

    yield

    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass

    if backup_task:
        backup_task.cancel()
        try:
            await backup_task
        except asyncio.CancelledError:
            pass

    SelfHealer.stop()

    # Gracefully stop all running bot processes
    running_bots = list(ContainerManager._instances.keys())
    if running_bots:
        logger.info(f"Stopping {len(running_bots)} running bot(s) on shutdown...")
        for cid in running_bots:
            try:
                await ContainerManager.stop_bot(cid)
            except Exception as e:
                logger.warning(f"Error stopping bot {cid} during shutdown: {e}")

    logger.info("Wolf Host shut down gracefully")


app = FastAPI(
    title=settings.app_name,
    description=f"{settings.app_name} — {settings.developer}",
    version="3.0.0",
    lifespan=lifespan,
    contact={
        "name": settings.developer,
        "url": settings.x_account,
    },
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[
        "wolf-host.pages.dev",
        "localhost",
        "127.0.0.1",
        "*.hf.space",
        ".hf.space",
    ],
)

_space_id = os.environ.get("SPACE_ID", "")
_space_url = f"https://{_space_id}.hf.space" if _space_id else ""
cors_origins = [
    "https://wolf-host.pages.dev",
    "http://localhost:7860",
    "http://127.0.0.1:7860",
]
if _space_url:
    cors_origins.append(_space_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Bot-Slug", "X-Webhook-Token"],
)


@app.api_route("/api/__proxy/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
@limiter.limit("30/minute")
async def bot_proxy(request: Request, path: str):
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"ok": False, "error_code": 403, "description": "Forbidden"}, status_code=403)
    cf_proxy = os.environ.get("CF_PROXY", "")
    if not cf_proxy:
        return JSONResponse({"ok": False, "error_code": 502, "description": "Proxy not configured"}, status_code=502)
    clean_path = path.replace("..", "").replace("@", "").replace("//", "/")
    # SSRF hardening: only allow safe URL path characters
    if not re.match(r'^[a-zA-Z0-9/_\-.?&=%]*$', clean_path):
        return JSONResponse({"ok": False, "error_code": 400, "description": "Invalid path characters"}, status_code=400)
    target = f"{cf_proxy}/{clean_path}"
    if request.url.query:
        target += f"?{request.url.query}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host")}
    body = await request.body()
    timeout = aiohttp.ClientTimeout(total=120, connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.request(method=request.method, url=target, headers=headers, data=body or None) as resp:
                content = await resp.read()
                return Response(content=content, status_code=resp.status, media_type=resp.content_type or "application/json")
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning(f"bot_proxy error for {path}: {e}")
        return JSONResponse({"ok": False, "error_code": 502, "description": "Upstream unreachable"}, status_code=502)


@app.middleware("http")
async def body_size_limit(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and int(cl) > MAX_BODY_SIZE:
        return JSONResponse({"detail": "Request body too large"}, status_code=413)
    if request.method in ("POST", "PUT", "PATCH"):
        body = await request.body()
        if len(body) > MAX_BODY_SIZE:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
    return await call_next(request)


@app.get("/api/health")
async def health_check():
    """Lightweight health check — verifies DB connectivity."""
    from sqlalchemy import text as sa_text
    try:
        async with async_session() as session:
            await session.execute(sa_text("SELECT 1"))
        return {"status": "healthy", "db": "ok"}
    except Exception as e:
        logger.error(f"Health check DB failure: {e}")
        return JSONResponse({"status": "unhealthy", "db": "error"}, status_code=503)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(),midi=(),sync-xhr=(),microphone=(),camera=(),magnetometer=(),gyroscope=(),fullscreen=(self)"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self' https://wolf-host.pages.dev https://*.hf.space; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(frontend_router)
app.include_router(auth_router)
app.include_router(bots_router)
app.include_router(logs_router)
app.include_router(webhook_router)
app.include_router(backup_router)
app.include_router(ai_router)
app.include_router(tg_proxy_router)
app.include_router(public_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=7860,
        reload=False,
        log_level=settings.log_level,
        proxy_headers=True,
    )
