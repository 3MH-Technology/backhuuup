#!/usr/bin/env python3
"""
Host Reboot Recovery Script

Runs automatically on server startup (via systemd or as part of
the FastAPI lifespan).  Reads the database and restarts all containers
that were marked as "running" before the shutdown.

Integrated into main.py via SelfHealer.recover_running_bots().
This standalone version is provided for direct systemd use.
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from models.database import init_db, async_session
from models.bot import Bot
from sqlalchemy import select
from services.container_manager import ContainerManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reboot_recovery")


async def recover():
    logger.info("=== Host Reboot Recovery ===")
    await init_db()

    async with async_session() as session:
        result = await session.execute(select(Bot).where(Bot.status == "running"))
        bots = result.scalars().all()

        if not bots:
            logger.info("No running bots to recover.")
            return

        logger.info(f"Found {len(bots)} bots to recover...")
        for bot in bots:
            if ContainerManager.container_exists(bot.container_id):
                logger.info(f"  Bot {bot.id} ({bot.slug}) — container still exists, skipping")
                continue

            logger.info(f"  Recreating bot {bot.id} (slug: {bot.slug})...")
            outcome = await ContainerManager.start_bot(
                bot_id=bot.id,
                user_id=bot.user_id,
                slug=bot.slug,
                bot_type=bot.bot_type,
                main_file_content=bot.main_file or "",
                requirements=bot.requirements or "",
            )
            if outcome["status"] == "success":
                bot.container_id = outcome["container_id"]
                bot.restart_count = 0
                logger.info(f"    ✅ Recovered → {outcome['container_id'][:12]}...")
            else:
                bot.status = "crashed"
                logger.error(f"    ❌ Failed: {outcome['message']}")

        await session.commit()
        logger.info("=== Recovery complete ===")


if __name__ == "__main__":
    asyncio.run(recover())
