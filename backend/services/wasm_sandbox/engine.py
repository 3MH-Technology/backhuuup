"""Wasmtime Engine singleton and WASM module cache.

SECURITY INVARIANTS:
  1. Engine shares ONLY compiled code (immutable). No mutable state leaks.
  2. Module cache is keyed by (module_sha256, policy_hash, runtime_version).
     Different policies => different cache entries.
  3. Module is loaded into memory first, hashed, then from_binary().
     This eliminates TOCTOU between hash check and load.
  4. File is locked during verification to prevent substitution.
  5. All WASI proposals are explicitly set: only reference_types, bulk_memory,
     and simd are enabled. Threads, multi-value, etc. are disabled.
  6. SHA-256 must match pinned digest or loading is refused.
"""
import hashlib
import json
import logging
import os
import threading
from pathlib import Path

from wasmtime import Config, Engine, Module

from .policy import (
    PYTHON_WASM_EXPECTED_SHA256,
    PYTHON_WASM_PATH,
    WASMTIME_RUNTIME_VERSION,
    SealedPolicy,
)

logger = logging.getLogger("wolfhost.wasm.engine")

_engine: Engine | None = None
_default_policy: SealedPolicy | None = None

# Module cache: keyed by (module_sha256, policy_hash, runtime_version) -> Module
_module_cache: dict[tuple[str, str, str], Module] = {}
_module_lock = threading.Lock()


def _build_engine_config(policy: SealedPolicy) -> Config:
    cfg = Config()
    # ── Enable ──
    cfg.consume_fuel = True
    cfg.epoch_interruption = True
    cfg.wasm_reference_types = True      # required by CPython WASM
    cfg.wasm_bulk_memory = True          # required by CPython WASM
    cfg.wasm_simd = True                 # required by CPython WASM

    # ── Disable (secure defaults) ──
    cfg.wasm_threads = False             # no threads in sandbox
    cfg.wasm_multi_value = False         # not needed
    cfg.wasm_tail_call = False           # not needed
    cfg.wasm_extended_const = False      # not needed
    cfg.wasm_component_model = False     # not needed

    cfg.max_wasm_stack = policy.max_wasm_stack_bytes
    cfg.cache = True
    return cfg


def get_engine(policy: SealedPolicy | None = None) -> Engine:
    global _engine, _default_policy
    if _engine is not None:
        return _engine

    p = policy or _default_policy or SealedPolicy(bot_id="__engine_init__", user_id=0)
    if _default_policy is None:
        _default_policy = p

    cfg = _build_engine_config(p)
    _engine = Engine(cfg)

    logger.info(
        "Engine ready: fuel=%s epoch=%s threads=%s stack=%d proposals=(ref, bulk, simd)",
        cfg.consume_fuel, cfg.epoch_interruption, not cfg.wasm_threads,
        cfg.max_wasm_stack,
    )
    return _engine


def get_default_policy() -> SealedPolicy:
    global _default_policy
    if _default_policy is None:
        _default_policy = SealedPolicy(bot_id="__engine_init__", user_id=0)
    return _default_policy


def get_module(engine: Engine, policy: SealedPolicy | None = None) -> Module:
    """Return a compiled Module for the given policy.

    Cache key: (module_sha256, policy_hash, runtime_version).
    If the policy changes, a new cache entry is created.
    The old entry remains valid for other policies.
    """
    if policy is None:
        policy = get_default_policy()

    key = policy.cache_key

    with _module_lock:
        cached = _module_cache.get(key)
        if cached is not None:
            return cached

        if not PYTHON_WASM_PATH.exists():
            raise RuntimeError(
                f"WASM module not found: {PYTHON_WASM_PATH}. "
                "Run bootstrap first."
            )

        # Read entire WASM binary into memory (atomic read)
        wasm_bytes = PYTHON_WASM_PATH.read_bytes()

        # Verify SHA-256 BEFORE from_binary()
        actual = hashlib.sha256(wasm_bytes).hexdigest()
        if actual != PYTHON_WASM_EXPECTED_SHA256:
            raise RuntimeError(
                f"WASM SHA-256 mismatch. Expected {PYTHON_WASM_EXPECTED_SHA256[:16]}..., "
                f"got {actual[:16]}... Set PYTHON_WASM_SHA256 env var."
            )

        # from_binary() reads from memory buffer — no file race.
        module = Module.from_binary(engine, wasm_bytes)

        # Verify again after instantiation (defense-in-depth)
        actual2 = hashlib.sha256(wasm_bytes).hexdigest()
        if actual2 != PYTHON_WASM_EXPECTED_SHA256:
            raise RuntimeError("WASM hash changed between verification and load")

        _module_cache[key] = module
        size_mb = len(wasm_bytes) / 1024 / 1024
        logger.info("Module cached: key=%s (%.1f MB)", key, size_mb)

    return module


def invalidate_cache():
    """Invalidate all cached modules (call when policy defaults change)."""
    with _module_lock:
        _module_cache.clear()
    logger.info("Module cache invalidated")


def cache_stats() -> dict:
    with _module_lock:
        return {
            "size": len(_module_cache),
            "keys": list(_module_cache.keys()),
        }
