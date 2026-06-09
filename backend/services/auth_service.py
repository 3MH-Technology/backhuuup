from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
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
    async def authenticate(email: str, password: str, session: AsyncSession) -> dict:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if not user or not verify_password(password, user.hashed_password):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        if not user.email_verified:
            raise HTTPException(status_code=403, detail="البريد الإلكتروني غير موثق. تحقق من بريدك أولاً.")
        token = create_access_token({"sub": str(user.id), "email": user.email})
        return {"access_token": token, "token_type": "bearer", "user": {"id": user.id, "email": user.email, "username": user.username}}

    @staticmethod
    async def register(username: str, email: str, password: str, device_fingerprint: str, session: AsyncSession) -> dict:
        existing = await session.execute(select(User).where((User.email == email) | (User.username == username)))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Email or username already registered")

        if device_fingerprint:
            existing_device = await session.execute(select(User).where(User.device_fingerprint == device_fingerprint))
            if existing_device.scalar_one_or_none():
                raise HTTPException(status_code=403, detail="هذا الجهاز مسجل بحساب آخر. حساب واحد لكل جهاز.")

        code = generate_code()
        user = User(
            username=username, email=email,
            hashed_password=get_password_hash(password),
            device_fingerprint=device_fingerprint or None,
            email_verified=0,
            verification_code=code,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        sent = send_verification(email, code)
        return {
            "message": "تم إنشاء الحساب. تحقق من بريدك الإلكتروني لكود التفعيل." if sent else "حساب تم إنشاؤه (تعذر إرسال البريد).",
            "email_sent": sent,
            "user_id": user.id,
        }

    @staticmethod
    async def send_code(email: str, session: AsyncSession) -> dict:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="البريد غير مسجل")
        if user.email_verified:
            return {"message": "البريد موثق بالفعل"}

        code = generate_code()
        user.verification_code = code
        await session.commit()
        sent = send_verification(email, code)
        if not sent:
            raise HTTPException(status_code=500, detail="فشل إرسال البريد. تحقق من إعدادات SMTP.")
        return {"message": "تم إرسال الكود"}

    @staticmethod
    async def verify_email(email: str, code: str, session: AsyncSession) -> dict:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="البريد غير مسجل")
        if user.email_verified:
            return {"message": "موثق بالفعل"}
        if user.verification_code != code:
            raise HTTPException(status_code=400, detail="كود خاطئ")

        user.email_verified = 1
        user.verification_code = None
        await session.commit()

        token = create_access_token({"sub": str(user.id), "email": user.email})
        return {"access_token": token, "token_type": "bearer", "user": {"id": user.id, "email": user.email, "username": user.username}}

    @staticmethod
    async def get_current_user(
        credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
        session: AsyncSession = Depends(get_session),
    ) -> User:
        payload = verify_token(credentials.credentials)
        user_id = int(payload.get("sub"))
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
