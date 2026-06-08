from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import relationship
from .database import Base


class Bot(Base):
    __tablename__ = "bots"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False, index=True, unique=True)
    bot_type = Column(String(20), nullable=False)  # "python" or "php"
    status = Column(String(20), default="stopped", index=True)  # running, stopped, crashed
    container_id = Column(String(64), nullable=True, index=True)
    main_file = Column(Text, nullable=True)
    requirements = Column(Text, nullable=True)
    restart_count = Column(Integer, default=0)  # self-healing counter
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner = relationship("User", back_populates="bots")
