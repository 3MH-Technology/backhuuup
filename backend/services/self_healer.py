import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from config import settings
from models.database import async_session
from models.bot import Bot
from services.container_manager import ContainerManager

logger = logging.getLogger("wolfhost.healer")


class SelfHealer:
    _task: asyncio.Task | None = None
    _running = False

    @classmethod
    def start(cls):
        if cls._running:
            return
        cls._running = True
        cls._task = asyncio.create_task(cls._heal_loop())
        logger.info("🐺 Self-healer polling every 30s")

    @classmethod
    async def stop(cls):
        cls._running = False
        if cls._task:
            cls._task.cancel()
            try:
                await cls._task
            except asyncio.CancelledError:
                pass
        logger.info("🐺 Self-healer stopped")

    @classmethod
    async def _heal_loop(cls):
        while cls._running:
            try:
                await cls._heal_once()
            except Exception as exc:
                logger.error(f"🐺 Healer error: {exc}")
            await asyncio.sleep(30)

    @classmethod
    async def _heal_once(cls):
        async with async_session() as session:
            try:
                result = await session.execute(
                    select(Bot).where(Bot.status == "running").with_for_update(skip_locked=True)
                )
            except Exception as exc:
                if "InvalidCachedStatement" in type(exc).__name__:
                    logger.info("Healer skipped (schema cache refresh)")
                    return
                raise
            else:
                bots = result.scalars().all()

            for bot in bots:
                if ContainerManager.container_exists(bot.container_id):
                    state = ContainerManager.get_status(bot.container_id)
                    if state == "running":
                        continue

                if bot.restart_count < settings.container_auto_restart_max:
                    new_count = bot.restart_count + 1
                    logger.info(
                        f"🔄 Healing bot {bot.id} ({bot.slug}) — "
                        f"attempt {new_count}/{settings.container_auto_restart_max}"
                    )
                    outcome = await ContainerManager.start_bot(
                        bot_id=bot.id,
                        user_id=bot.user_id,
                        slug=bot.slug,
                        bot_type=bot.bot_type,
                        main_file_content=bot.main_file or "",
                        requirements=bot.requirements or "",
                        is_upload=bot.is_upload or False,
                    )
                    if outcome["status"] == "success":
                        bot.container_id = outcome["container_id"]
                        bot.restart_count = new_count
                        bot.status = "running"
                    else:
                        bot.restart_count = new_count
                        logger.warning(f"Restart failed for bot {bot.id}: {outcome['message']}")
                else:
                    logger.warning(f"Bot {bot.id} ({bot.slug}) — max restarts exceeded, marking crashed")
                    bot.status = "crashed"

            await session.commit()

    @classmethod
    async def recover_running_bots(cls):
        logger.info("🐺 Recovering previously running bots after restart...")
        async with async_session() as session:
            result = await session.execute(
                select(Bot).where(Bot.status == "running")
            )
            bots = result.scalars().all()
            recovered = 0
            for bot in bots:
                if ContainerManager.container_exists(bot.container_id):
                    logger.info(f"  Bot {bot.id} ({bot.slug}) — container exists, skipping")
                    continue
                logger.info(f"  Recreating bot {bot.id} ({bot.slug})...")
                outcome = await ContainerManager.start_bot(
                    bot_id=bot.id,
                    user_id=bot.user_id,
                    slug=bot.slug,
                    bot_type=bot.bot_type,
                    main_file_content=bot.main_file or "",
                    requirements=bot.requirements or "",
                    is_upload=bot.is_upload or False,
                )
                if outcome["status"] == "success":
                    bot.container_id = outcome["container_id"]
                    bot.restart_count = 0
                    bot.status = "running"
                    recovered += 1
                else:
                    bot.status = "crashed"
                    logger.error(f"  Failed: {outcome['message']}")
            await session.commit()
            logger.info(f"Recovery complete — {recovered} bots recreated")
