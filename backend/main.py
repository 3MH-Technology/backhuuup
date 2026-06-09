import asyncio
import logging
import os
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from config import settings, BASE_DIR
from models.database import init_db, async_session
from routes import auth_router, bots_router, logs_router, frontend_router, webhook_router, backup_router, ai_router
from services.limiter import limiter
from services.self_healer import SelfHealer
from services.container_manager import ContainerManager

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] 🐺 %(name)s: %(message)s",
)
logger = logging.getLogger("wolfhost")

WOLF_BANNER = r"""
╔══════════════════════════════════════════════════╗
║        🐺 Wolf Host — استضافة الذب هوست           ║
║     Autonomous Bot Hosting for Arab Developers   ║
║     Developer: الذئب الأبيض 🐺                    ║
║     Telegram:  @j49_c                            ║
║     Channel:   @O5O6J                            ║
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
    logger.info("🐺 Wolf Host shut down gracefully")


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
    allowed_hosts=["*"],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(),midi=(),sync-xhr=(),microphone=(),camera=(),magnetometer=(),gyroscope=(),fullscreen=(self)"
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=7860,
        reload=False,
        log_level=settings.log_level,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
