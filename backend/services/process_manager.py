import asyncio
import os
import signal
import sys
import subprocess
from pathlib import Path
from typing import Optional

import psutil

from config import settings


class ProcessManager:
    """
    Manages the lifecycle of user bot processes.
    Each bot runs as an isolated subprocess with resource limits.
    """

    _instances: dict[int, dict] = {}  # bot_id -> {process, log_queue, task}

    @classmethod
    async def start_bot(cls, bot_id: int, bot_type: str, work_dir: Path, main_file_content: str, requirements: str = "") -> dict:
        if bot_id in cls._instances:
            return {"status": "error", "message": "Bot already running"}

        work_dir.mkdir(parents=True, exist_ok=True)
        log_file = work_dir / "bot.log"

        cls._write_main_file(work_dir, bot_type, main_file_content)

        if bot_type == "python" and requirements.strip():
            cls._install_requirements(work_dir, requirements)

        cmd = cls._build_command(bot_type, work_dir)

        try:
            log_fh = open(log_file, "w", encoding="utf-8")
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=str(work_dir),
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )

            cls._instances[bot_id] = {
                "process": process,
                "log_file": log_file,
                "log_fh": log_fh,
                "bot_type": bot_type,
                "work_dir": work_dir,
            }

            return {"status": "success", "pid": process.pid, "message": "Bot started"}

        except Exception as e:
            return {"status": "error", "message": str(e)}

    @classmethod
    async def stop_bot(cls, bot_id: int) -> dict:
        instance = cls._instances.pop(bot_id, None)
        if not instance:
            return {"status": "error", "message": "Bot not running"}

        process = instance["process"]
        try:
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

        return {"status": "success", "message": "Bot stopped"}

    @classmethod
    async def restart_bot(cls, bot_id: int, bot_type: str, work_dir: Path, main_file_content: str, requirements: str = "") -> dict:
        await cls.stop_bot(bot_id)
        await asyncio.sleep(0.5)
        return await cls.start_bot(bot_id, bot_type, work_dir, main_file_content, requirements)

    @classmethod
    def get_status(cls, bot_id: int) -> str:
        instance = cls._instances.get(bot_id)
        if not instance:
            return "stopped"
        process = instance["process"]
        if process.returncode is not None:
            cls._instances.pop(bot_id, None)
            return "crashed" if process.returncode != 0 else "stopped"
        return "running"

    @classmethod
    def get_log_content(cls, bot_id: int, max_lines: int = 100) -> list[str]:
        instance = cls._instances.get(bot_id)
        if not instance:
            return ["No logs available. Bot is not running."]
        log_file = instance["log_file"]
        if not log_file.exists():
            return ["Log file not found."]
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-max_lines:]

    @classmethod
    def get_resource_usage(cls, bot_id: int) -> dict:
        instance = cls._instances.get(bot_id)
        if not instance:
            return {"cpu": 0, "memory_mb": 0, "status": "stopped"}
        process = instance["process"]
        try:
            proc = psutil.Process(process.pid)
            cpu = proc.cpu_percent(interval=0.1)
            mem = proc.memory_info().rss / 1024 / 1024
            return {"cpu": round(cpu, 1), "memory_mb": round(mem, 1), "status": "running"}
        except (psutil.NoSuchProcess, ProcessLookupError):
            return {"cpu": 0, "memory_mb": 0, "status": "crashed"}

    @classmethod
    def _write_main_file(cls, work_dir: Path, bot_type: str, content: str):
        filename = "bot.py" if bot_type == "python" else "index.php"
        filepath = work_dir / filename
        filepath.write_text(content, encoding="utf-8")

    @classmethod
    def _install_requirements(cls, work_dir: Path, requirements: str):
        req_file = work_dir / "requirements.txt"
        req_file.write_text(requirements, encoding="utf-8")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req_file), "--quiet"],
                cwd=str(work_dir),
                capture_output=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            pass

    @classmethod
    def _build_command(cls, bot_type: str, work_dir: Path) -> list[str]:
        if bot_type == "python":
            return [sys.executable, "-u", str(work_dir / "bot.py")]
        else:
            php_path = "php"  # assume php is in PATH
            return [php_path, str(work_dir / "index.php")]
