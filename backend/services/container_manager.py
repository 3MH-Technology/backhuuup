import asyncio
import logging
import os
import re
import zipfile
from pathlib import Path

from config import settings
from services import docker_manager

logger = logging.getLogger("wolfhost.container")

BOTS_DIR = Path("/app/data/bots")
ALLOWED_EXTENSIONS = {".py", ".php", ".txt", ".html", ".css", ".js", ".json", ".xml", ".md", ".htaccess", ".env.example"}
MAX_UPLOAD_SIZE = 5 * 1024 * 1024
MAX_ZIP_SIZE = 10 * 1024 * 1024


class ContainerManager:
    _instances: dict[str, dict] = {}
    _port_pool: set[int] = set(range(9000, 10000))
    _resource_cache: dict[str, dict] = {}
    _port_lock = asyncio.Lock()

    @staticmethod
    def check_docker():
        if docker_manager.is_available():
            logger.info("✅ Docker is available — bots will run in isolated containers")
        else:
            logger.error("❌ Docker is NOT available — bot execution is disabled")
            logger.error("   Install Docker and restart the platform to enable bot hosting")

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
    def get_bot_target(container_id: str) -> str | None:
        inst = ContainerManager._instances.get(container_id)
        if inst:
            return inst.get("target", "127.0.0.1:0")
        return None

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
    def _write_php_ini(work_dir: Path, user_id: int, bot_id: int):
        basedir = Path("/app/data/bots") / str(user_id) / str(bot_id)
        ini_dir = work_dir / ".php"
        ini_dir.mkdir(parents=True, exist_ok=True)
        ini_path = ini_dir / "php.ini"
        ini_path.write_text(
            f"; Wolf Host — per-bot PHP config (user={user_id}, bot={bot_id})\n"
            f"open_basedir = {basedir}:/tmp/\n"
            f"disable_functions = exec,system,shell_exec,passthru,popen,proc_open,proc_nice,proc_terminate,proc_close,pcntl_exec,pcntl_fork,pcntl_signal,pcntl_alarm,pcntl_wait,show_source,highlight_file,curl_exec,curl_multi_exec,mail,mb_send_mail,shmop_open,shmop_read,shmop_write,shmop_close,shmop_delete,shm_attach,shm_detach,shm_remove,shm_get_var,shm_has_var,shm_put_var,shm_remove_var,sem_acquire,sem_get,sem_release,sem_remove,msg_get_queue,msg_receive,msg_remove_queue,msg_send,msg_set_queue,msg_stat_queue,fsockopen,pfsockopen,stream_socket_client,stream_socket_server,stream_socket_accept,stream_socket_pair,dns_get_record,gethostbyname,gethostbyaddr,ftp_connect,ftp_login,ftp_get,ftp_put,ftp_nb_fput,ftp_nb_fget,ftp_raw,ftp_rawlist,imap_open,imap_mail,imap_mail_copy,imap_mail_move,ldap_connect,ldap_bind,ldap_search,pg_connect,pg_query,pg_send_query,pg_execute,pg_put_line,pg_end_copy,pg_copy_from,mysql_connect,mysqli_connect,socket_create,socket_connect,socket_bind,socket_listen,socket_accept,socket_read,socket_write,socket_send,socket_recv,socket_sendto,socket_recvfrom,chgrp,chown,chroot,disk_free_space,disk_total_space,diskfreespace,dl,error_log,file_put_contents,file_get_contents,file,ftp_get,ini_alter,ini_restore,ini_set,link,linkinfo,parse_ini_file,parse_ini_string,pfsockopen,phpinfo,posix_kill,posix_mkfifo,posix_setpgid,posix_setsid,posix_setuid,putenv,set_time_limit,symlink,sys_getloadavg,chdir\n"
            f"allow_url_fopen = Off\n"
            f"allow_url_include = Off\n"
            f"max_execution_time = 30\n"
            f"max_input_time = 30\n"
            f"memory_limit = 32M\n"
            f"post_max_size = 8M\n"
            f"upload_max_filesize = 8M\n"
            f"enable_dl = Off\n"
            f"register_globals = Off\n"
            f"display_errors = Off\n"
            f"display_startup_errors = Off\n"
            f"log_errors = On\n"
            f"expose_php = Off\n"
            f"opcache.enable = 0\n"
            f"opcache.enable_cli = 0\n",
            encoding="utf-8",
        )
        return ini_path

    @staticmethod
    def _write_main_file(work_dir: Path, bot_type: str, content: str):
        if bot_type == "python":
            filename = "bot.py"
        elif bot_type == "php":
            filename = "index.php"
        else:
            return
        (work_dir / filename).write_text(content, encoding="utf-8")

    @staticmethod
    def _write_requirements(work_dir: Path, requirements: str):
        if requirements.strip():
            (work_dir / "requirements.txt").write_text(requirements, encoding="utf-8")

    @staticmethod
    def _write_boot_loader(work_dir: Path):
        code = r'''"""_wolf_boot.py — Wolf Host boot loader."""
import os as _os, sys as _sys

# ── auto-configure Telegram proxy (local HTTP → Cloudflare Worker) ──
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
        if not docker_manager.is_available():
            return {"status": "error", "message": "Docker is required — bot execution disabled without Docker"}

        name = cls.container_name(user_id, slug)
        work_dir = cls._work_dir(user_id, bot_id)

        if name in cls._instances:
            logger.info(f"Stopping previous instance of {name} before restart")
            await cls.stop_bot(name)

        if not is_upload:
            cls._write_main_file(work_dir, bot_type, main_file_content)
            if bot_type == "python":
                cls._write_requirements(work_dir, requirements)

        port = await cls._allocate_port()

        try:
            if bot_type == "python":
                cls._write_boot_loader(work_dir)
            if bot_type == "php":
                cls._write_php_ini(work_dir, user_id, bot_id)

            result = await docker_manager.start_container(
                name=name,
                bot_type=bot_type,
                work_dir=str(work_dir),
                port=port,
                main_file_content=main_file_content,
                requirements=requirements,
                is_upload=is_upload,
            )
            target = f"{name}:{port}"
            cls._instances[name] = {
                "target": target,
                "port": port,
                "bot_type": bot_type,
                "work_dir": work_dir,
                "user_id": user_id,
                "bot_id": bot_id,
            }
            cls._resource_cache[name] = {"cpu": 0.0, "memory_mb": 0.0}
            logger.info(f"Bot {name} started in Docker container (target {target})")
            return {
                "status": "success",
                "container_id": name,
                "container_name": name,
                "message": f"Bot {name} started in Docker",
            }
        except Exception as e:
            cls._release_port(port)
            cls._instances.pop(name, None)
            cls._resource_cache.pop(name, None)
            logger.error(f"Failed to start bot {name} in Docker: {e}")
            return {"status": "error", "message": str(e)}

    @classmethod
    async def stop_bot(cls, container_id: str) -> dict:
        if not container_id:
            return {"status": "error", "message": "No container ID"}
        instance = cls._instances.pop(container_id, None)
        if not instance:
            return {"status": "error", "message": "Bot not running"}
        port = instance.get("port")
        await docker_manager.stop_container(container_id)
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
        if log_file and log_file.exists():
            try:
                lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                return "\n".join(lines[-tail:])
            except Exception:
                pass
        return ""

    @classmethod
    def get_status(cls, container_id: str) -> str:
        if not container_id:
            return "stopped"
        instance = cls._instances.get(container_id)
        if not instance:
            return "stopped"
        return docker_manager.get_status(container_id)

    @classmethod
    def get_resource_usage(cls, container_id: str) -> dict:
        if not container_id:
            return {"cpu": 0, "memory_mb": 0}
        instance = cls._instances.get(container_id)
        if not instance:
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
        for cid in list(cls._instances.keys()):
            cls._resource_cache[cid] = docker_manager.get_resource_usage(cid)

    @classmethod
    async def cleanup_stale(cls):
        docker_manager.cleanup_stale()
        stale = []
        for cid, inst in cls._instances.items():
            status = docker_manager.get_status(cid)
            if status != "running":
                stale.append(cid)
        for cid in stale:
            cls._instances.pop(cid, None)
            cls._resource_cache.pop(cid, None)
