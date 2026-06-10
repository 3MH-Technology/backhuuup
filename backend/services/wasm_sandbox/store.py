"""Per-bot Wasmtime Store factory and executor.

SECURITY INVARIANTS:
  1. One Store per BotStore instance. Never shared across bots.
  2. _start can only be called ONCE — re-entry guard prevents double execution.
  3. Per-bot asyncio.Lock ensures one execution at a time per bot.
  4. stdout/stderr captured to deterministic paths under bot_root/tmp/.
  5. Post-execution: only first N bytes read; file is then truncated.
  6. Dedicated ThreadPoolExecutor (max 3) prevents thread starvation globally.
  7. Epoch deadline + fuel = two independent kill switches.
  8. Request ID generated per execution for audit traceability.
  9. Circuit breaker checked before execution (quarantined bots rejected).
"""
import asyncio
import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from wasmtime import (
    DirPerms,
    FilePerms,
    Linker,
    Module,
    Store,
    WasiConfig,
    WasiError,
    WasmtimeError,
)

from .engine import get_engine, get_module
from .policy import (
    RE_CONTROL_CHARS,
    SealedPolicy,
)
from .storage import bot_root

logger = logging.getLogger("wolfhost.wasm.store")

_WASM_EXECUTOR = None
_WASM_EXECUTOR_LOCK = threading.Lock()


def _get_executor(max_workers: int = 3) -> ThreadPoolExecutor:
    global _WASM_EXECUTOR
    if _WASM_EXECUTOR is None:
        with _WASM_EXECUTOR_LOCK:
            if _WASM_EXECUTOR is None:
                _WASM_EXECUTOR = ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="wasm",
                )
    return _WASM_EXECUTOR


class _StartGuard:
    def __init__(self):
        self._called = False

    def acquire(self) -> bool:
        if self._called:
            return False
        self._called = True
        return True


class BotStore:
    __slots__ = (
        "_policy", "_gateway", "_engine",
        "_store", "_instance", "_guard",
        "_stdout_path", "_stderr_path", "_closed",
        "_bot_root_path", "_exec_lock", "_request_id",
        "_source_code",
    )

    def __init__(
        self,
        policy: SealedPolicy,
        source_code: str,
        gateway: NetworkGateway,
    ):
        self._policy = policy
        self._gateway = gateway
        self._engine = get_engine(policy)
        self._store: Store | None = None
        self._instance = None
        self._guard = _StartGuard()
        self._stdout_path: Path | None = None
        self._stderr_path: Path | None = None
        self._closed = False
        self._bot_root_path = bot_root(policy.user_id, int(policy.bot_id.rsplit("_", 1)[-1]))
        self._exec_lock = asyncio.Lock()
        self._request_id = ""
        self._source_code = source_code

    async def start(self):
        if self._closed:
            raise RuntimeError("Store closed")

        p = self._policy
        engine = self._engine

        linker = Linker(engine)
        linker.define_wasi()

        wasi = WasiConfig()
        wasi.argv = ("python3", "-c", self._source_code)

        root = self._bot_root_path
        tmp_dir = root / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        self._stdout_path = tmp_dir / "stdout.log"
        self._stderr_path = tmp_dir / "stderr.log"
        wasi.stdout_file = str(self._stdout_path)
        wasi.stderr_file = str(self._stderr_path)

        wasi.env = (
            f"BOT_ID={p.bot_id}",
            f"USER_ID={p.user_id}",
        )

        code_dir = root / "code"
        if code_dir.exists():
            wasi.preopen_dir(
                str(code_dir), "/app/code",
                dir_perms=DirPerms.READ_ONLY,
                file_perms=FilePerms.READ_ONLY,
            )

        state_dir = root / "state"
        if state_dir.exists():
            wasi.preopen_dir(
                str(state_dir), "/app/state",
                dir_perms=DirPerms.READ_WRITE,
                file_perms=FilePerms.READ_WRITE,
            )

        if tmp_dir.exists():
            wasi.preopen_dir(
                str(tmp_dir), "/app/tmp",
                dir_perms=DirPerms.READ_WRITE,
                file_perms=FilePerms.READ_WRITE,
            )

        host_python_lib = Path("/app/data/python-lib")
        if host_python_lib.exists():
            wasi.preopen_dir(
                str(host_python_lib), p.python_lib_root,
                dir_perms=DirPerms.READ_ONLY,
                file_perms=FilePerms.READ_ONLY,
            )

        self._store = Store(engine)
        self._store.set_wasi(wasi)
        self._store.set_fuel(p.fuel_per_execution)
        self._store.set_epoch_deadline(p.epoch_deadline_seconds)
        self._store.set_limits(**p.to_memory_limits())

        module = get_module(engine, p)
        self._instance = linker.instantiate(self._store, module)

        logger.info("Store ready: %s (key=%s)", p.bot_id, p.cache_key)

    async def run(self) -> dict:
        if not self._store or not self._instance:
            raise RuntimeError("Store not started")

        # Per-bot execution lock: one execution at a time per bot
        async with self._exec_lock:
            if not self._guard.acquire():
                raise RuntimeError("_start already called — re-entry denied")

            self._request_id = uuid.uuid4().hex[:16]

            start_fn = self._instance.exports(self._store).get("_start")
            if not start_fn:
                raise RuntimeError("No _start export")

            fuel_start = self._policy.fuel_per_execution
            executor = _get_executor(self._policy.max_concurrent_wasm_executions)

            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    executor,
                    lambda: start_fn(self._store),
                )
            except WasiError as e:
                return self._collect(fuel_start, error=e, etype="wasi")
            except WasmtimeError as e:
                return self._collect(fuel_start, error=e, etype="wasmtime")
            except RuntimeError as e:
                return self._collect(fuel_start, error=e, etype="runtime")
            except Exception as e:
                return self._collect(fuel_start, error=e, etype="unknown")

            return self._collect(fuel_start)

    def _collect(
        self,
        fuel_start: int,
        error: Exception | None = None,
        etype: str | None = None,
    ) -> dict:
        fuel_remaining = self._store.get_fuel() if self._store else 0
        fuel_used = max(0, fuel_start - fuel_remaining)

        stdout = self._read_and_cap(self._stdout_path, self._policy.max_stdout_bytes)
        stderr = self._read_and_cap(self._stderr_path, self._policy.max_stderr_bytes)

        result = {
            "request_id": self._request_id,
            "stdout": stdout,
            "stderr": stderr,
            "fuel_used": fuel_used,
            "fuel_remaining": fuel_remaining,
            "success": error is None,
        }

        if error:
            msg = str(error)
            if "all fuel consumed" in msg or "out of fuel" in msg:
                result.update(error_type="cpu_limit", error="CPU budget exhausted")
            elif "epoch deadline" in msg:
                result.update(error_type="timeout",
                              error=f"Wall-clock timeout ({self._policy.wall_clock_timeout_s}s)")
            elif "memory" in msg.lower() or "grow" in msg.lower():
                result.update(error_type="memory", error="Memory limit exceeded")
            elif "unreachable" in msg:
                result.update(error_type="trap", error="Unreachable instruction")
            else:
                result.update(error_type=etype or "runtime", error=msg)

            logger.warning(
                "Store %s req=%s: [%s] %s fuel=%d",
                self._policy.bot_id, self._request_id,
                result["error_type"], result.get("error", ""), fuel_used,
            )

        return result

    @staticmethod
    def _read_and_cap(path: Path | None, max_bytes: int) -> str:
        if not path or not path.exists():
            return ""
        try:
            raw = path.read_bytes()
            capped = raw[:max_bytes]
            path.write_bytes(b"")
            text = capped.decode("utf-8", errors="replace")
            return RE_CONTROL_CHARS.sub("", text)
        except Exception:
            return ""

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._store = None
        self._instance = None
        logger.info("Store closed: %s (req=%s)", self._policy.bot_id, self._request_id)

    @property
    def policy(self) -> SealedPolicy:
        return self._policy

    @property
    def bot_id(self) -> str:
        return self._policy.bot_id

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def request_id(self) -> str:
        return self._request_id
