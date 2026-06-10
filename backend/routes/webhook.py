import asyncio
import logging

import aiohttp
from fastapi import APIRouter, Header, Request, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from models.database import async_session
from models.bot import Bot
from services.container_manager import ContainerManager
from services.limiter import limiter
from services.webhook_crypto import hash_token

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
@limiter.limit("120/minute")
async def proxy_webhook_token(request: Request, token: str):
    token_h = hash_token(token)
    async with async_session() as session:
        result = await session.execute(select(Bot).where(Bot.webhook_token_hash == token_h))
        bot = result.scalar_one_or_none()
    if not bot or not bot.webhook_active:
        raise HTTPException(status_code=404, detail="Invalid webhook token")
    return await _proxy_bot(request, bot, "")


@router.api_route("/", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@limiter.limit("120/minute")
async def proxy_webhook_root(request: Request, x_bot_slug: str = Header(default="", alias="X-Bot-Slug"), x_webhook_token: str = Header(default="", alias="X-Webhook-Token")):
    token = x_webhook_token.strip() or x_bot_slug.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Missing webhook token")
    token_h = hash_token(token)
    async with async_session() as session:
        result = await session.execute(select(Bot).where(Bot.webhook_token_hash == token_h))
        bot = result.scalar_one_or_none()
    if not bot or not bot.webhook_active:
        raise HTTPException(status_code=404, detail="Invalid webhook token")
    return await _proxy_bot(request, bot, "")


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@limiter.limit("120/minute")
async def proxy_webhook_legacy(request: Request, path: str):
    return await proxy_webhook_root(request, "", "")


async def _proxy_bot(request: Request, bot, path: str):
    if bot.status != "running" or not bot.container_id:
        return {"ok": False, "error": f"Bot '{bot.slug}' is not running", "bot_status": bot.status}
    container_status = ContainerManager.get_status(bot.container_id)
    if container_status != "running":
        return {"ok": False, "error": f"Bot is {container_status}", "bot_status": container_status}
    target = ContainerManager.get_bot_target(bot.container_id)
    if not target:
        return {"ok": False, "error": "Bot target not assigned"}
    target_url = f"http://{target}/{path}" if path else f"http://{target}/"
    return await _stream(request, target_url, bot.slug)


async def _proxy(request: Request, path: str, x_bot_slug: str):
    slug = extract_slug(request, x_bot_slug)
    if not slug:
        raise HTTPException(status_code=400, detail="Missing bot slug")

    async with async_session() as session:
        result = await session.execute(select(Bot).where(Bot.slug == slug))
        bot = result.scalar_one_or_none()

    if not bot:
        raise HTTPException(status_code=404, detail=f"No bot found with slug '{slug}'")

    return await _proxy_bot(request, bot, path)


WOLF_HEADER = b"""<!-- Wolf Host Platform -->
<div id="wolf-host-bar" style="position:fixed;top:0;left:0;right:0;z-index:2147483647;background:linear-gradient(135deg,#0f0f23 0%,#1a1a3e 100%);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);padding:0 16px;display:flex;align-items:center;gap:10px;direction:ltr;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;border-bottom:1px solid rgba(255,255,255,0.06);box-shadow:0 4px 30px rgba(0,0,0,0.4);height:48px;box-sizing:border-box;">
  <img src="https://wolf-host.pages.dev/static/logo.jpg" onerror="this.style.display='none'" style="height:28px;width:28px;border-radius:6px;flex-shrink:0;object-fit:cover;">
  <span style="color:#fff;font-weight:700;font-size:15px;letter-spacing:0.4px;">Wolf Host</span>
  <span style="color:rgba(255,255,255,0.3);font-size:11px;margin-left:auto;letter-spacing:0.2px;">Bot Hosting Platform</span>
  <a href="https://wolf-host.pages.dev/dashboard" target="_blank" rel="noopener" style="color:rgba(255,255,255,0.4);font-size:11px;text-decoration:none;border:1px solid rgba(255,255,255,0.08);padding:4px 10px;border-radius:6px;transition:0.2s;white-space:nowrap;" onmouseover="this.style.borderColor='rgba(255,255,255,0.3)';this.style.color='#fff'" onmouseout="this.style.borderColor='rgba(255,255,255,0.08)';this.style.color='rgba(255,255,255,0.4)'">Dashboard</a>
</div>
<style>
body { margin-top: 56px !important; }
#wolf-host-bar + * { margin-top: 0 !important; }
@media (max-width: 640px){#wolf-host-bar{gap:6px;padding:0 10px;height:44px}body{margin-top:52px!important}#wolf-host-bar span:last-of-type{display:none}}
</style>"""


def _inject_branding(html_body: bytes) -> bytes:
    """Inject Wolf Host header into HTML responses."""
    body_tag = b"<body"
    idx = html_body.find(body_tag)
    if idx != -1:
        close = html_body.find(b">", idx)
        if close != -1:
            return html_body[:close+1] + WOLF_HEADER + html_body[close+1:]
    return WOLF_HEADER + html_body


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
    sess = aiohttp.ClientSession(timeout=timeout)

    try:
        resp = await sess.request(
            method=request.method,
            url=target_url,
            headers=headers_to_forward,
            data=body,
        )
    except aiohttp.ClientConnectorError:
        await sess.close()
        return {"ok": False, "error": "Bot unreachable"}
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        await sess.close()
        logger.warning(f"Webhook proxy failed for {slug}: {e}")
        return {"ok": False, "error": "Proxy upstream error"}

    content_type = resp.headers.get("Content-Type", "application/json")
    is_html = "text/html" in content_type

    async def stream_response(resp, sess, is_html):
        try:
            buffer = b""
            injected = False
            async for chunk in resp.content.iter_chunked(8192):
                if is_html and not injected:
                    buffer += chunk
                    if len(buffer) > 131072 or b"<body" in buffer:
                        yield _inject_branding(buffer)
                        injected = True
                        buffer = b""
                else:
                    yield chunk
            if is_html and not injected and buffer:
                yield _inject_branding(buffer)
            elif buffer:
                yield buffer
        finally:
            resp.release()
            await sess.close()

    return StreamingResponse(
        stream_response(resp, sess, is_html),
        media_type=content_type,
    )


# ── Public bot proxy by slug (no webhook token required) ──
public_router = APIRouter(prefix="/p", tags=["Public Bot Proxy"])


@public_router.api_route("/{slug}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@limiter.limit("120/minute")
async def proxy_public_path(request: Request, slug: str, path: str = ""):
    return await _proxy(request, path, slug)


@public_router.api_route("/{slug}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@limiter.limit("120/minute")
async def proxy_public_root(request: Request, slug: str):
    return await _proxy(request, "", slug)
