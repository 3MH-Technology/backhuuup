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
        return {"access_token": token, "token_type": "bearer", "user": {"id": user.id, "email": user.email, "username": user.username}}

    @staticmethod
    async def register(username: str, email: str, password: str, session: AsyncSession) -> dict:
        existing = await session.execute(select(User).where((User.email == email) | (User.username == username)))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Email or username already registered")
        user = User(username=username, email=email, hashed_password=get_password_hash(password))
        session.add(user)
        await session.commit()
        await session.refresh(user)
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
