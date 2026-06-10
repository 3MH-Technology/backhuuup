import os
import re
import secrets
import shutil
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from config import settings
from models.database import get_session
from models.bot import Bot
from models.user import User
from services.auth_service import AuthService
from services.container_manager import ContainerManager, BOTS_DIR
from services.limiter import limiter
from services.webhook_crypto import encrypt_token, decrypt_token, hash_token

router = APIRouter(prefix="/api/bots", tags=["Bot Management"])

ALLOWED_UPLOAD_EXTS = {".py", ".php", ".zip", ".html", ".css", ".js", ".json", ".xml", ".md", ".htaccess", ".env.example"}
MAX_FILE_SIZE = 5 * 1024 * 1024

class CreateBotCode(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, pattern=r"^[^<>\"'&]+$")
    bot_type: str = Field(..., pattern="^(python|php|static)$")
    main_file: str = Field(..., min_length=1)
    requirements: str = ""


class UpdateCodeRequest(BaseModel):
    main_file: str
    requirements: str = ""




def _slugify(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^\w\s\-]", "", name).strip().lower()
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-")[:60] or "bot"


async def _unique_slug(session: AsyncSession, base_slug: str) -> str:
    slug = base_slug
    counter = 1
    while True:
        existing = (await session.execute(
            select(Bot).where(Bot.slug == slug)
        )).scalar_one_or_none()
        if not existing:
            return slug
        slug = f"{base_slug}-{counter}"
        counter += 1


def _allowed_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_UPLOAD_EXTS


def _sanitize_zip(zip_path: Path) -> list[str]:
    extracted = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            fname = info.filename
            normalized = Path(fname).as_posix()
            if ".." in normalized or normalized.startswith("/") or normalized.startswith("\\"):
                raise HTTPException(status_code=400, detail=f"مسار غير آمن في الملف المضغوط: {fname}")
            if info.is_symlink():
                raise HTTPException(status_code=400, detail=f"روابط رمزية غير مسموحة: {fname}")
            ext = Path(fname).suffix.lower()
            if ext not in ALLOWED_UPLOAD_EXTS and ext != ".txt":
                raise HTTPException(status_code=400, detail=f"امتداد غير مسموح: {fname}")
            if info.file_size > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail=f"ملف كبير جداً: {fname}")
            extracted.append(fname)
    return extracted


def _mask_token(encrypted_token: str | None) -> str | None:
    """Decrypt an encrypted webhook token and return a masked preview."""
    if not encrypted_token:
        return None
    plaintext = decrypt_token(encrypted_token)
    if not plaintext:
        return "••••••••"
    if len(plaintext) < 8:
        return plaintext
    return plaintext[:4] + "…" + plaintext[-4:]


def _bot_to_dict(bot: Bot) -> dict:
    usage = ContainerManager.get_resource_usage(bot.container_id) if bot.container_id else {"cpu": 0, "memory_mb": 0}
    # Build webhook URL from decrypted token for display
    plaintext_token = decrypt_token(bot.webhook_token) if bot.webhook_token else None
    webhook_url = f"https://{settings.domain}/api/webhook/{plaintext_token}" if bot.webhook_active and plaintext_token else None
    return {
        "id": bot.id,
        "name": bot.name,
        "slug": bot.slug,
        "bot_type": bot.bot_type,
        "status": ContainerManager.get_status(bot.container_id) if bot.container_id else bot.status,
        "container_id": bot.container_id,
        "main_file": bot.main_file,
        "requirements": bot.requirements,
        "is_upload": bot.is_upload or False,
        "upload_path": bot.upload_path,
        "webhook_token": _mask_token(bot.webhook_token),
        "webhook_url": webhook_url,
        "webhook_active": bot.webhook_active or False,
        "restart_count": bot.restart_count,
        "created_at": bot.created_at.isoformat() if bot.created_at else None,
        "resource_usage": usage,
    }


async def _get_user_bot(bot_id: int, user: User, session: AsyncSession) -> Bot:
    is_admin = getattr(user, "is_admin", False)
    if is_admin:
        result = await session.execute(select(Bot).where(Bot.id == bot_id))
    else:
        result = await session.execute(select(Bot).where(Bot.id == bot_id, Bot.user_id == user.id))
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    return bot


@router.get("/")
@limiter.limit("30/minute")
async def list_bots(
    request: Request,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Bot).where(Bot.user_id == user.id).order_by(Bot.created_at.desc())
    )
    bots = result.scalars().all()
    return [
        {
            "id": b.id,
            "name": b.name,
            "slug": b.slug,
            "bot_type": b.bot_type,
            "status": ContainerManager.get_status(b.container_id) if b.container_id else b.status,
            "is_upload": b.is_upload or False,
            "webhook_active": b.webhook_active or False,
            "webhook_token": _mask_token(b.webhook_token),
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in bots
    ]


@router.post("/code")
@limiter.limit("5/hour")
async def create_bot_code(
    request: Request,
    req: CreateBotCode,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    is_admin = getattr(user, "is_admin", False)
    if not is_admin:
        count_result = await session.execute(
            select(func.count()).select_from(Bot).where(Bot.user_id == user.id)
        )
        if count_result.scalar() >= settings.max_bots_per_user:
            raise HTTPException(
                status_code=429,
                detail=f"الحد الأقصى {settings.max_bots_per_user} بوتات لكل مستخدم",
            )

    base_slug = _slugify(req.name)
    slug = await _unique_slug(session, base_slug)

    bot = Bot(
        user_id=user.id,
        name=req.name.strip(),
        slug=slug,
        bot_type=req.bot_type,
        main_file=req.main_file,
        requirements=req.requirements if req.bot_type == "python" else "",
        is_upload=False,
    )
    session.add(bot)
    await session.commit()
    await session.refresh(bot)
    return {
        "id": bot.id,
        "name": bot.name,
        "slug": bot.slug,
        "bot_type": bot.bot_type,
        "status": "created",
        "webhook_url": None,
    }


@router.post("/upload")
@limiter.limit("5/hour")
async def create_bot_upload(
    request: Request,
    name: str = Form(...),
    bot_type: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    is_admin = getattr(user, "is_admin", False)
    if not is_admin:
        count_result = await session.execute(
            select(func.count()).select_from(Bot).where(Bot.user_id == user.id)
        )
        if count_result.scalar() >= settings.max_bots_per_user:
            raise HTTPException(
                status_code=429,
                detail=f"الحد الأقصى {settings.max_bots_per_user} بوتات لكل مستخدم",
            )

    if any(c in name for c in "<>\"'&"):
        raise HTTPException(status_code=400, detail="اسم البوت يحتوي على أحرف غير مسموحة")

    if not _allowed_file(file.filename):
        raise HTTPException(status_code=400, detail="الامتداد غير مسموح. الامتدادات المسموحة: .py, .php, .zip, .html, .css, .js, .json, .xml, .md, .htaccess, .env.example")

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE * 2:
        raise HTTPException(status_code=400, detail="حجم الملف كبير جداً")

    base_slug = _slugify(name)
    slug = await _unique_slug(session, base_slug)
    work_dir = BOTS_DIR / str(user.id)

    ext = Path(file.filename).suffix.lower()

    if ext == ".zip":
        extract_dir = work_dir / slug
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)
        zip_path = work_dir / f"{slug}_upload.zip"
        zip_path.write_bytes(contents)
        try:
            extracted = _sanitize_zip(zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                for fname in extracted:
                    zf.extract(fname, extract_dir)
        finally:
            zip_path.unlink(missing_ok=True)
        bot = Bot(
            user_id=user.id,
            name=name.strip(),
            slug=slug,
            bot_type=bot_type,
            is_upload=True,
            upload_path=str(extract_dir),
        )
    else:
        safe_filename = Path(file.filename).name
        if ".." in safe_filename or "/" in safe_filename or "\\" in safe_filename:
            raise HTTPException(status_code=400, detail="اسم ملف غير صالح")
        bot_file_path = work_dir / slug / safe_filename
        bot_file_path.parent.mkdir(parents=True, exist_ok=True)
        bot_file_path.write_bytes(contents)
        bot = Bot(
            user_id=user.id,
            name=name.strip(),
            slug=slug,
            bot_type=bot_type,
            is_upload=True,
            upload_path=str(bot_file_path),
        )

    session.add(bot)
    await session.commit()
    await session.refresh(bot)
    return {
        "id": bot.id,
        "name": bot.name,
        "slug": bot.slug,
        "bot_type": bot.bot_type,
        "status": "created",
        "is_upload": True,
        "webhook_url": None,
    }


@router.get("/{bot_id}")
@limiter.limit("30/minute")
async def get_bot(
    request: Request,
    bot_id: int,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    bot = await _get_user_bot(bot_id, user, session)
    return _bot_to_dict(bot)


@router.post("/{bot_id}/start")
@limiter.limit("10/minute")
async def start_bot(
    request: Request,
    bot_id: int,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    bot = await _get_user_bot(bot_id, user, session)

    outcome = await ContainerManager.start_bot(
        bot_id=bot.id,
        user_id=user.id,
        slug=bot.slug,
        bot_type=bot.bot_type,
        main_file_content=bot.main_file or "",
        requirements=bot.requirements or "",
        is_upload=bot.is_upload or False,
    )

    if outcome["status"] == "success":
        bot.container_id = outcome["container_id"]
        bot.status = "running"
        bot.restart_count = 0
        await session.commit()

    return outcome


@router.post("/{bot_id}/stop")
@limiter.limit("10/minute")
async def stop_bot(
    request: Request,
    bot_id: int,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    bot = await _get_user_bot(bot_id, user, session)

    outcome = await ContainerManager.stop_bot(bot.container_id)
    if outcome["status"] == "success":
        bot.status = "stopped"
        bot.container_id = None
        await session.commit()

    return outcome


@router.post("/{bot_id}/restart")
@limiter.limit("10/minute")
async def restart_bot(
    request: Request,
    bot_id: int,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    bot = await _get_user_bot(bot_id, user, session)

    if bot.container_id:
        await ContainerManager.stop_bot(bot.container_id)

    outcome = await ContainerManager.start_bot(
        bot_id=bot.id,
        user_id=user.id,
        slug=bot.slug,
        bot_type=bot.bot_type,
        main_file_content=bot.main_file or "",
        requirements=bot.requirements or "",
        is_upload=bot.is_upload or False,
    )

    if outcome["status"] == "success":
        bot.container_id = outcome["container_id"]
        bot.status = "running"
        bot.restart_count = 0
        await session.commit()

    return outcome


@router.put("/{bot_id}/code")
@limiter.limit("10/minute")
async def update_code(
    request: Request,
    bot_id: int,
    req: UpdateCodeRequest,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    bot = await _get_user_bot(bot_id, user, session)

    bot.main_file = req.main_file
    bot.requirements = req.requirements
    await session.commit()
    return {"status": "success", "message": "تم تحديث الكود — أعد تشغيل البوت لتطبيق التغييرات"}


@router.put("/{bot_id}/webhook")
@limiter.limit("10/minute")
async def update_webhook(
    request: Request,
    bot_id: int,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    bot = await _get_user_bot(bot_id, user, session)

    bot.webhook_active = not bot.webhook_active
    if bot.webhook_active:
        if not bot.webhook_token:
            # Generate a new random token, store encrypted + hashed
            plaintext_token = secrets.token_hex(16)
            bot.webhook_token = encrypt_token(plaintext_token)
            bot.webhook_token_hash = hash_token(plaintext_token)
        # Decrypt for the URL display
        plaintext = decrypt_token(bot.webhook_token)
        bot.webhook_url = f"https://{settings.domain}/api/webhook/{plaintext}" if plaintext else None
    else:
        bot.webhook_url = None
    await session.commit()
    # Return plaintext token once for user to copy
    plaintext = decrypt_token(bot.webhook_token) if bot.webhook_active else None
    return {"status": "success", "webhook_active": bot.webhook_active, "webhook_url": bot.webhook_url, "webhook_token_full": plaintext}


@router.delete("/{bot_id}")
@limiter.limit("5/minute")
async def delete_bot(
    request: Request,
    bot_id: int,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    bot = await _get_user_bot(bot_id, user, session)

    if bot.container_id:
        await ContainerManager.stop_bot(bot.container_id)
    await session.delete(bot)
    await session.commit()
    return {"status": "success", "message": "تم حذف البوت"}



