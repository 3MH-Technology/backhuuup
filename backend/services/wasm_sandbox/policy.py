import hashlib
import json
import os
import re as _re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from urllib.parse import urlparse

PYTHON_WASM_EXPECTED_SHA256 = os.environ.get(
    "PYTHON_WASM_SHA256",
    os.environ.get("_PYTHON_WASM_SHA256_PLACEHOLDER", ""),
)
PYTHON_WASM_URL = os.environ.get(
    "PYTHON_WASM_URL",
    "https://github.com/brettcannon/cpython-wasi-build/releases/"
    "download/v3.13.2/cpython-wasi-python3.13-wasm32-wasi.tar.gz",
)
PYTHON_WASM_PATH = Path("/app/data/python-lib/python.wasm")
BOTS_ROOT = Path("/app/data/bots")
WASMTIME_RUNTIME_VERSION = os.environ.get("WASMTIME_VERSION", "28.0.0")

RE_CONTROL_CHARS = _re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
RE_ANSI_ESCAPES = _re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
RE_SECRET_PATTERNS = (
    _re.compile(r'\b(?:[A-Za-z0-9_]{32,})\b'),
    _re.compile(r'\bbot\d{8,10}:[A-Za-z0-9_-]{35,}\b'),
)


@dataclass(frozen=True)
class SealedPolicy:
    """Immutable, cryptographically signed per-bot policy object.

    The policy hash is computed from all fields and used as part of the
    WASM module cache key. No bot may execute unless its policy is sealed
    (immutable and hash-verified).
    """
    bot_id: str
    user_id: int

    # Resource limits
    fuel_per_execution: int = 500_000_000
    wall_clock_timeout_s: int = 30
    epoch_deadline_seconds: int = 30
    max_linear_memory_bytes: int = 64 * 1024 * 1024
    max_wasm_stack_bytes: int = 512 * 1024
    max_table_elements: int = 10_000
    max_instances: int = 10
    max_tables: int = 10
    max_memories: int = 5

    # Disk limits
    max_bot_disk_bytes: int = 10 * 1024 * 1024
    max_bot_files: int = 1000
    max_upload_bytes: int = 5 * 1024 * 1024
    max_source_bytes: int = 500 * 1024

    # Output limits
    max_response_bytes: int = 1 * 1024 * 1024
    max_stdout_bytes: int = 100 * 1024
    max_stderr_bytes: int = 100 * 1024

    # Filesystem roots
    code_root: str = "/app/code"
    state_root: str = "/app/state"
    tmp_root: str = "/app/tmp"
    python_lib_root: str = "/usr/local/lib"

    # Network policy
    allowed_domains: frozenset = field(
        default_factory=lambda: frozenset({"api.telegram.org"})
    )
    allowed_network: bool = True
    network_rate_per_minute: int = 30
    network_burst: int = 10
    network_daily_quota: int = 1000

    # Code validation
    max_ast_nesting_depth: int = 50

    # Global limits
    max_stores_per_user: int = 3
    max_total_stores: int = 30
    max_concurrent_wasm_executions: int = 3

    # Host-function allowlist (empty means no custom host functions)
    allowed_host_functions: tuple = field(default_factory=tuple)

    # ── Computed (set after construction) ──
    policy_hash: str = ""

    def __post_init__(self):
        if not self.policy_hash:
            canonical = json.dumps(
                {k: v for k, v in sorted(asdict(self).items())
                 if k != "policy_hash"},
                sort_keys=True, default=str,
            )
            h = hashlib.sha256(canonical.encode()).hexdigest()
            object.__setattr__(self, "policy_hash", h)

    @property
    def cache_key(self) -> tuple:
        return (PYTHON_WASM_EXPECTED_SHA256, self.policy_hash, WASMTIME_RUNTIME_VERSION)

    def to_memory_limits(self) -> dict:
        return {
            "memory_size": self.max_linear_memory_bytes,
            "table_elements": self.max_table_elements,
            "instances": self.max_instances,
            "tables": self.max_tables,
            "memories": self.max_memories,
        }


@dataclass(frozen=True)
class NetworkPolicy:
    allowed_hostnames: frozenset = field(
        default_factory=lambda: frozenset({"api.telegram.org"})
    )
    allowed_path_prefixes: tuple = ("/bot",)
    allowed_schemes: frozenset = field(
        default_factory=lambda: frozenset({"https"})
    )
    rate_per_minute: int = 30
    rate_burst: int = 10
    quota_per_day: int = 1000
    request_timeout_s: int = 10
    max_request_body_bytes: int = 1 * 1024 * 1024
    max_response_body_bytes: int = 5 * 1024 * 1024
    allowed_content_type_prefixes: tuple = (
        "text/", "application/json",
        "application/x-www-form-urlencoded",
    )

    def allows(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if parsed.scheme not in self.allowed_schemes:
            return False
        if parsed.hostname not in self.allowed_hostnames:
            return False
        if parsed.hostname is None:
            return False
        if _is_ip_literal(parsed.hostname):
            return False
        if parsed.port is not None and parsed.port != 443:
            return False
        if not parsed.path.startswith("/bot"):
            return False
        if len(parsed.path) > 200:
            return False
        return True

    def allows_redirect(self, url: str) -> bool:
        """Separate check for redirect targets (no /bot* restriction)."""
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if parsed.scheme not in self.allowed_schemes:
            return False
        if parsed.hostname not in self.allowed_hostnames:
            return False
        if _is_ip_literal(parsed.hostname):
            return False
        return True

    def sanitize_url(self, url: str) -> str:
        parsed = urlparse(url)
        if "/bot" in parsed.path:
            parts = parsed.path.split("/bot", 1)
            token_part = parts[1].split("/", 1)[0] if "/" in parts[1] else parts[1]
            redacted = parts[0] + "/bot" + token_part[:6] + "..."
            if "/" in parts[1]:
                redacted += "/" + "/".join(parts[1].split("/")[1:])
            return f"{parsed.scheme}://{parsed.hostname}{redacted}"
        return url


def _is_ip_literal(host: str) -> bool:
    import re as _re
    ipv4 = _re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host)
    ipv6 = host.startswith("[") and host.endswith("]")
    return bool(ipv4) or ipv6


def sanitize_log(msg: str) -> str:
    """Strip control chars, ANSI escapes, and potential secrets from log messages."""
    msg = RE_CONTROL_CHARS.sub("", msg)
    msg = RE_ANSI_ESCAPES.sub("", msg)
    for pat in RE_SECRET_PATTERNS:
        msg = pat.sub("<REDACTED>", msg)
    return msg[:2000]
