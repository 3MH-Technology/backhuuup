"""Download, verify, and install the Python WASM module.

Called once at platform startup. Downloads from the configured URL,
verifies SHA-256, and atomically installs to PYTHON_WASM_PATH.
"""
import asyncio
import hashlib
import logging
import shutil
import tarfile
import tempfile
from pathlib import Path

import aiohttp

from .policy import PYTHON_WASM_EXPECTED_SHA256, PYTHON_WASM_PATH, PYTHON_WASM_URL

logger = logging.getLogger("wolfhost.wasm.bootstrap")


async def download_python_wasm(force: bool = False):
    PYTHON_WASM_PATH.parent.mkdir(parents=True, exist_ok=True)

    if PYTHON_WASM_PATH.exists() and not force:
        actual = hashlib.sha256(PYTHON_WASM_PATH.read_bytes()).hexdigest()
        if actual == PYTHON_WASM_EXPECTED_SHA256:
            size_mb = PYTHON_WASM_PATH.stat().st_size / 1024 / 1024
            logger.info("Already present and verified (%s, %.1f MB)",
                        actual[:16], size_mb)
            return
        logger.warning("SHA mismatch, re-downloading")

    tmp_dir = Path(tempfile.mkdtemp(prefix="wasm_bootstrap_"))
    tmp_tar = tmp_dir / "python-wasi.tar.gz"

    try:
        logger.info("Downloading from %s", PYTHON_WASM_URL)
        async with aiohttp.ClientSession() as session:
            async with session.get(PYTHON_WASM_URL) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                with open(tmp_tar, "wb") as f:
                    async for chunk in resp.content.iter_chunked(65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            logger.info("  %d / %d MB (%d%%)",
                                        downloaded // 1024 // 1024,
                                        total // 1024 // 1024,
                                        downloaded * 100 // total)

        with tarfile.open(tmp_tar, "r:gz") as tar:
            found = False
            for member in tar.getmembers():
                if member.name.endswith("python.wasm"):
                    tar.extract(member, path=str(tmp_dir))
                    extracted = tmp_dir / member.name
                    if extracted != PYTHON_WASM_PATH:
                        shutil.move(str(extracted), str(PYTHON_WASM_PATH))
                    found = True
                    break
            if not found:
                raise RuntimeError("python.wasm not found in archive")

        actual = hashlib.sha256(PYTHON_WASM_PATH.read_bytes()).hexdigest()
        if actual != PYTHON_WASM_EXPECTED_SHA256:
            PYTHON_WASM_PATH.unlink()
            raise RuntimeError(
                f"SHA mismatch. Expected {PYTHON_WASM_EXPECTED_SHA256[:16]}..., "
                f"got {actual[:16]}... Update PYTHON_WASM_SHA256 env var"
            )

        size_mb = PYTHON_WASM_PATH.stat().st_size / 1024 / 1024
        logger.info("Downloaded and verified (%s, %.1f MB)", actual[:16], size_mb)

    finally:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)
