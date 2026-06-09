import asyncio
import logging
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    import resource
    HAS_RESOURCE = True
except ImportError:
    HAS_RESOURCE = False

import psutil

from config import settings

logger = logging.getLogger("wolfhost.container")

BOTS_DIR = Path("/app/data/bots")
ALLOWED_EXTENSIONS = {".py", ".php", ".txt"}
MAX_UPLOAD_SIZE = 5 * 1024 * 1024
MAX_ZIP_SIZE = 10 * 1024 * 1024


class ContainerManager:
    _instances: dict[str, dict] = {}
    _port_allocator = iter(range(9000, 10000))

    @staticmethod
    def _allocate_port() -> int:
        return next(ContainerManager._port_allocator)

    @staticmethod
    def get_bot_port(container_id: str) -> int | None:
        inst = ContainerManager._instances.get(container_id)
        return inst["port"] if inst else None

    @staticmethod
    def container_name(user_id: int, slug: str) -> str:
        safe = re.sub(r'[^a-z0-9\-]', '-', slug.lower()).strip('-')[:60]
        return f"wh_{user_id}_{safe}"

    @staticmethod
    def _work_dir(user_id: int, bot_id: int) -> Path:
        d = BOTS_DIR / str(user_id) / str(bot_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _sanitize_path(path: str) -> bool:
        if ".." in path or path.startswith("/"):
            return False
        return True

    @staticmethod
    def _is_safe_filename(name: str) -> bool:
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS and ext != ".zip":
            return False
        if not ContainerManager._sanitize_path(name):
            return False
        return True

    @staticmethod
    def _extract_zip(zip_path: Path, extract_dir: Path) -> list[str]:
        extracted = []
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                fname = info.filename
                if not ContainerManager._is_safe_filename(fname):
                    logger.warning(f"Skipping unsafe file in zip: {fname}")
                    continue
                if info.file_size > MAX_UPLOAD_SIZE:
                    logger.warning(f"Skipping oversized file in zip: {fname} ({info.file_size} bytes)")
                    continue
                dest = extract_dir / fname
                dest.parent.mkdir(parents=True, exist_ok=True)
                zf.extract(info, extract_dir)
                extracted.append(fname)
        return extracted

    @staticmethod
    def _write_main_file(work_dir: Path, bot_type: str, content: str):
        filename = "bot.py" if bot_type == "python" else "index.php"
        (work_dir / filename).write_text(content, encoding="utf-8")

    @staticmethod
    def _write_requirements(work_dir: Path, requirements: str):
        if requirements.strip():
            (work_dir / "requirements.txt").write_text(requirements, encoding="utf-8")

    @classmethod
    async def start_bot(cls, bot_id: int, user_id: int, slug: str,
                        bot_type: str, main_file_content: str = "",
                        requirements: str = "", is_upload: bool = False) -> dict:
        name = cls.container_name(user_id, slug)
        work_dir = cls._work_dir(user_id, bot_id)

        if not is_upload:
            cls._write_main_file(work_dir, bot_type, main_file_content)
            if bot_type == "python":
                cls._write_requirements(work_dir, requirements)

        log_file = work_dir / "bot.log"
        log_fh = open(log_file, "w", encoding="utf-8")

        port = cls._allocate_port()

        if bot_type == "python":
            req_file = work_dir / "requirements.txt"
            if req_file.exists() and req_file.stat().st_size > 0:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        [sys.executable, "-m", "pip", "install", "-q",
                         "-r", str(req_file)],
                        cwd=str(work_dir),
                        capture_output=True, timeout=120,
                    ),
                )
            entry = work_dir / "bot.py"
            if not entry.exists():
                py_files = list(work_dir.glob("*.py"))
                entry = py_files[0] if py_files else entry
            cmd = [sys.executable, "-u", str(entry)]
        else:
            entry = work_dir / "index.php"
            if not entry.exists():
                php_files = list(work_dir.glob("*.php"))
                entry = php_files[0] if php_files else entry
            cmd = ["php", "-S", f"0.0.0.0:{port}", "-t", str(work_dir), str(entry)]

        safe_env = {k: v for k, v in os.environ.items()
                     if not k.startswith(("SECRET_", "JWT_", "HF_", "HUGGING_", "SMTP_", "DB_"))}
        env = {
            **safe_env,
            "PYTHONUNBUFFERED": "1",
            "HOME": str(work_dir),
            "TMPDIR": str(work_dir),
            "PORT": str(port),
        }

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=str(work_dir),
                env=env,
                preexec_fn=lambda: _set_limits() if os.name != "nt" else None,
            )

            cls._instances[name] = {
                "process": process,
                "log_file": log_file,
                "log_fh": log_fh,
                "bot_type": bot_type,
                "work_dir": work_dir,
                "user_id": user_id,
                "bot_id": bot_id,
                "port": port,
            }

            logger.info(f"Bot {name} started (PID {process.pid}, port {port})")
            return {
                "status": "success",
                "container_id": name,
                "container_name": name,
                "message": f"Bot {name} started",
            }

        except Exception as e:
            log_fh.close()
            logger.error(f"Failed to start bot {name}: {e}")
            return {"status": "error", "message": str(e)}

    @classmethod
    async def stop_bot(cls, container_id: str) -> dict:
        if not container_id:
            return {"status": "error", "message": "No container ID"}

        instance = cls._instances.get(container_id)
        if not instance:
            return {"status": "error", "message": "Bot not running"}

        process = instance["process"]
        try:
            if process.returncode is None:
                proc = psutil.Process(process.pid)
                for child in proc.children(recursive=True):
                    child.terminate()
                proc.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    for child in proc.children(recursive=True):
                        child.kill()
                    proc.kill()
                    await process.wait()
        except (psutil.NoSuchProcess, ProcessLookupError):
            pass

        if instance.get("log_fh"):
            instance["log_fh"].close()
        cls._instances.pop(container_id, None)
        logger.info(f"Bot {container_id} stopped")
        return {"status": "success", "message": "Bot stopped"}

    @classmethod
    async def get_logs(cls, container_id: str, tail: int = 100) -> str:
        if not container_id:
            return ""
        instance = cls._instances.get(container_id)
        if not instance:
            return ""
        log_file = instance.get("log_file")
        if not log_file or not log_file.exists():
            return ""
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(lines[-tail:])
        except Exception:
            return ""

    @classmethod
    def get_status(cls, container_id: str) -> str:
        if not container_id:
            return "stopped"
        instance = cls._instances.get(container_id)
        if not instance:
            return "stopped"
        process = instance["process"]
        if process.returncode is not None:
            cls._instances.pop(container_id, None)
            return "crashed" if process.returncode != 0 else "stopped"
        return "running"

    @classmethod
    def get_resource_usage(cls, container_id: str) -> dict:
        if not container_id:
            return {"cpu": 0, "memory_mb": 0}
        instance = cls._instances.get(container_id)
        if not instance:
            return {"cpu": 0, "memory_mb": 0}
        process = instance["process"]
        try:
            proc = psutil.Process(process.pid)
            cpu = proc.cpu_percent(interval=0.1)
            mem = proc.memory_info().rss / 1024 / 1024
            return {"cpu": round(cpu, 1), "memory_mb": round(mem, 1)}
        except (psutil.NoSuchProcess, ProcessLookupError):
            return {"cpu": 0, "memory_mb": 0}

    @classmethod
    def container_exists(cls, container_id: str) -> bool:
        if not container_id:
            return False
        return container_id in cls._instances


def _set_limits():
    if not HAS_RESOURCE:
        return
    try:
        mem_bytes = settings.container_mem_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (50, 50))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    except Exception:
        pass
