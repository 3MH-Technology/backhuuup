"""Per-bot filesystem isolation.

SECURITY INVARIANTS:
  1. Every bot has its own root at /app/data/bots/{user}/{bot}/.
  2. All paths are canonicalised via Path.resolve() and checked against the root.
  3. WASI preview1 does NOT expose symlink() syscall — symlink attacks
     require a host-side compromise, which this check defends against.
  4. Disk quota is enforced at the end of each write operation.
  5. File count quota prevents inode exhaustion across many bots.
  6. Temporary files use deterministic paths under bot_root/tmp/,
     never global temp directories.
"""
import logging
import shutil
from pathlib import Path

from .policy import BOTS_ROOT, PYTHON_WASM_PATH, RE_CONTROL_CHARS, SealedPolicy

logger = logging.getLogger("wolfhost.wasm.storage")

ALLOWED_CODE_EXTS = frozenset({
    ".py", ".php", ".txt", ".html", ".css", ".js",
    ".json", ".xml", ".md", ".htaccess",
})


def ensure_dirs():
    BOTS_ROOT.mkdir(parents=True, exist_ok=True)
    PYTHON_WASM_PATH.parent.mkdir(parents=True, exist_ok=True)


def bot_root(user_id: int, bot_id: int) -> Path:
    return BOTS_ROOT / str(user_id) / str(bot_id)


def ensure_bot_dirs(user_id: int, bot_id: int) -> dict[str, Path]:
    base = bot_root(user_id, bot_id)
    dirs = {
        "code": base / "code",
        "state": base / "state",
        "tmp": base / "tmp",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def resolve_safe(root: Path, filename: str) -> Path | None:
    """Resolve filename relative to root. Returns None on escape."""
    if ".." in filename:
        return None
    if filename.startswith("/") or filename.startswith("\\"):
        return None
    candidate = (root / filename).resolve()
    if not str(candidate).startswith(str(root.resolve())):
        return None
    return candidate


def write_bot_code(user_id: int, bot_id: int, filename: str, content: str):
    """Write a single source file. Raises ValueError on path escape."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_CODE_EXTS:
        raise ValueError(f"Extension not allowed: {filename}")

    code_dir = ensure_bot_dirs(user_id, bot_id)["code"]
    resolved = resolve_safe(code_dir, filename)
    if resolved is None:
        raise ValueError(f"Path escape: {filename}")

    resolved.parent.mkdir(parents=True, exist_ok=True)
    content = RE_CONTROL_CHARS.sub("", content)
    resolved.write_text(content, encoding="utf-8")
    resolved.chmod(0o444)
    logger.info("Wrote %s (%d B) for bot %d/%d", filename, len(content), user_id, bot_id)


def write_bot_files(user_id: int, bot_id: int, files: dict[str, str | bytes]):
    """Write multiple files. Silently skips unsafe filenames."""
    code_dir = ensure_bot_dirs(user_id, bot_id)["code"]
    written = 0
    for fn, content in files.items():
        ext = Path(fn).suffix.lower()
        if ext not in ALLOWED_CODE_EXTS and ext != ".zip":
            logger.warning("Skipping %s: extension not allowed", fn)
            continue
        resolved = resolve_safe(code_dir, fn)
        if resolved is None:
            logger.warning("Skipping %s: path escape", fn)
            continue
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            content = RE_CONTROL_CHARS.sub("", content)
            resolved.write_text(content, encoding="utf-8")
        else:
            resolved.write_bytes(content)
        resolved.chmod(0o444)
        written += 1
    logger.info("Wrote %d/%d files for bot %d/%d", written, len(files), user_id, bot_id)


def check_disk_quota(user_id: int, bot_id: int, policy: SealedPolicy | None = None) -> bool:
    """Return True if bot is under disk quota."""
    if policy is None:
        policy = SealedPolicy(bot_id="__quota_check__", user_id=0)
    root = bot_root(user_id, bot_id)
    if not root.exists():
        return True

    files = list(root.rglob("*"))
    if len(files) > policy.max_bot_files:
        logger.warning("Bot %d/%d file count %d > %d", user_id, bot_id,
                       len(files), policy.max_bot_files)
        return False

    total = sum(f.stat().st_size for f in files if f.is_file())
    if total > policy.max_bot_disk_bytes:
        logger.warning("Bot %d/%d disk %d > %d bytes", user_id, bot_id,
                       total, policy.max_bot_disk_bytes)
        return False

    return True


def estimate_usage(user_id: int, bot_id: int) -> tuple[int, int]:
    """Return (file_count, total_bytes)."""
    root = bot_root(user_id, bot_id)
    if not root.exists():
        return (0, 0)
    files = list(root.rglob("*"))
    fcount = len(files)
    total = sum(f.stat().st_size for f in files if f.is_file())
    return (fcount, total)


def cleanup(user_id: int, bot_id: int):
    """Remove entire bot directory tree."""
    root = bot_root(user_id, bot_id)
    if root.exists():
        shutil.rmtree(str(root))
        logger.info("Cleaned bot %d/%d", user_id, bot_id)
