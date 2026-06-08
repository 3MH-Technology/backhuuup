from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=15,
    max_overflow=25,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args={
        "timeout": 30,
        "command_timeout": 30,
    },
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def init_db():
    async with engine.begin() as conn:
        from models.user import User
        from models.bot import Bot
        await conn.run_sync(Base.metadata.create_all)

    async with engine.begin() as conn:
        for stmt in [
            "CREATE INDEX IF NOT EXISTS idx_bots_user_id ON bots (user_id)",
            "CREATE INDEX IF NOT EXISTS idx_bots_status ON bots (status)",
            "CREATE INDEX IF NOT EXISTS idx_bots_user_status ON bots (user_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_bots_slug ON bots (slug)",
            "CREATE INDEX IF NOT EXISTS idx_users_email ON users (email)",
        ]:
            await conn.execute(text(stmt))


async def get_session():
    async with async_session() as session:
        yield session
