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
from services.email_service import (
    generate_verification_code,
    send_verification_email,
    send_welcome_email,
)

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
        token = create_access_token({"sub": str(user.id), "email": user.email})
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {"id": user.id, "email": user.email, "username": user.username},
            "verified": user.is_verified,
        }

    @staticmethod
    async def register(username: str, email: str, password: str, device_fingerprint: str, session: AsyncSession) -> dict:
        existing = await session.execute(select(User).where((User.email == email) | (User.username == username)))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Email or username already registered")

        if device_fingerprint:
            existing_device = await session.execute(
                select(User).where(User.device_fingerprint == device_fingerprint)
            )
            if existing_device.scalar_one_or_none():
                raise HTTPException(
                    status_code=403,
                    detail="هذا الجهاز مسجل بحساب آخر. كل جهاز يُسمح بحساب واحد فقط.",
                )

        code = generate_verification_code()
        now = datetime.now(timezone.utc)
        user = User(
            username=username,
            email=email,
            hashed_password=get_password_hash(password),
            is_verified=False,
            verification_code=code,
            verification_expires=now + timedelta(minutes=15),
            device_fingerprint=device_fingerprint or None,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        try:
            send_verification_email(email, code)
        except Exception:
            pass

        token = create_access_token({"sub": str(user.id), "email": user.email})
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {"id": user.id, "email": user.email, "username": user.username},
            "verified": False,
            "message": f"تم إرسال رمز التحقق إلى {email}",
        }

    @staticmethod
    async def verify_email(user_id: int, code: str, session: AsyncSession) -> dict:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if user.is_verified:
            return {"message": "البريد موثق بالفعل"}

        if user.verification_code != code:
            raise HTTPException(status_code=400, detail="رمز التحقق غير صحيح")

        now = datetime.now(timezone.utc)
        if user.verification_expires and user.verification_expires.replace(tzinfo=timezone.utc) < now:
            raise HTTPException(status_code=400, detail="انتهت صلاحية رمز التحقق")

        user.is_verified = True
        user.verification_code = None
        user.verification_expires = None
        await session.commit()

        try:
            send_welcome_email(user.email, user.username)
        except Exception:
            pass

        return {"message": "تم توثيق البريد الإلكتروني بنجاح! 🎉"}

    @staticmethod
    async def resend_code(user_id: int, session: AsyncSession) -> dict:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if user.is_verified:
            return {"message": "البريد موثق بالفعل"}

        code = generate_verification_code()
        now = datetime.now(timezone.utc)
        user.verification_code = code
        user.verification_expires = now + timedelta(minutes=15)
        await session.commit()

        try:
            send_verification_email(user.email, code)
        except Exception:
            pass
        return {"message": f"تم إرسال رمز جديد إلى {user.email}"}

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
