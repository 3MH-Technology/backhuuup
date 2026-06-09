import logging
from datetime import date

import aiohttp
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import get_session
from models.user import User
from services.auth_service import AuthService

logger = logging.getLogger("wolfhost.ai")

router = APIRouter(prefix="/api/ai", tags=["AI Assistant"])

OLLAMA_URL = "http://108.181.196.208:11434"
MODEL = "llama3.2:latest"
DAILY_LIMIT = 30

SYSTEM_PROMPT = (
    "أنت مساعد ذكي لمنصة Wolf Host — استضافة الذب هوست. "
    "اسم المطور: الذئب الأبيض 🐺. تيليجرام: @j49_c. القناة: @O5O6J. إكس: @wolfhost_1. "
    "مهمتك مساعدة المبرمجين العرب في استضافة بوتاتهم، الإجابة عن أسئلة البرمجة، "
    "وشرح كيفية استخدام المنصة. المنصة تدعم بوتات بايثون و PHP مع رفع ملفات. "
    "الردود تكون بالعربية الفصحى أو العامية المفهومة. كن مفيداً ومختصراً."
)


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str
    remaining: int


@router.post("/chat")
async def chat(
    req: ChatRequest,
    user: User = Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    today = date.today().isoformat()
    if user.ai_date != today:
        user.ai_messages_today = 0
        user.ai_date = today
        await session.commit()

    if user.ai_messages_today >= DAILY_LIMIT:
        raise HTTPException(status_code=429, detail=f"انتهى حدك اليومي ({DAILY_LIMIT} رسالة). ارجع بكرة!")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": req.message},
    ]

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as sess:
            async with sess.post(
                f"{OLLAMA_URL}/api/chat",
                json={"model": MODEL, "messages": messages, "stream": False, "options": {"temperature": 0.7, "num_predict": 512}},
            ) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=502, detail="AI service unavailable")
                data = await resp.json()
                reply = data.get("message", {}).get("content", "")
    except aiohttp.ClientConnectorError:
        raise HTTPException(status_code=502, detail="لا يمكن الاتصال بخادم AI")

    user.ai_messages_today += 1
    await session.commit()
    remaining = DAILY_LIMIT - user.ai_messages_today

    return ChatResponse(response=reply, remaining=remaining)
