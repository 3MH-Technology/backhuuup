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
    _port_pool: set[int] = set(range(9000, 10000))
    _resource_cache: dict[str, dict] = {}
    _port_lock = asyncio.Lock()

    @staticmethod
    async def _allocate_port() -> int:
        async with ContainerManager._port_lock:
            if not ContainerManager._port_pool:
                raise RuntimeError("No available ports")
            port = min(ContainerManager._port_pool)
            ContainerManager._port_pool.remove(port)
            return port

    @staticmethod
    def _release_port(port: int):
        if 9000 <= port <= 9999:
            ContainerManager._port_pool.add(port)

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
        normalized = Path(path).as_posix()
        if ".." in normalized or normalized.startswith("/") or normalized.startswith("\\"):
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

    @staticmethod
    def _write_boot_loader(work_dir: Path):
        """Boot loader — patches telebot proxy, runs user bot."""
        code = r'''"""_wolf_boot.py — Wolf Host boot loader."""
import os as _os, sys as _sys

# ── auto-configure Telegram proxy (local HTTP → Cloudflare Worker) ──
# Bot subprocess connects via HTTP to the main app (port 7860),
# which forwards to the Cloudflare Worker via HTTPS.
# This avoids SSL memory overhead in the memory-limited bot subprocess.
_cf = _os.environ.get("CF_PROXY", "")
if _cf:
    _proxy_url = "http://127.0.0.1:7860/api/__proxy/bot{0}/{1}"
    _orig_import = __builtins__.__import__ if isinstance(__builtins__, dict) else __builtins__.__import__
    def _hook(name, *a, **kw):
        mod = _orig_import(name, *a, **kw)
        if name == "telebot.apihelper":
            try:
                mod.API_URL = _proxy_url
            except Exception:
                pass
        elif name == "telebot":
            try:
                mod.apihelper.API_URL = _proxy_url
            except Exception:
                pass
        return mod
    (__builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__)["__import__"] = _hook

# ── run user bot ──
_user = _sys.argv[1] if len(_sys.argv) > 1 else "bot.py"
_sys.argv = [_user] + _sys.argv[2:]
with open(_user, "rb") as _f:
    exec(compile(_f.read(), _user, "exec"), {"__name__": "__main__", "__file__": _user})
'''
        f = work_dir / "_wolf_boot.py"
        f.write_text(code, encoding="utf-8")
        f.chmod(0o444)

    @classmethod
    async def start_bot(cls, bot_id: int, user_id: int, slug: str,
                        bot_type: str, main_file_content: str = "",
                        requirements: str = "", is_upload: bool = False) -> dict:
        name = cls.container_name(user_id, slug)
        work_dir = cls._work_dir(user_id, bot_id)

        # Stop any existing instance first (prevents multiple copies → 409 Conflict)
        if name in cls._instances:
            logger.info(f"Stopping previous instance of {name} before restart")
            await cls.stop_bot(name)

        if not is_upload:
            cls._write_main_file(work_dir, bot_type, main_file_content)
            if bot_type == "python":
                cls._write_requirements(work_dir, requirements)

        log_file = work_dir / "bot.log"
        log_fh = open(log_file, "w", encoding="utf-8")

        port = await cls._allocate_port()

        if bot_type == "python":
            # ── isolated venv per bot ──
            venv_path = work_dir / "venv"
            python_exe = str(venv_path / "bin" / "python")
            loop = asyncio.get_event_loop()
            if not (venv_path / "pyvenv.cfg").exists():
                logger.info(f"Creating venv for {name} at {venv_path}")
                await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        [sys.executable, "-m", "venv", str(venv_path)],
                        capture_output=True, timeout=60,
                    ),
                )
            # ── auto-install requirements in isolated venv ──
            req_file = work_dir / "requirements.txt"
            if req_file.exists() and req_file.stat().st_size > 0:
                await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        [python_exe, "-m", "pip", "install", "-q",
                         "-r", str(req_file)],
                        cwd=str(work_dir),
                        capture_output=True, timeout=120,
                    ),
                )
            cls._write_boot_loader(work_dir)
            entry = work_dir / "bot.py"
            if not entry.exists():
                py_files = [f for f in work_dir.glob("*.py") if f.name != "_wolf_boot.py"]
                entry = py_files[0] if py_files else entry
            cmd = [python_exe, "-u", str(work_dir / "_wolf_boot.py"), str(entry)]
        else:
            entry = work_dir / "index.php"
            if not entry.exists():
                php_files = list(work_dir.glob("*.php"))
                entry = php_files[0] if php_files else entry
            cmd = ["php", "-S", f"127.0.0.1:{port}", "-t", str(work_dir), str(entry)]

        cf_proxy = os.environ.get("CF_PROXY", "")
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "en_US.UTF-8"),
            "HOME": str(work_dir),
            "TMPDIR": str(work_dir),
            "PYTHONUNBUFFERED": "1",
            "PYTHONNOUSERSITE": "1",
            "PORT": str(port),
            "CF_PROXY": cf_proxy,
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
            cls._resource_cache[name] = {"cpu": 0.0, "memory_mb": 0.0}

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

        port = instance.get("port")
        if instance.get("log_fh"):
            instance["log_fh"].close()
        cls._instances.pop(container_id, None)
        cls._resource_cache.pop(container_id, None)
        if port:
            cls._release_port(port)
        logger.info(f"Bot {container_id} stopped (port {port} released)")
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
        if container_id not in cls._instances:
            return {"cpu": 0, "memory_mb": 0}
        cached = cls._resource_cache.get(container_id)
        if cached:
            return cached
        return {"cpu": 0, "memory_mb": 0}

    @classmethod
    def container_exists(cls, container_id: str) -> bool:
        if not container_id:
            return False
        return container_id in cls._instances

    @classmethod
    async def monitor_loop(cls):
        loop = asyncio.get_event_loop()
        while True:
            try:
                await loop.run_in_executor(None, cls._poll_all_resources)
            except Exception:
                pass
            await asyncio.sleep(3)

    @classmethod
    def _poll_all_resources(cls):
        for cid, inst in list(cls._instances.items()):
            process = inst.get("process")
            if not process or process.returncode is not None:
                cls._resource_cache.pop(cid, None)
                continue
            try:
                proc = psutil.Process(process.pid)
                with proc.oneshot():
                    cpu = proc.cpu_percent(interval=0.1)
                    mem = proc.memory_info().rss / 1024 / 1024
                    children = proc.children(recursive=True)
                    for child in children:
                        try:
                            with child.oneshot():
                                cpu += child.cpu_percent(interval=0.1)
                                mem += child.memory_info().rss / 1024 / 1024
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                cls._resource_cache[cid] = {
                    "cpu": round(min(cpu, 100.0), 1),
                    "memory_mb": round(mem, 1),
                }
            except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
                cls._resource_cache.pop(cid, None)

    @classmethod
    async def cleanup_stale(cls):
        stale = []
        for cid, inst in cls._instances.items():
            process = inst.get("process")
            if process and process.returncode is not None:
                stale.append(cid)
        for cid in stale:
            inst = cls._instances.pop(cid, None)
            if inst and inst.get("log_fh"):
                inst["log_fh"].close()
            cls._resource_cache.pop(cid, None)


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
