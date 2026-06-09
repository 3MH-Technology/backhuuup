from sqlalchemy import Column, Integer, String, DateTime, Boolean, func
from sqlalchemy.orm import relationship
from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    username = Column(String(100), unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_active = Column(Integer, default=1)
    is_admin = Column(Boolean, default=False)
    device_fingerprint = Column(String(255), nullable=True)

    reset_code = Column(String(10), nullable=True)
    reset_date = Column(String(20), nullable=True)
    reset_attempts_today = Column(Integer, default=0)
    reset_cooldown_until = Column(DateTime(timezone=True), nullable=True)
    reset_code_expires_at = Column(DateTime(timezone=True), nullable=True)
    reset_code_ip = Column(String(45), nullable=True)
    failed_login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)

    ai_messages_today = Column(Integer, default=0)
    ai_date = Column(String(20), nullable=True)

    bots = relationship("Bot", back_populates="owner", cascade="all, delete-orphan")
