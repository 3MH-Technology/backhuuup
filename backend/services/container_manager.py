"""
Wolf Host — استضافة الذب هوست 🐺
Production container manager using Docker SDK (docker-py).

Communicates with the internal Docker daemon (DinD) inside the
Hugging Face Space.  Every user bot runs in a fully isolated
container with strict resource limits and zero host access.

Developer:  الذئب الأبيض 🐺
Telegram:   @j49_c
Channel:    @O5O6J
X:          https://x.com/wolfhost_1
"""
import asyncio
import re
from pathlib import Path
from typing import Optional

import docker
from docker import DockerClient
from docker.errors import DockerException, NotFound, APIError
from docker.types import LogConfig

from config import settings


class ContainerManager:
    """
    Manages the full lifecycle of user bot Docker containers
    via the internal DinD Docker daemon.
    """

    _client: Optional[DockerClient] = None

    # ── Docker client (lazy, thread-safe) ─────────────────────────
    @classmethod
    def _get_client(cls) -> DockerClient:
        if cls._client is None:
            try:
                cls._client = docker.from_env()
            except DockerException as e:
                raise RuntimeError(f"Cannot connect to Docker daemon: {e}")
        return cls._client

    # ── Naming & helpers ──────────────────────────────────────────
    @staticmethod
    def container_name(user_id: int, slug: str) -> str:
        safe_slug = re.sub(r'[^a-z0-9\-]', '-', slug.lower()).strip('-')[:60]
        return f"wh_{user_id}_{safe_slug}"

    @staticmethod
    def _image_for(bot_type: str) -> str:
        return settings.python_image if bot_type == "python" else settings.php_image

    @staticmethod
    def _entrypoint_cmd(bot_type: str) -> list[str]:
        if bot_type == "python":
            return ["python", "-u", "/app/bot.py"]
        return ["php", "-S", "0.0.0.0:8080", "/app/index.php"]

    @staticmethod
    def _write_main_file(work_dir: Path, bot_type: str, content: str):
        work_dir.mkdir(parents=True, exist_ok=True)
        filename = "bot.py" if bot_type == "python" else "index.php"
        (work_dir / filename).write_text(content, encoding="utf-8")

    @staticmethod
    def _write_requirements(work_dir: Path, requirements: str):
        if requirements.strip():
            (work_dir / "requirements.txt").write_text(requirements, encoding="utf-8")

    # ── Core lifecycle ─────────────────────────────────────────────
    @classmethod
    async def start_bot(cls, bot_id: int, user_id: int, slug: str,
                        bot_type: str, main_file_content: str,
                        requirements: str = "") -> dict:
        """
        Create & start a fully isolated Docker container.

        In Hugging Face Spaces DinD mode, we do NOT map host ports.
        All Telegram webhook traffic arrives via Cloudflare → HF Space
        → /api/webhook/{slug} endpoint, which proxies internally.
        The container only needs an internal port 8080 for PHP's
        built-in web server or a Python webhook listener.
        """
        name = cls.container_name(user_id, slug)
        image = cls._image_for(bot_type)
        cmd = cls._entrypoint_cmd(bot_type)
        client = cls._get_client()

        # ── Prepare code directory ─────────────────────────────────
        work_dir = Path("/tmp") / f"wh_{user_id}_{bot_id}"
        cls._write_main_file(work_dir, bot_type, main_file_content)
        cls._write_requirements(work_dir, requirements)

        # ── Build custom image if Python requirements exist ─────────
        if bot_type == "python" and requirements.strip():
            dockerfile = f"""FROM {image}
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY . /app
WORKDIR /app
"""
            (work_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")
            try:
                loop = asyncio.get_event_loop()
                img, _ = await loop.run_in_executor(
                    None,
                    lambda: client.images.build(
                        path=str(work_dir),
                        dockerfile="Dockerfile",
                        tag=f"wh_{user_id}_{bot_id}:latest",
                        rm=True,
                        forcerm=True,
                    ),
                )
                image = img.id
            except APIError as e:
                return {"status": "error", "message": f"Build failed: {e}"}

        # ── Log config (prevents disk exhaustion) ──────────────────
        log_config = LogConfig(
            type=LogConfig.types.JSON,
            config={
                "max-size": settings.container_log_max_size,
                "max-file": str(settings.container_log_max_file),
            },
        )

        # ── Create & start container ───────────────────────────────
        try:
            loop = asyncio.get_event_loop()
            container = await loop.run_in_executor(
                None,
                lambda: client.containers.run(
                    image=image,
                    command=cmd,
                    name=name,
                    hostname=name,
                    detach=True,
                    network=settings.docker_network,
                    mem_limit=f"{settings.container_mem_limit_mb}m",
                    nano_cpus=settings.container_cpu_nanos,
                    user="1000:1000",
                    read_only=True,
                    tmpfs={"/app/data": "uid=1000,gid=1000,mode=755"},
                    volumes={str(work_dir): {"bind": "/app", "mode": "ro"}},
                    log_config=log_config,
                    remove=True,
                    restart_policy={"Name": "no"},
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
                    labels={
                        "wolfhost.managed": "true",
                        "wolfhost.user_id": str(user_id),
                        "wolfhost.bot_id": str(bot_id),
                        "wolfhost.slug": slug,
                    },
                    dns=["1.1.1.1", "8.8.8.8"],
                    dns_search=[""],
                    extra_hosts={},
                ),
            )

            # ── Ensure container is ONLY on bot net ────────────────
            try:
                container.disconnect("bridge")
            except APIError:
                pass

            return {
                "status": "success",
                "container_id": container.id,
                "container_name": name,
                "message": f"Container {name} started",
            }

        except APIError as e:
            return {"status": "error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": f"Unexpected error: {e}"}

    @classmethod
    async def stop_bot(cls, container_id: str) -> dict:
        """Gracefully stop and remove a bot container."""
        if not container_id:
            return {"status": "error", "message": "No container ID"}
        client = cls._get_client()
        try:
            container = client.containers.get(container_id)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, container.stop, timeout=10)
            await loop.run_in_executor(None, container.remove, force=True)
            return {"status": "success", "message": "Container stopped"}
        except NotFound:
            return {"status": "error", "message": "Container not found"}
        except APIError as e:
            return {"status": "error", "message": str(e)}

    @classmethod
    async def get_logs(cls, container_id: str, tail: int = 100) -> str:
        if not container_id:
            return ""
        client = cls._get_client()
        try:
            container = client.containers.get(container_id)
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None,
                lambda: container.logs(
                    tail=tail, timestamps=False
                ).decode("utf-8", errors="replace"),
            )
            return raw
        except (NotFound, APIError):
            return ""

    @classmethod
    def get_status(cls, container_id: str) -> str:
        if not container_id:
            return "stopped"
        client = cls._get_client()
        try:
            container = client.containers.get(container_id)
            state = container.attrs["State"]
            if state.get("Running"):
                return "running"
            if state.get("ExitCode", 0) != 0:
                return "crashed"
            return "stopped"
        except (NotFound, APIError):
            return "stopped"

    @classmethod
    def get_resource_usage(cls, container_id: str) -> dict:
        if not container_id:
            return {"cpu": 0, "memory_mb": 0}
        client = cls._get_client()
        try:
            container = client.containers.get(container_id)
            stats = container.stats(stream=False)
            cpu_delta = (
                stats["cpu_stats"]["cpu_usage"]["total_usage"]
                - stats["precpu_stats"]["cpu_usage"]["total_usage"]
            )
            system_delta = (
                stats["cpu_stats"]["system_cpu_usage"]
                - stats["precpu_stats"]["system_cpu_usage"]
            )
            num_cpus = stats["cpu_stats"]["online_cpus"]
            cpu_percent = 0.0
            if system_delta > 0 and cpu_delta > 0:
                cpu_percent = (cpu_delta / system_delta) * num_cpus * 100.0
            mem_bytes = stats["memory_stats"].get("usage", 0)
            mem_mb = round(mem_bytes / 1024 / 1024, 1)
            return {"cpu": round(cpu_percent, 1), "memory_mb": mem_mb}
        except (NotFound, APIError, KeyError):
            return {"cpu": 0, "memory_mb": 0}

    @classmethod
    def container_exists(cls, container_id: str) -> bool:
        if not container_id:
            return False
        client = cls._get_client()
        try:
            client.containers.get(container_id)
            return True
        except NotFound:
            return False

    @staticmethod
    def _cleanup_work_dir(user_id: int, bot_id: int):
        path = Path("/tmp") / f"wh_{user_id}_{bot_id}"
        if path.exists():
            import shutil
            shutil.rmtree(path, ignore_errors=True)
