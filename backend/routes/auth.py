from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import get_session
from services.auth_service import AuthService
from services.limiter import limiter

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    device_fingerprint: str = ""


class ForgotPasswordRequest(BaseModel):
    username: str


class ResetPasswordRequest(BaseModel):
    username: str
    code: str
    new_password: str


def validate_password(password: str):
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="كلمة المرور يجب أن تكون 8 أحرف على الأقل")
    if not any(c.isupper() for c in password):
        raise HTTPException(status_code=400, detail="كلمة المرور يجب أن تحتوي على حرف كبير واحد على الأقل")
    if not any(c.islower() for c in password):
        raise HTTPException(status_code=400, detail="كلمة المرور يجب أن تحتوي على حرف صغير واحد على الأقل")
    if not any(c.isdigit() for c in password):
        raise HTTPException(status_code=400, detail="كلمة المرور يجب أن تحتوي على رقم واحد على الأقل")


@router.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, req: LoginRequest, session: AsyncSession = Depends(get_session)):
    return await AuthService.authenticate(req.username, req.password, session)


@router.post("/register")
@limiter.limit("3/hour")
async def register(request: Request, req: RegisterRequest, session: AsyncSession = Depends(get_session)):
    if any(c in req.username for c in "<>\"'&"):
        raise HTTPException(status_code=400, detail="اسم المستخدم يحتوي على أحرف غير مسموحة")
    validate_password(req.password)
    return await AuthService.register(req.username, req.password, req.device_fingerprint, session)


@router.post("/forgot-password")
@limiter.limit("3/hour")
async def forgot_password(request: Request, req: ForgotPasswordRequest, session: AsyncSession = Depends(get_session)):
    return await AuthService.forgot_password(req.username, session)


@router.post("/reset-password")
@limiter.limit("5/minute")
async def reset_password(request: Request, req: ResetPasswordRequest, session: AsyncSession = Depends(get_session)):
    return await AuthService.reset_password(req.username, req.code, req.new_password, session)


@router.get("/me")
async def me(user=Depends(AuthService.get_current_user)):
    is_admin = getattr(user, "is_admin", False)
    return {"id": user.id, "username": user.username, "is_admin": is_admin}
