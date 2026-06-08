import asyncio
from typing import Set

from fastapi import WebSocket

from services.container_manager import ContainerManager


class LogStreamer:
    _connections: dict[int, Set[WebSocket]] = {}

    @classmethod
    async def subscribe(cls, bot_id: int, websocket: WebSocket, container_id: str | None):
        await websocket.accept()
        if bot_id not in cls._connections:
            cls._connections[bot_id] = set()
        cls._connections[bot_id].add(websocket)
        try:
            await cls._stream_logs(bot_id, websocket, container_id)
        except Exception:
            pass
        finally:
            cls._connections.get(bot_id, set()).discard(websocket)
            if not cls._connections.get(bot_id):
                cls._connections.pop(bot_id, None)

    @classmethod
    async def _stream_logs(cls, bot_id: int, websocket: WebSocket, container_id: str | None):
        last_content = ""
        while True:
            try:
                raw = await ContainerManager.get_logs(container_id, tail=200) if container_id else ""
                if raw and raw != last_content:
                    new_part = raw[len(last_content):] if raw.startswith(last_content) else raw
                    if new_part.strip():
                        await websocket.send_json({"type": "log", "data": new_part})
                    last_content = raw
                status = ContainerManager.get_status(container_id) if container_id else "stopped"
                await websocket.send_json({"type": "status", "data": status})
            except Exception:
                await websocket.send_json({"type": "status", "data": "stopped"})
                break
            await asyncio.sleep(0.5)
