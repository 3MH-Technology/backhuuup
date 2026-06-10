import asyncio
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import get_session
from models.user import User
from services.auth_service import AuthService
from services.limiter import limiter

logger = logging.getLogger("wolfhost.backup")

router = APIRouter(prefix="/api/backup", tags=["Backup"])


@router.post("/run")
@limiter.limit("1/hour")
async def trigger_backup(
    request: Request,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    is_admin = getattr(user, "is_admin", False)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    script = Path("/app/scripts/db_backup.sh")
    if not script.exists():
        raise HTTPException(status_code=500, detail="Backup script not found")

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["bash", str(script)],
                capture_output=True,
                text=True,
                timeout=120,
            ),
        )
        if result.returncode == 0:
            logger.info("Database backup completed successfully")
            return {"status": "success", "message": "Backup completed successfully"}
        else:
            # Log details server-side only — never expose stderr to the client
            logger.error(f"Backup failed: {result.stderr}")
            return {"status": "error", "error": "Backup failed. Check server logs for details."}
    except subprocess.TimeoutExpired:
        logger.error("Backup timed out after 120s")
        raise HTTPException(status_code=504, detail="Backup timed out")
    except Exception as e:
        logger.error(f"Backup exception: {e}")
        raise HTTPException(status_code=500, detail="Internal backup error")
