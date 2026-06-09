import asyncio
import sys
from sqlalchemy import select
from models.database import async_session
from models.user import User
from services.auth_service import get_password_hash

async def create_admin(username: str, password: str):
    async with async_session() as session:
        existing = await session.execute(select(User).where(User.username == username))
        if existing.scalar_one_or_none():
            print(f"⚠️ User '{username}' already exists")
            return

        user = User(
            username=username,
            hashed_password=get_password_hash(password),
            is_admin=True,
            device_fingerprint=None,
        )
        session.add(user)
        await session.commit()
        print(f"✅ Admin account created: {username}")
        print(f"   Password: {password}")
        print(f"   No limits: device check ⛔, max bots ⛔, access all bots ✅")


if __name__ == "__main__":
    username = sys.argv[1] if len(sys.argv) > 1 else "admin"
    password = sys.argv[2] if len(sys.argv) > 2 else "Admin123!"
    asyncio.run(create_admin(username, password))
