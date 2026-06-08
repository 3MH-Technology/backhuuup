from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from config import settings


def _clean_db_url(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    clean_params = {}
    for k, v in params.items():
        val = v[0] if isinstance(v, list) and len(v) == 1 else v[0]
        if k != "sslmode" and k != "channel_binding":
            clean_params[k] = val
    new_query = urlencode(clean_params) if clean_params else ""
    return urlunparse(parsed._replace(query=new_query))


_db_url = _clean_db_url(settings.database_url)
_ssl_required = "sslmode=require" in settings.database_url.lower()

_connect_args = {"timeout": 30, "command_timeout": 30}
if _ssl_required:
    import ssl
    _connect_args["ssl"] = ssl.create_default_context()

engine = create_async_engine(
    _db_url, echo=False, pool_size=5, max_overflow=10,
    pool_pre_ping=True, pool_recycle=1800, connect_args=_connect_args,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    async with engine.begin() as conn:
        from models.user import User
        from models.bot import Bot
        try:
            await conn.run_sync(Base.metadata.create_all)
        except Exception:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.run_sync(Base.metadata.create_all)


async def get_session():
    async with async_session() as session:
        yield session
