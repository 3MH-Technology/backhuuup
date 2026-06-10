"""Public API — WasmSandboxManager.

This is the only module that external code (routes, webhook) should import.
All sandbox interactions go through this manager.

SECURITY INVARIANTS:
  1. Every bot gets a SealedPolicy with an immutable cryptographic hash.
  2. The policy hash is part of the module cache key.
  3. Circuit breaker checks happen before Store creation.
  4. Disk quota is checked before writing any files.
  5. Source validation is defense-in-depth; WASM is the real boundary.
"""
import logging
from pathlib import Path

from .engine import get_engine, get_module
from .network import NetworkGateway, PolicyViolation
from .policy import (
    NetworkPolicy,
    SealedPolicy,
    sanitize_log,
)
from .registry import StoreRegistry
from .store import BotStore
from .storage import (
    check_disk_quota,
    cleanup,
    ensure_bot_dirs,
    ensure_dirs,
    write_bot_code,
    write_bot_files,
)
from .validator import validate_source, ValidationError

logger = logging.getLogger("wolfhost.wasm.manager")


class WasmSandboxManager:
    _gateway: NetworkGateway | None = None
    _initialized = False

    @classmethod
    def ensure_initialized(cls):
        if cls._initialized:
            return
        ensure_dirs()
        get_engine()
        StoreRegistry.start_epoch_timer()
        if cls._gateway is None:
            cls._gateway = NetworkGateway()
        cls._initialized = True
        logger.info("Sandbox manager initialised")

    @classmethod
    def _build_policy(
        cls,
        bot_id: str,
        user_id: int,
        bot_type: str,
    ) -> SealedPolicy:
        """Build a SealedPolicy for the given bot.

        The policy hash is automatically computed in __post_init__.
        This hash becomes part of the WASM module cache key.
        """
        allow_network = bot_type != "static"
        return SealedPolicy(
            bot_id=bot_id,
            user_id=user_id,
            allowed_network=allow_network,
            allowed_domains=frozenset({"api.telegram.org"}),
        )

    @classmethod
    async def start_bot(
        cls,
        bot_id: int,
        user_id: int,
        slug: str,
        bot_type: str,
        source_code: str = "",
        files: dict | None = None,
        is_upload: bool = False,
    ) -> dict:
        cls.ensure_initialized()

        if bot_type not in ("python", "php", "static"):
            return {"status": "error", "message": f"Unsupported: {bot_type}"}

        cid = f"wh_{user_id}_{slug}"

        # Circuit breaker check
        if StoreRegistry.is_quarantined(cid):
            return {
                "status": "error",
                "message": "Bot quarantined due to repeated failures. Try again later.",
            }

        # Clean previous instance
        existing = StoreRegistry.get(cid)
        if existing and not existing.is_closed:
            existing.close()
            StoreRegistry.remove(cid)

        # Source validation (defense-in-depth)
        if not is_upload and bot_type == "python" and source_code:
            try:
                source_code = validate_source(source_code)
            except ValidationError as e:
                return {"status": "error", "message": f"Validation: {e}"}

        # Disk quota
        if not check_disk_quota(user_id, bot_id):
            return {"status": "error", "message": "Disk quota exceeded"}

        # Write files
        ensure_bot_dirs(user_id, bot_id)
        if files:
            write_bot_files(user_id, bot_id, files)
        elif source_code:
            ext = {"python": "bot.py", "php": "index.php"}.get(bot_type)
            if ext:
                write_bot_code(user_id, bot_id, ext, source_code)

        # Build sealed policy
        policy = cls._build_policy(cid, user_id, bot_type)

        # Create store (policy is cemented here — immutable hash)
        store = BotStore(
            policy=policy,
            source_code=source_code or "",
            gateway=cls._gateway,
        )

        registered = await StoreRegistry.register(store)
        if not registered:
            store.close()
            return {"status": "error", "message": "Max stores reached or quarantined"}

        try:
            await store.start()
        except Exception as e:
            store.close()
            StoreRegistry.remove(cid)
            StoreRegistry.record_failure(cid)
            logger.error("Store start failed %s: %s", cid, e)
            return {"status": "error", "message": f"Start: {e}"}

        try:
            result = await store.run()
        except Exception as e:
            StoreRegistry.record_failure(cid)
            result = {"success": False, "error_type": "host", "error": str(e)}

        store.close()

        if result.get("success"):
            StoreRegistry.record_success(cid)
        else:
            StoreRegistry.record_failure(cid)

        return {
            "status": "success" if result.get("success") else "error",
            "container_id": cid,
            "policy_hash": policy.policy_hash,
            "request_id": result.get("request_id", ""),
            "sandbox_result": result,
        }

    @classmethod
    async def stop_bot(cls, container_id: str) -> dict:
        store = StoreRegistry.get(container_id)
        if store:
            store.close()
            StoreRegistry.remove(container_id)
            return {"status": "success", "message": "Stopped"}
        return {"status": "error", "message": "Not found"}

    @classmethod
    def get_status(cls, container_id: str) -> str:
        if StoreRegistry.is_quarantined(container_id):
            return "quarantined"
        store = StoreRegistry.get(container_id)
        if store and not store.is_closed:
            return "running"
        return "stopped"

    @classmethod
    def container_exists(cls, container_id: str) -> bool:
        store = StoreRegistry.get(container_id)
        return store is not None and not store.is_closed

    @classmethod
    async def shutdown(cls):
        if cls._gateway:
            await cls._gateway.close()
        await StoreRegistry.close_all()
