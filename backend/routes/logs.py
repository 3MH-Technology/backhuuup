from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models.database import async_session, get_session
from models.bot import Bot
from models.user import User
from services.auth_service import AuthService
from services.container_manager import ContainerManager
from services.limiter import limiter
from services.log_streamer import LogStreamer

router = APIRouter(prefix="/api/logs", tags=["Logs"])


@router.get("/{bot_id}")
@limiter.limit("30/minute")
async def get_logs(
    request: Request,
    bot_id: int,
    lines: int = Query(default=50, le=500),
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Bot).where(Bot.id == bot_id, Bot.user_id == user.id)
    )
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")

    raw = await ContainerManager.get_logs(bot.container_id, tail=lines)
    log_lines = raw.splitlines() if raw else []
    return {"logs": log_lines}


@router.websocket("/ws/{bot_id}")
async def stream_logs(websocket: WebSocket, bot_id: int):
    from services.auth_service import verify_token
    token = websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=4001)
        return
    try:
        payload = verify_token(token)
        user_id = int(payload.get("sub"))
    except Exception:
        await websocket.close(code=4001)
        return

    async with async_session() as session:
        result = await session.execute(
            select(Bot).where(Bot.id == bot_id, Bot.user_id == user_id)
        )
        bot = result.scalar_one_or_none()
        if not bot:
            await websocket.close(code=4004)
            return
        container_id = bot.container_id

    await LogStreamer.subscribe(bot_id, websocket, container_id)
