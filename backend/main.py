import asyncio
import logging
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import settings, BASE_DIR
from models.database import init_db
from routes import auth_router, bots_router, logs_router, frontend_router, webhook_router, backup_router
from services.self_healer import SelfHealer

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

    await SelfHealer.recover_running_bots()

    SelfHealer.start()
    logger.info("✅ Self-healer activated (30s polling)")

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(frontend_router)
app.include_router(auth_router)
app.include_router(bots_router)
app.include_router(logs_router)
app.include_router(webhook_router)
app.include_router(backup_router)


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
