import logging
import os
from pathlib import Path

from config import settings

logger = logging.getLogger("wolfhost.docker")

_client = None
_available = False

IMAGES = {
    "python": os.environ.get("PYTHON_IMAGE", "python:3.11-slim"),
    "php": os.environ.get("PHP_IMAGE", "php:8.2-cli-alpine"),
    "static": os.environ.get("PYTHON_IMAGE", "python:3.11-slim"),
}

NETWORK = os.environ.get("DOCKER_NETWORK", "estidafa_bot_net")


def is_available() -> bool:
    global _client, _available
    if _client is not None:
        return _available
    try:
        import docker
        _client = docker.from_env()
        _client.ping()
        _available = True
        logger.info("Docker is available — using container isolation")
    except Exception as e:
        _client = None
        _available = False
        logger.warning(f"Docker not available — bot execution is disabled: {e}")
    return _available


def container_name(user_id: int, slug: str) -> str:
    safe = "".join(c for c in slug if c.isalnum() or c in "-_").lower()[:40]
    return f"wh_{user_id}_{safe}"


async def start_container(
    name: str,
    bot_type: str,
    work_dir: str,
    port: int,
    main_file_content: str = "",
    requirements: str = "",
    is_upload: bool = False,
) -> dict:
    if not _client:
        raise RuntimeError("Docker not available")

    image = IMAGES.get(bot_type, "python:3.11-slim")
    work_dir_path = Path(work_dir)

    # Determine command based on bot type
    if bot_type == "static":
        cmd = ["python", "-m", "http.server", str(port), "-d", "/app"]
    elif bot_type == "php":
        cmd = ["php", "-S", f"0.0.0.0:{port}", "-c", "/app/.php/php.ini", "/app/index.php"]
    else:
        cmd = ["python", "/app/_wolf_boot.py"]

    # Build environment
    env = {
        "PORT": str(port),
        "HOME": "/app",
        "TMPDIR": "/app",
        "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1",
    }

    # Remove existing container with same name
    try:
        old = _client.containers.get(name)
        old.remove(force=True)
    except:
        pass

    # Ensure network exists
    try:
        net = _client.networks.get(NETWORK)
    except:
        logger.info(f"Creating Docker network '{NETWORK}'")
        net = _client.networks.create(NETWORK, driver="bridge", internal=False)

    container = _client.containers.create(
        image=image,
        name=name,
        command=cmd,
        working_dir="/app",
        volumes={str(work_dir_path): {"bind": "/app", "mode": "rw"}},
        environment=env,
        network=NETWORK,
        mem_limit=f"{settings.container_mem_limit_mb}m",
        cpu_period=100000,
        cpu_quota=int(settings.container_cpu_nanos / 10000) if settings.container_cpu_nanos else 50000,
        # Security hardening
        security_opt=["no-new-privileges:true"],
        cap_drop=["ALL"],
        read_only=False,
        detach=True,
    )

    container.start()

    # Get container IP on our network
    ip = None
    container.reload()
    net_data = container.attrs.get("NetworkSettings", {}).get("Networks", {})
    if NETWORK in net_data:
        ip = net_data[NETWORK]["IPAddress"]

    logger.info(f"Started Docker container '{name}' ({container.short_id}) on IP {ip} port {port}")
    return {"container_id": container.id, "short_id": container.short_id, "ip": ip}


async def stop_container(name: str):
    if not _client:
        return
    try:
        container = _client.containers.get(name)
        container.stop(timeout=5)
        container.remove(force=True)
        logger.info(f"Stopped and removed Docker container '{name}'")
    except Exception as e:
        logger.warning(f"Failed to stop Docker container '{name}': {e}")


def get_status(name: str) -> str:
    if not _client:
        return "stopped"
    try:
        container = _client.containers.get(name)
        status = container.status
        if status == "running":
            return "running"
        elif status == "exited":
            return "crashed"
        return "stopped"
    except:
        return "stopped"


def get_container_ip(name: str) -> str:
    if not _client:
        return ""
    try:
        container = _client.containers.get(name)
        container.reload()
        net_data = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        if NETWORK in net_data:
            return net_data[NETWORK]["IPAddress"]
        return ""
    except:
        return ""


def get_resource_usage(name: str) -> dict:
    if not _client:
        return {"cpu": 0, "memory_mb": 0}
    try:
        container = _client.containers.get(name)
        stats = container.stats(stream=False)
        cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        system_delta = stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
        cpu_percent = 0.0
        if system_delta > 0:
            cpu_percent = (cpu_delta / system_delta) * 100.0
        mem_bytes = stats["memory_stats"].get("usage", 0)
        return {"cpu": round(cpu_percent, 1), "memory_mb": round(mem_bytes / 1024 / 1024, 1)}
    except:
        return {"cpu": 0, "memory_mb": 0}


def cleanup_stale():
    if not _client:
        return
    try:
        for container in _client.containers.list(all=True, filters={"name": "wh_"}):
            if container.status != "running":
                container.remove(force=True)
                logger.info(f"Cleaned up stale container {container.name}")
    except Exception as e:
        logger.warning(f"Cleanup error: {e}")


def container_exists(name: str) -> bool:
    if not _client:
        return False
    try:
        _client.containers.get(name)
        return True
    except:
        return False
