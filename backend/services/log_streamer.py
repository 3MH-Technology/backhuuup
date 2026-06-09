import asyncio
import logging
from typing import Set, Dict

from fastapi import WebSocket

from services.container_manager import ContainerManager

logger = logging.getLogger("wolfhost.logstreamer")

MAX_CONNECTIONS_TOTAL = 50
MAX_CONNECTIONS_PER_BOT = 5
MAX_CONNECTIONS_PER_USER = 10


class LogStreamer:
    _connections: dict[int, Set[WebSocket]] = {}
    _user_connections: Dict[int, Set[WebSocket]] = {}
    _total_connections = 0

    @classmethod
    async def subscribe(cls, bot_id: int, websocket: WebSocket, container_id: str | None, user_id: int | None = None):
        if cls._total_connections >= MAX_CONNECTIONS_TOTAL:
            await websocket.close(code=1008)
            return

        if bot_id in cls._connections and len(cls._connections[bot_id]) >= MAX_CONNECTIONS_PER_BOT:
            await websocket.close(code=1008)
            return

        if user_id and user_id in cls._user_connections and len(cls._user_connections.get(user_id, set())) >= MAX_CONNECTIONS_PER_USER:
            await websocket.close(code=1008)
            return

        await websocket.accept()
        if bot_id not in cls._connections:
            cls._connections[bot_id] = set()
        cls._connections[bot_id].add(websocket)
        if user_id:
            if user_id not in cls._user_connections:
                cls._user_connections[user_id] = set()
            cls._user_connections[user_id].add(websocket)
        cls._total_connections += 1
        try:
            await cls._stream_logs(bot_id, websocket, container_id)
        except Exception:
            pass
        finally:
            cls._connections.get(bot_id, set()).discard(websocket)
            if not cls._connections.get(bot_id):
                cls._connections.pop(bot_id, None)
            if user_id:
                cls._user_connections.get(user_id, set()).discard(websocket)
                if not cls._user_connections.get(user_id):
                    cls._user_connections.pop(user_id, None)
            cls._total_connections = max(0, cls._total_connections - 1)

    @classmethod
    async def _stream_logs(cls, bot_id: int, websocket: WebSocket, container_id: str | None):
        last_content = ""
        while True:
            try:
                cid = container_id
                from models.database import async_session
                from models.bot import Bot
                from sqlalchemy import select
                async with async_session() as session:
                    result = await session.execute(select(Bot.container_id).where(Bot.id == bot_id))
                    row = result.one_or_none()
                    if row:
                        cid = row[0] or cid

                raw = await ContainerManager.get_logs(cid, tail=200) if cid else ""
                if raw and raw != last_content:
                    new_part = raw[len(last_content):] if raw.startswith(last_content) else raw
                    if new_part.strip():
                        await websocket.send_json({"type": "log", "data": new_part})
                    last_content = raw
                status = ContainerManager.get_status(cid) if cid else "stopped"
                await websocket.send_json({"type": "status", "data": status})
            except Exception:
                try:
                    await websocket.send_json({"type": "status", "data": "stopped"})
                except Exception:
                    pass
                break
            await asyncio.sleep(0.5)
