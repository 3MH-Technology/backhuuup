import re
import shutil
import unicodedata
import zipfile
from datetime import datetime, timedelta, timezone
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
from services.captcha import CaptchaService

router = APIRouter(prefix="/api/bots", tags=["Bot Management"])

ALLOWED_UPLOAD_EXTS = {".py", ".php", ".zip"}
MAX_FILE_SIZE = 5 * 1024 * 1024

BOT_LIFETIME_DAYS = 4


class CreateBotCode(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    bot_type: str = Field(..., pattern="^(python|php)$")
    main_file: str = Field(..., min_length=1)
    requirements: str = ""


class UpdateCodeRequest(BaseModel):
    main_file: str
    requirements: str = ""


class RenewRequest(BaseModel):
    captcha_id: str
    answer: str


def _slugify(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^\w\s\-]", "", name).strip().lower()
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-")[:60] or "bot"


async def _unique_slug(session: AsyncSession, base_slug: str, user_id: int) -> str:
    slug = base_slug
    counter = 1
    while True:
        existing = (await session.execute(
            select(Bot).where(Bot.slug == slug, Bot.user_id == user_id)
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
            if ".." in fname or fname.startswith("/"):
                raise HTTPException(status_code=400, detail=f"不安全路径 في الملف المضغوط: {fname}")
            ext = Path(fname).suffix.lower()
            if ext not in ALLOWED_UPLOAD_EXTS and ext != ".txt":
                raise HTTPException(status_code=400, detail=f"امتداد غير مسموح: {fname}")
            if info.file_size > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail=f"ملف كبير جداً: {fname}")
            extracted.append(fname)
    return extracted


def _is_expired(bot: Bot) -> bool:
    if bot.expires_at is None:
        return False
    return datetime.now(timezone.utc) > bot.expires_at


def _bot_to_dict(bot: Bot) -> dict:
    usage = ContainerManager.get_resource_usage(bot.container_id) if bot.container_id else {"cpu": 0, "memory_mb": 0}
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
        "webhook_url": bot.webhook_url or f"https://{settings.domain}/api/webhook/",
        "webhook_active": bot.webhook_active or False,
        "restart_count": bot.restart_count,
        "created_at": bot.created_at.isoformat() if bot.created_at else None,
        "expires_at": bot.expires_at.isoformat() if bot.expires_at else None,
        "expired": _is_expired(bot),
        "resource_usage": usage,
    }


@router.get("/")
async def list_bots(
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
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "expires_at": b.expires_at.isoformat() if b.expires_at else None,
            "expired": _is_expired(b),
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
    count_result = await session.execute(
        select(func.count()).select_from(Bot).where(Bot.user_id == user.id)
    )
    if count_result.scalar() >= settings.max_bots_per_user:
        raise HTTPException(
            status_code=429,
            detail=f"الحد الأقصى {settings.max_bots_per_user} بوتات لكل مستخدم",
        )

    base_slug = _slugify(req.name)
    slug = await _unique_slug(session, base_slug, user.id)

    bot = Bot(
        user_id=user.id,
        name=req.name.strip(),
        slug=slug,
        bot_type=req.bot_type,
        main_file=req.main_file,
        requirements=req.requirements if req.bot_type == "python" else "",
        is_upload=False,
        expires_at=datetime.now(timezone.utc) + timedelta(days=BOT_LIFETIME_DAYS),
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
        "expires_at": bot.expires_at.isoformat() if bot.expires_at else None,
        "webhook_url": f"https://{settings.domain}/api/webhook/",
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
    count_result = await session.execute(
        select(func.count()).select_from(Bot).where(Bot.user_id == user.id)
    )
    if count_result.scalar() >= settings.max_bots_per_user:
        raise HTTPException(
            status_code=429,
            detail=f"الحد الأقصى {settings.max_bots_per_user} بوتات لكل مستخدم",
        )

    if not _allowed_file(file.filename):
        raise HTTPException(status_code=400, detail="الامتداد غير مسموح. فقط .py, .php, .zip")

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE * 2:
        raise HTTPException(status_code=400, detail="حجم الملف كبير جداً")

    base_slug = _slugify(name)
    slug = await _unique_slug(session, base_slug, user.id)
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
            expires_at=datetime.now(timezone.utc) + timedelta(days=BOT_LIFETIME_DAYS),
        )
    else:
        bot_file_path = work_dir / slug / file.filename
        bot_file_path.parent.mkdir(parents=True, exist_ok=True)
        bot_file_path.write_bytes(contents)
        bot = Bot(
            user_id=user.id,
            name=name.strip(),
            slug=slug,
            bot_type=bot_type,
            is_upload=True,
            upload_path=str(bot_file_path),
            expires_at=datetime.now(timezone.utc) + timedelta(days=BOT_LIFETIME_DAYS),
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
        "expires_at": bot.expires_at.isoformat() if bot.expires_at else None,
        "webhook_url": f"https://{settings.domain}/api/webhook/",
    }


@router.get("/{bot_id}")
async def get_bot(
    bot_id: int,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == user.id)
    )
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    return _bot_to_dict(bot)


@router.post("/{bot_id}/start")
async def start_bot(
    bot_id: int,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == user.id)
    )
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")

    if _is_expired(bot):
        bot.expires_at = datetime.now(timezone.utc) + timedelta(days=BOT_LIFETIME_DAYS)

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
async def stop_bot(
    bot_id: int,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == user.id)
    )
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")

    outcome = await ContainerManager.stop_bot(bot.container_id)
    if outcome["status"] == "success":
        bot.status = "stopped"
        bot.container_id = None
        await session.commit()

    return outcome


@router.post("/{bot_id}/restart")
async def restart_bot(
    bot_id: int,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == user.id)
    )
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")

    if _is_expired(bot):
        bot.expires_at = datetime.now(timezone.utc) + timedelta(days=BOT_LIFETIME_DAYS)

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
async def update_code(
    bot_id: int,
    req: UpdateCodeRequest,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == user.id)
    )
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")

    bot.main_file = req.main_file
    bot.requirements = req.requirements
    await session.commit()
    return {"status": "success", "message": "تم تحديث الكود — أعد تشغيل البوت لتطبيق التغييرات"}


@router.put("/{bot_id}/webhook")
async def update_webhook(
    bot_id: int,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == user.id)
    )
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")

    if _is_expired(bot):
        bot.expires_at = datetime.now(timezone.utc) + timedelta(days=BOT_LIFETIME_DAYS)

    bot.webhook_active = not bot.webhook_active
    bot.webhook_url = f"/api/webhook/" if bot.webhook_active else None
    await session.commit()
    return {"status": "success", "webhook_active": bot.webhook_active, "webhook_url": bot.webhook_url}


@router.delete("/{bot_id}")
async def delete_bot(
    bot_id: int,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == user.id)
    )
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")

    if bot.container_id:
        await ContainerManager.stop_bot(bot.container_id)
    await session.delete(bot)
    await session.commit()
    return {"status": "success", "message": "تم حذف البوت"}


@router.get("/{bot_id}/captcha")
async def get_captcha(bot_id: int, user: User = Depends(AuthService.get_current_user)):
    return CaptchaService.generate()


@router.post("/{bot_id}/renew")
@limiter.limit("2/hour")
async def renew_bot(
    request: Request,
    bot_id: int,
    req: RenewRequest,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not CaptchaService.verify(req.captcha_id, req.answer):
        raise HTTPException(status_code=400, detail="إجابة الكابتشا خاطئة")

    result = await session.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == user.id)
    )
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")

    bot.expires_at = datetime.now(timezone.utc) + timedelta(days=BOT_LIFETIME_DAYS)
    await session.commit()

    return {"status": "success", "expires_at": bot.expires_at.isoformat(), "message": "تم تجديد البوت لمدة 4 أيام"}
