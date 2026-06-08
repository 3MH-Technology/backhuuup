from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import get_session
from services.auth_service import AuthService

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    device_fingerprint: str = ""


class VerifyEmailRequest(BaseModel):
    code: str


@router.post("/login")
async def login(req: LoginRequest, session: AsyncSession = Depends(get_session)):
    return await AuthService.authenticate(req.email, req.password, session)


@router.post("/register")
async def register(req: RegisterRequest, session: AsyncSession = Depends(get_session)):
    return await AuthService.register(
        req.username, req.email, req.password, req.device_fingerprint, session
    )


@router.post("/verify-email")
async def verify_email(
    req: VerifyEmailRequest,
    user=Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await AuthService.verify_email(user.id, req.code, session)


@router.post("/resend-code")
async def resend_code(
    user=Depends(AuthService.get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await AuthService.resend_code(user.id, session)


@router.get("/me")
async def me(user=Depends(AuthService.get_current_user)):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "is_verified": user.is_verified,
    }
