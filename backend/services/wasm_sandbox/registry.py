"""StoreRegistry — central supervisor with circuit breaker.

SECURITY INVARIANTS:
  1. Enforces per-user (max 3) and global (max 30) Store limits.
  2. Epoch timer ticks every 1s + random jitter (±100ms) to reduce timing precision.
  3. Dead stores are lazily pruned on each timer tick.
  4. Circuit breaker: bots that fail N times consecutively are quarantined
     for quarantine_seconds. During quarantine, start_bot returns an error.
  5. register() is the ONLY way a BotStore enters the active set.
"""
import asyncio
import logging
import random
import time

from .engine import get_engine, get_default_policy
from .policy import SealedPolicy

logger = logging.getLogger("wolfhost.wasm.registry")


class CircuitBreaker:
    """Tracks consecutive failures per bot. Quarantines repeat offenders."""

    def __init__(self, max_failures: int = 5, quarantine_seconds: int = 300):
        self._max = max_failures
        self._quarantine_s = quarantine_seconds
        self._failures: dict[str, int] = {}
        self._quarantine_until: dict[str, float] = {}

    def record_failure(self, bot_id: str):
        self._failures[bot_id] = self._failures.get(bot_id, 0) + 1
        if self._failures[bot_id] >= self._max:
            self._quarantine_until[bot_id] = time.monotonic() + self._quarantine_s
            logger.warning("Circuit breaker: %s quarantined for %ds",
                           bot_id, self._quarantine_s)

    def record_success(self, bot_id: str):
        self._failures.pop(bot_id, None)

    def is_quarantined(self, bot_id: str) -> bool:
        until = self._quarantine_until.get(bot_id, 0)
        if until == 0:
            return False
        if time.monotonic() > until:
            del self._quarantine_until[bot_id]
            self._failures.pop(bot_id, None)
            logger.info("Circuit breaker: %s released from quarantine", bot_id)
            return False
        return True


class StoreRegistry:
    _stores: dict[str, "BotStore"] = {}
    _epoch_task: asyncio.Task | None = None
    _lock = asyncio.Lock()
    _started = False
    _circuit_breaker = CircuitBreaker()
    _default_policy: SealedPolicy | None = None

    @classmethod
    async def register(cls, store: "BotStore") -> bool:
        limits = get_default_policy()

        async with cls._lock:
            bid = store.bot_id

            if cls._circuit_breaker.is_quarantined(bid):
                logger.warning("Store %s rejected: quarantined", bid)
                return False

            if bid in cls._stores and not cls._stores[bid].is_closed:
                return False

            if len(cls._stores) >= limits.max_total_stores:
                logger.warning("Global limit (%d) reached", limits.max_total_stores)
                return False

            uid = store.policy.user_id
            user_count = sum(
                1 for s in cls._stores.values()
                if s.policy.user_id == uid and not s.is_closed
            )
            if user_count >= limits.max_stores_per_user:
                logger.warning("User %d limit (%d) reached", uid, limits.max_stores_per_user)
                return False

            cls._stores[bid] = store
            return True

    @classmethod
    def record_failure(cls, bot_id: str):
        cls._circuit_breaker.record_failure(bot_id)

    @classmethod
    def record_success(cls, bot_id: str):
        cls._circuit_breaker.record_success(bot_id)

    @classmethod
    def is_quarantined(cls, bot_id: str) -> bool:
        return cls._circuit_breaker.is_quarantined(bot_id)

    @classmethod
    def get(cls, bot_id: str) -> "BotStore | None":
        return cls._stores.get(bot_id)

    @classmethod
    def remove(cls, bot_id: str):
        cls._stores.pop(bot_id, None)

    @classmethod
    def start_epoch_timer(cls):
        if cls._started:
            return
        cls._started = True

        engine = get_engine()

        async def _tick():
            while True:
                base = 1.0
                jitter = random.uniform(-0.1, 0.1)
                await asyncio.sleep(base + jitter)
                engine.increment_epoch()
                dead = [bid for bid, s in cls._stores.items() if s.is_closed]
                for bid in dead:
                    cls._stores.pop(bid, None)

        cls._epoch_task = asyncio.create_task(_tick())
        logger.info("Epoch timer started (1s ±100ms tick)")

    @classmethod
    async def close_all(cls):
        if cls._epoch_task:
            cls._epoch_task.cancel()
            cls._epoch_task = None
        for store in list(cls._stores.values()):
            store.close()
        cls._stores.clear()
        cls._started = False
        logger.info("All stores closed")

    @classmethod
    def active_count(cls) -> int:
        return sum(1 for s in cls._stores.values() if not s.is_closed)

    @classmethod
    def user_count(cls, user_id: int) -> int:
        return sum(
            1 for s in cls._stores.values()
            if s.policy.user_id == user_id and not s.is_closed
        )

    @classmethod
    def list_active(cls) -> list[str]:
        return [bid for bid, s in cls._stores.items() if not s.is_closed]
