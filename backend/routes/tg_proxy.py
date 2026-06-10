import asyncio
import logging
import os
import re

import aiohttp
from fastapi import APIRouter, Request
from fastapi.responses import Response, JSONResponse

from services.limiter import limiter

logger = logging.getLogger("wolfhost.tgproxy")

router = APIRouter(prefix="/api/tg", tags=["Telegram Proxy"])

TG_API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")

# Telegram bot token format: digits:alphanumeric-hyphen-underscore
_TG_TOKEN_RE = re.compile(r'^[0-9]+:[A-Za-z0-9_\-]+$')
# Whitelist for safe API method paths
_TG_PATH_RE = re.compile(r'^[a-zA-Z0-9/_\-.?&=%]*$')


def _validate_tg_token(token: str) -> bool:
    return bool(_TG_TOKEN_RE.match(token))


def _validate_tg_path(path: str) -> bool:
    return bool(_TG_PATH_RE.match(path))


@router.api_route("/{token}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@limiter.limit("60/minute")
async def tg_proxy(request: Request, token: str, path: str):
    if not _validate_tg_token(token):
        return JSONResponse({"ok": False, "error": "Invalid bot token format"}, status_code=400)
    if not _validate_tg_path(path):
        return JSONResponse({"ok": False, "error": "Invalid path"}, status_code=400)
    target = f"{TG_API_BASE}/bot{token}/{path}"
    body = await request.body()
    headers_to_forward = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "x-forwarded-for",
                            "x-forwarded-proto", "x-forwarded-host")
    }
    timeout = aiohttp.ClientTimeout(total=60, connect=10)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.request(
                method=request.method,
                url=target,
                headers=headers_to_forward,
                data=body,
            ) as resp:
                content = await resp.read()
                ctype = resp.content_type or "application/json"
                return Response(content=content, status_code=resp.status, media_type=ctype)
    except aiohttp.ClientConnectorError:
        return JSONResponse({"ok": False, "error": "Telegram API unreachable"}, status_code=502)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning(f"tg_proxy error for {token}/{path}: {e}")
        return JSONResponse({"ok": False, "error": "Proxy error"}, status_code=502)


@router.api_route("/file/{token}/{path:path}", methods=["GET"])
@limiter.limit("30/minute")
async def tg_file_proxy(request: Request, token: str, path: str):
    if not _validate_tg_token(token):
        return JSONResponse({"ok": False, "error": "Invalid bot token format"}, status_code=400)
    if not _validate_tg_path(path):
        return JSONResponse({"ok": False, "error": "Invalid path"}, status_code=400)
    target = f"{TG_API_BASE}/file/bot{token}/{path}"
    timeout = aiohttp.ClientTimeout(total=60, connect=10)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(target) as resp:
                content = await resp.read()
                ctype = resp.content_type or "application/octet-stream"
                return Response(content=content, status_code=resp.status, media_type=ctype)
    except aiohttp.ClientConnectorError:
        return JSONResponse({"ok": False, "error": "Telegram file API unreachable"}, status_code=502)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning(f"tg_file_proxy error for {token}/{path}: {e}")
        return JSONResponse({"ok": False, "error": "Proxy error"}, status_code=502)
