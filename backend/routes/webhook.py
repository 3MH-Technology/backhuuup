import asyncio
import logging

import aiohttp
from fastapi import APIRouter, Header, Request, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from models.database import get_session
from models.bot import Bot
from services.container_manager import ContainerManager

logger = logging.getLogger("wolfhost.webhook")

router = APIRouter(prefix="/api/webhook", tags=["Webhook"])


def extract_slug(request: Request, x_bot_slug: str) -> str:
    slug = x_bot_slug.strip().lower()
    if slug:
        return slug
    host = request.headers.get("host", "")
    dot_index = host.find(".")
    if dot_index > 0:
        return host[:dot_index].strip().lower()
    return ""


@router.api_route("/{token}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_webhook_token(request: Request, token: str):
    async with get_session() as session:
        result = await session.execute(select(Bot).where(Bot.webhook_token == token))
        bot = result.scalar_one_or_none()
    if not bot or not bot.webhook_active:
        raise HTTPException(status_code=404, detail="Invalid webhook token")
    return await _proxy_bot(request, bot, "")


@router.api_route("/", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_webhook_root(request: Request, x_bot_slug: str = Header(default="", alias="X-Bot-Slug"), x_webhook_token: str = Header(default="", alias="X-Webhook-Token")):
    token = x_webhook_token.strip() or x_bot_slug.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Missing webhook token")
    async with get_session() as session:
        result = await session.execute(select(Bot).where(Bot.webhook_token == token))
        bot = result.scalar_one_or_none()
    if not bot or not bot.webhook_active:
        raise HTTPException(status_code=404, detail="Invalid webhook token")
    return await _proxy_bot(request, bot, "")


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_webhook_legacy(request: Request, path: str):
    return await proxy_webhook_root(request, "", "")


async def _proxy_bot(request: Request, bot, path: str):
    if bot.status != "running" or not bot.container_id:
        return {"ok": False, "error": f"Bot '{bot.slug}' is not running", "bot_status": bot.status}
    container_status = ContainerManager.get_status(bot.container_id)
    if container_status != "running":
        return {"ok": False, "error": f"Bot is {container_status}", "bot_status": container_status}
    port = ContainerManager.get_bot_port(bot.container_id) or 8080
    target_url = f"http://127.0.0.1:{port}/{path}" if path else f"http://127.0.0.1:{port}/"
    return await _stream(request, target_url, bot.slug)


async def _proxy(request: Request, path: str, x_bot_slug: str):
    slug = extract_slug(request, x_bot_slug)
    if not slug:
        raise HTTPException(status_code=400, detail="Missing bot slug")

    async with get_session() as session:
        result = await session.execute(select(Bot).where(Bot.slug == slug))
        bot = result.scalar_one_or_none()

    if not bot:
        raise HTTPException(status_code=404, detail=f"No bot found with slug '{slug}'")

    return await _proxy_bot(request, bot, path)


async def _stream(request: Request, target_url: str, slug: str):
    body = await request.body()
    headers_to_forward = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "x-bot-slug", "content-length", "x-forwarded-for",
                            "x-forwarded-proto", "x-forwarded-host")
    }
    headers_to_forward.pop("cf-ray", None)
    headers_to_forward.pop("cf-visitor", None)
    headers_to_forward.pop("cf-connecting-ip", None)

    timeout = aiohttp.ClientTimeout(total=25, connect=5)

    async def stream_response():
        try:
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.request(
                    method=request.method,
                    url=target_url,
                    headers=headers_to_forward,
                    data=body,
                ) as resp:
                    async for chunk in resp.content.iter_chunked(8192):
                        yield chunk
        except aiohttp.ClientConnectorError:
            logger.error(f"Webhook proxy: cannot connect to bot {slug}")
            yield b'{"ok":false,"error":"Bot unreachable"}'
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"Webhook proxy failed for {slug}: {e}")
            yield f'{{"ok":false,"error":"{e}"}}'.encode()

    return StreamingResponse(stream_response(), media_type="application/json")
