import asyncio
import logging
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiogram import Bot, Dispatcher

from app.ai.client import GigaChatClient
from app.bot.handlers import register_handlers
from app.config import get_settings
from app.db import create_session_factory, init_database
from app.services.recommendations import RecommendationService
from app.services.reports import ReportService


async def start_bot() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")

    session_factory = create_session_factory(settings.database_url)
    await init_database(session_factory)
    report_service = ReportService(session_factory)
    recommendation_service = RecommendationService(
        session_factory,
        GigaChatClient(settings),
    )

    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    register_handlers(dispatcher, report_service, recommendation_service)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            close_bot_session=False,
        )
    finally:
        await bot.session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_bot())


if __name__ == "__main__":
    main()
