import re
import unicodedata
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from config import settings
from models.database import get_session
from models.bot import Bot
from models.user import User
from services.auth_service import AuthService
from services.container_manager import ContainerManager

router = APIRouter(prefix="/api/bots", tags=["Bot Management"])


class CreateBotRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    bot_type: str = Field(..., pattern="^(python|php)$")
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


def _unique_slug(session: AsyncSession, base_slug: str, user_id: int) -> str:
    slug = base_slug
    counter = 1
    while True:
        existing = session.execute(
            select(Bot).where(Bot.slug == slug, Bot.user_id == user_id)
        ).scalar_one_or_none()
        if not existing:
            return slug
        slug = f"{base_slug}-{counter}"
        counter += 1


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
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in bots
    ]


@router.post("/")
async def create_bot(
    req: CreateBotRequest,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    count_result = await session.execute(
        select(func.count()).select_from(Bot).where(Bot.user_id == user.id)
    )
    if count_result.scalar() >= settings.max_bots_per_user:
        raise HTTPException(
            status_code=429,
            detail=f"Maximum {settings.max_bots_per_user} bots allowed on free tier",
        )

    base_slug = _slugify(req.name)
    slug = _unique_slug(session, base_slug, user.id)

    bot = Bot(
        user_id=user.id,
        name=req.name.strip(),
        slug=slug,
        bot_type=req.bot_type,
        main_file=req.main_file,
        requirements=req.requirements,
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
        "webhook_url": f"https://{bot.slug}.{settings.domain}/",
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
        "restart_count": bot.restart_count,
        "created_at": bot.created_at.isoformat() if bot.created_at else None,
        "resource_usage": usage,
        "webhook_url": f"https://{bot.slug}.{settings.domain}/" if bot.slug else None,
    }


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

    outcome = await ContainerManager.start_bot(
        bot_id=bot.id,
        user_id=user.id,
        slug=bot.slug,
        bot_type=bot.bot_type,
        main_file_content=bot.main_file or "",
        requirements=bot.requirements or "",
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

    if bot.container_id:
        await ContainerManager.stop_bot(bot.container_id)

    outcome = await ContainerManager.start_bot(
        bot_id=bot.id,
        user_id=user.id,
        slug=bot.slug,
        bot_type=bot.bot_type,
        main_file_content=bot.main_file or "",
        requirements=bot.requirements or "",
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
    return {"status": "success", "message": "Code updated — restart bot to apply changes"}


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
    return {"status": "success", "message": "Bot deleted"}
