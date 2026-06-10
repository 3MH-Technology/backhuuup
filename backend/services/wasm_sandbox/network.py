"""Central outbound network gateway.

SECURITY INVARIANTS:
  1. ALL outbound requests go through this gateway — no exceptions.
  2. URL validation uses urlparse() with exact hostname match.
     Subdomain attacks, IP literals, and scheme bypass are rejected.
  3. Redirects are checked against the allowlist separately (no /bot* path restriction).
     Redirects to non-approved hosts are rejected with an error.
  4. Each request gets a fresh TCP connection (no cross-bot connection pooling).
     DNS is resolved per-connection, not cached across bots.
  5. Rate limiting via TokenBucket per bot.
  6. Daily quota per bot.
  7. Audit logging with sanitized URLs (tokens redacted).
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import aiohttp

from .policy import NetworkPolicy, sanitize_log

logger = logging.getLogger("wolfhost.wasm.network")


class TokenBucket:
    __slots__ = ("_rate_per_min", "_burst", "_tokens", "_last_refill")

    def __init__(self, rate_per_minute: float, burst: int):
        self._rate_per_min = rate_per_minute
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()

    def try_consume(self, count: int = 1) -> bool:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            float(self._burst),
            self._tokens + elapsed * self._rate_per_min / 60.0,
        )
        self._last_refill = now
        if self._tokens >= count:
            self._tokens -= count
            return True
        return False


class BotNetState:
    __slots__ = ("_bucket", "_daily_count", "_daily_reset")

    def __init__(self, rate: int, burst: int, daily_quota: int):
        self._bucket = TokenBucket(float(rate), burst)
        self._daily_count = 0
        self._daily_reset = time.monotonic()
        self._quota = daily_quota

    def allow(self) -> bool:
        now = time.monotonic()
        if now - self._daily_reset > 86400:
            self._daily_count = 0
            self._daily_reset = now
        if self._daily_count >= self._quota:
            return False
        if not self._bucket.try_consume():
            return False
        self._daily_count += 1
        return True


class PolicyViolation(Exception):
    def __init__(self, reason: str, status_code: int = 403):
        self.reason = reason
        self.status_code = status_code
        super().__init__(sanitize_log(reason))


class NetworkGateway:
    def __init__(self, policy: NetworkPolicy | None = None):
        self._policy = policy or NetworkPolicy()
        self._states: dict[str, BotNetState] = {}
        self._lock = asyncio.Lock()

    async def request(
        self,
        bot_id: str,
        method: str,
        url: str,
        headers: dict | None = None,
        body: bytes | None = None,
    ) -> dict:
        # ── URL validation (exact hostname, no subdomain bypass) ──
        if not self._policy.allows(url):
            raise PolicyViolation(f"URL denied: {self._policy.sanitize_url(url)}")

        if body and len(body) > self._policy.max_request_body_bytes:
            raise PolicyViolation(f"Body > {self._policy.max_request_body_bytes // 1024} KB")

        # ── Rate limit + quota ──
        async with self._lock:
            state = self._states.get(bot_id)
            if not state:
                state = BotNetState(
                    self._policy.rate_per_minute,
                    self._policy.rate_burst,
                    self._policy.quota_per_day,
                )
                self._states[bot_id] = state
            if not state.allow():
                raise PolicyViolation("Rate/daily limit exceeded", 429)

        # ── Execute with per-request connector (no cross-bot DNS cache) ──
        fheaders = dict(headers or {})
        fheaders.setdefault("User-Agent", "WolfHost-Bot/1.0")

        connector = aiohttp.TCPConnector(
            force_close=True,            # fresh connection per request
            enable_cleanup_closed=True,
            ssl=True,
            limit=1,                     # 1 connection per connector
        )

        try:
            async with aiohttp.ClientSession(
                connector=connector,
                headers=fheaders,
                timeout=aiohttp.ClientTimeout(total=self._policy.request_timeout_s),
            ) as session:
                async with session.request(method, url, data=body) as resp:
                    # ── Redirect check ──
                    if resp.status in (301, 302, 303, 307, 308):
                        redirect_url = resp.headers.get("Location", "")
                        if not self._policy.allows_redirect(redirect_url):
                            raise PolicyViolation(
                                f"Redirect denied: {self._policy.sanitize_url(redirect_url)}"
                            )

                    raw = await resp.read()
                    if len(raw) > self._policy.max_response_body_bytes:
                        raise PolicyViolation(f"Response > {self._policy.max_response_body_bytes // 1024 // 1024} MB")

                    ct = (resp.content_type or "").lower()
                    if not any(ct.startswith(p) for p in self._policy.allowed_content_type_prefixes):
                        raise PolicyViolation(f"Content type denied: {ct}")

                    logger.info(
                        "net %s %s -> %d (body=%d)",
                        bot_id, self._policy.sanitize_url(url),
                        resp.status, len(raw),
                    )

                    return {
                        "status": resp.status,
                        "headers": dict(resp.headers),
                        "body": raw.decode("utf-8", errors="replace"),
                    }

        except asyncio.TimeoutError:
            raise PolicyViolation(f"Timeout ({self._policy.request_timeout_s}s)", 504)
        except aiohttp.ClientError as e:
            raise PolicyViolation(f"Network: {e}", 502)

    async def close(self):
        pass

    def release(self, bot_id: str):
        self._states.pop(bot_id, None)
