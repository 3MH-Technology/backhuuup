from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from config import settings
from models.database import get_session
from models.user import User
from services.email_service import generate_code, send_verification

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
security_scheme = HTTPBearer()


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def verify_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        return payload
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


class AuthService:
    @staticmethod
    async def authenticate(username: str, password: str, session: AsyncSession) -> dict:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة المرور خطأ")

        now = datetime.now(timezone.utc)
        if user.locked_until and now < user.locked_until:
            remaining = int((user.locked_until - now).total_seconds())
            raise HTTPException(status_code=429, detail=f"الحساب مقفل. حاول بعد {remaining} ثانية.")

        if not verify_password(password, user.hashed_password):
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= 5:
                user.locked_until = now + timedelta(minutes=15)
            await session.commit()
            raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة المرور خطأ")

        user.failed_login_attempts = 0
        user.locked_until = None
        await session.commit()
        token = create_access_token({"sub": str(user.id), "username": user.username})
        return {"access_token": token, "token_type": "bearer", "user": {"id": user.id, "username": user.username, "is_admin": bool(getattr(user, "is_admin", False))}}

    @staticmethod
    async def register(username: str, password: str, device_fingerprint: str, session: AsyncSession) -> dict:
        existing = await session.execute(select(User).where(User.username == username))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="اسم المستخدم موجود مسبقاً")

        if device_fingerprint:
            existing_device = await session.execute(select(User).where(User.device_fingerprint == device_fingerprint))
            if existing_device.scalar_one_or_none():
                raise HTTPException(status_code=403, detail="هذا الجهاز مسجل بحساب آخر. حساب واحد لكل جهاز.")

        user = User(
            username=username,
            hashed_password=get_password_hash(password),
            device_fingerprint=device_fingerprint or None,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        token = create_access_token({"sub": str(user.id), "username": user.username})
        return {
            "access_token": token, "token_type": "bearer",
            "user": {"id": user.id, "username": user.username, "is_admin": False},
        }

    @staticmethod
    async def forgot_password(username: str, request: Request, session: AsyncSession) -> dict:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="اسم المستخدم غير صحيح")

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if user.reset_date != today:
            user.reset_date = today
            user.reset_attempts_today = 0
            user.reset_code = None
            user.reset_cooldown_until = None
            user.reset_code_expires_at = None

        if user.reset_attempts_today >= 3:
            raise HTTPException(status_code=429, detail="لقد استنفذت محاولاتك اليومية (3 محاولات). حاول بكرة.")

        now = datetime.now(timezone.utc)
        if user.reset_cooldown_until and now < user.reset_cooldown_until:
            remaining = int((user.reset_cooldown_until - now).total_seconds())
            raise HTTPException(status_code=429, detail=f"انتظر {remaining} ثانية قبل إعادة المحاولة")

        cooldowns = [60, 120, 300]
        level = user.reset_attempts_today

        code = generate_code()
        user.reset_code = code
        user.reset_code_expires_at = now + timedelta(minutes=5)
        user.reset_code_ip = request.client.host if request.client else None
        user.reset_attempts_today = level + 1
        user.reset_cooldown_until = now + timedelta(seconds=cooldowns[min(level, len(cooldowns) - 1)])
        await session.commit()

        # Send reset code via email if user has an email address
        email_sent = False
        if user.email:
            email_sent = send_verification(user.email, code)

        return {
            "message": "تم إنشاء كود إعادة التعيين. الكود صالح لمدة 5 دقائق.",
            "reset_code": code,
            "email_sent": email_sent,
        }

    @staticmethod
    async def get_reset_code(username: str, request: Request, session: AsyncSession) -> dict:
        # SECURITY: This endpoint has been removed.
        # Reset codes are now returned inline in forgot_password() response.
        # Previously, an unauthenticated GET could expose reset codes to anyone.
        raise HTTPException(status_code=404, detail="Endpoint removed for security reasons")

    @staticmethod
    async def reset_password(username: str, code: str, new_password: str, request: Request, session: AsyncSession) -> dict:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="اسم المستخدم غير موجود")
        if user.reset_code is None or user.reset_code != code:
            raise HTTPException(status_code=400, detail="كود خاطئ أو منتهي الصلاحية")

        if user.reset_code_expires_at and datetime.now(timezone.utc) > user.reset_code_expires_at:
            user.reset_code = None
            user.reset_code_expires_at = None
            await session.commit()
            raise HTTPException(status_code=400, detail="انتهت صلاحية الكود. أعد المحاولة.")

        client_ip = request.client.host if request.client else None
        if user.reset_code_ip and client_ip and user.reset_code_ip != client_ip:
            raise HTTPException(status_code=403, detail="الكود صالح فقط من نفس عنوان IP الذي طلبه.")

        if len(new_password) < 8 or not any(c.isupper() for c in new_password) or not any(c.islower() for c in new_password) or not any(c.isdigit() for c in new_password):
            raise HTTPException(status_code=400, detail="كلمة المرور يجب أن تكون 8 أحرف على الأقل، تحتوي على حرف كبير، حرف صغير، ورقم")

        user.hashed_password = get_password_hash(new_password)
        user.reset_code = None
        user.reset_code_expires_at = None
        user.reset_cooldown_until = None
        await session.commit()
        return {"message": "تم تغيير كلمة المرور بنجاح. سجل دخول الآن."}

    @staticmethod
    async def get_current_user(
        credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
        session: AsyncSession = Depends(get_session),
    ) -> User:
        payload = verify_token(credentials.credentials)
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid token")
        user_id = int(sub)
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
