import asyncio
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException

logger = logging.getLogger("wolfhost.backup")

router = APIRouter(prefix="/api/backup", tags=["Backup"])


@router.post("/run")
async def trigger_backup():
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
            return {"status": "success", "output": result.stdout.strip()}
        else:
            logger.error(f"Backup failed: {result.stderr}")
            return {"status": "error", "error": result.stderr.strip()}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Backup timed out after 120s")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
