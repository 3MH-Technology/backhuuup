import asyncio
import logging
import os

import aiohttp
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse

from services.limiter import limiter

logger = logging.getLogger("wolfhost.tgproxy")

router = APIRouter(prefix="/api/tg", tags=["Telegram Proxy"])

TG_API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")


@router.api_route("/{token}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@limiter.limit("60/minute")
async def tg_proxy(request: Request, token: str, path: str):
    target = f"{TG_API_BASE}/bot{token}/{path}"
    body = await request.body()
    headers_to_forward = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "x-forwarded-for",
                            "x-forwarded-proto", "x-forwarded-host")
    }
    timeout = aiohttp.ClientTimeout(total=60, connect=10)

    async def stream():
        try:
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.request(
                    method=request.method,
                    url=target,
                    headers=headers_to_forward,
                    data=body,
                ) as resp:
                    async for chunk in resp.content.iter_chunked(8192):
                        yield chunk
        except aiohttp.ClientConnectorError:
            yield b'{"ok":false,"error":"Telegram API unreachable"}'
        except (aiohttp.ClientError, asyncio.TimeoutError):
            yield b'{"ok":false,"error":"Proxy error"}'

    return StreamingResponse(stream(), media_type="application/json")


@router.api_route("/file/{token}/{path:path}", methods=["GET"])
@limiter.limit("30/minute")
async def tg_file_proxy(request: Request, token: str, path: str):
    target = f"{TG_API_BASE}/file/bot{token}/{path}"
    timeout = aiohttp.ClientTimeout(total=60, connect=10)

    async def stream():
        try:
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(target) as resp:
                    async for chunk in resp.content.iter_chunked(8192):
                        yield chunk
        except aiohttp.ClientConnectorError:
            yield b'{"ok":false,"error":"Telegram file API unreachable"}'
        except (aiohttp.ClientError, asyncio.TimeoutError):
            yield b'{"ok":false,"error":"Proxy error"}'

    return StreamingResponse(stream(), media_type="application/octet-stream")
