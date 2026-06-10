from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean, CheckConstraint, func
from sqlalchemy.orm import relationship
from .database import Base


class Bot(Base):
    __tablename__ = "bots"
    __table_args__ = (
        CheckConstraint("bot_type IN ('python', 'php', 'static')", name="ck_bot_type"),
        CheckConstraint("status IN ('running', 'stopped', 'crashed')", name="ck_bot_status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False, index=True, unique=True)
    bot_type = Column(String(20), nullable=False)  # "python", "php", or "static"
    status = Column(String(20), default="stopped", index=True)  # running, stopped, crashed
    container_id = Column(String(64), nullable=True, index=True)
    main_file = Column(Text, nullable=True)
    requirements = Column(Text, nullable=True)

    is_upload = Column(Boolean, default=False)
    upload_path = Column(String(512), nullable=True)

    webhook_url = Column(String(512), nullable=True)
    webhook_active = Column(Boolean, default=False)
    webhook_token = Column(Text, nullable=True)       # Fernet-encrypted (for display)
    webhook_token_hash = Column(String(64), nullable=True, index=True)  # SHA-256 hash (for SQL lookups)

    restart_count = Column(Integer, default=0)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner = relationship("User", back_populates="bots")
