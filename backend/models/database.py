import logging
import os

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

logger = logging.getLogger("wolfhost.db")


class Base(DeclarativeBase):
    pass


async def _add_missing_columns(conn):
    """Apply incremental migrations. Log failures instead of swallowing them.

    NOTE: This is a lightweight migration approach. For production use,
    consider adopting Alembic for proper schema versioning and rollback support.
    """
    migrations = [
        "ALTER TABLE bots ADD COLUMN IF NOT EXISTS is_upload BOOLEAN DEFAULT FALSE",
        "ALTER TABLE bots ADD COLUMN IF NOT EXISTS upload_path VARCHAR(512)",
        "ALTER TABLE bots ADD COLUMN IF NOT EXISTS webhook_url VARCHAR(512)",
        "ALTER TABLE bots ADD COLUMN IF NOT EXISTS webhook_active BOOLEAN DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS ai_messages_today INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS ai_date VARCHAR(20)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_code VARCHAR(10)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_date VARCHAR(20)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_attempts_today INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_cooldown_until TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE bots DROP COLUMN IF EXISTS expires_at",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255)",
        "ALTER TABLE users ALTER COLUMN email DROP NOT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_code_expires_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_code_ip VARCHAR(45)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_login_attempts INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE bots ADD COLUMN IF NOT EXISTS slug VARCHAR(255)",
        "ALTER TABLE bots ADD COLUMN IF NOT EXISTS webhook_token TEXT",
        "UPDATE bots SET webhook_token = NULL, webhook_active = FALSE, webhook_url = NULL WHERE webhook_token IS NOT NULL AND webhook_token !~ '^gAAAA'",
    ]
    for stmt in migrations:
        try:
            await conn.execute(text(stmt))
        except Exception as e:
            logger.warning(f"Migration skipped (column may already exist): {e}")

    # webhook_token may exist as VARCHAR(64) from old schema — resize to TEXT only if needed
    try:
        row = await conn.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name='bots' AND column_name='webhook_token'"
        ))
        col_type = row.scalar()
        if col_type and col_type != 'text':
            await conn.execute(text("ALTER TABLE bots ALTER COLUMN webhook_token TYPE TEXT"))
    except Exception as e:
        logger.warning(f"webhook_token resize skipped: {e}")

    # Update check constraint to include 'static' bot type
    try:
        await conn.execute(text("ALTER TABLE bots DROP CONSTRAINT IF EXISTS ck_bot_type"))
        await conn.execute(text(
            "ALTER TABLE bots ADD CONSTRAINT ck_bot_type "
            "CHECK (bot_type IN ('python', 'php', 'static'))"
        ))
    except Exception as e:
        logger.warning(f"Bot type constraint update skipped: {e}")


async def init_db():
    async with engine.begin() as conn:
        from models.user import User
        from models.bot import Bot
        if os.environ.get("RESET_DB") == "1":
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            logger.warning("Database reset forced via RESET_DB=1")
        else:
            try:
                await conn.run_sync(Base.metadata.create_all)
            except Exception as e:
                logger.error(f"Schema creation failed: {e}. Run migrations manually or set RESET_DB=1 to recreate.")
                raise
        await _add_missing_columns(conn)


async def get_session():
    async with async_session() as session:
        yield session
