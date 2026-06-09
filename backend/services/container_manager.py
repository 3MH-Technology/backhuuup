import asyncio
import logging
import os
import re
import resource
import subprocess
import sys
from pathlib import Path

import psutil

from config import settings

logger = logging.getLogger("wolfhost.container")

BOTS_DIR = Path("/app/data/bots")


class ContainerManager:
    _instances: dict[str, dict] = {}
    _port_allocator = iter(range(9000, 10000))

    @staticmethod
    def _allocate_port() -> int:
        return next(ContainerManager._port_allocator)

    @staticmethod
    def _release_port(port: int):
        pass

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
    def _write_main_file(work_dir: Path, bot_type: str, content: str):
        filename = "bot.py" if bot_type == "python" else "index.php"
        (work_dir / filename).write_text(content, encoding="utf-8")

    @staticmethod
    def _write_requirements(work_dir: Path, requirements: str):
        if requirements.strip():
            (work_dir / "requirements.txt").write_text(requirements, encoding="utf-8")

    @classmethod
    async def start_bot(cls, bot_id: int, user_id: int, slug: str,
                        bot_type: str, main_file_content: str,
                        requirements: str = "") -> dict:
        name = cls.container_name(user_id, slug)
        work_dir = cls._work_dir(user_id, bot_id)

        cls._write_main_file(work_dir, bot_type, main_file_content)
        cls._write_requirements(work_dir, requirements)

        log_file = work_dir / "bot.log"
        log_fh = open(log_file, "w", encoding="utf-8")

        port = cls._allocate_port()

        if bot_type == "python":
            if requirements.strip():
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        [sys.executable, "-m", "pip", "install", "-q",
                         "-r", str(work_dir / "requirements.txt")],
                        cwd=str(work_dir),
                        capture_output=True, timeout=120,
                    ),
                )
            cmd = [sys.executable, "-u", str(work_dir / "bot.py")]
        else:
            cmd = ["php", "-S", f"0.0.0.0:{port}", "-t", str(work_dir), str(work_dir / "index.php")]

        env = {
            **os.environ,
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
    try:
        mem_bytes = settings.container_mem_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    except Exception:
        pass
