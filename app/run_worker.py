import asyncio
import logging

from app.config import get_settings
from app.db import create_session_factory, init_database
from app.mail.sync import EmailSyncWorker


async def _serve() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    await init_database(session_factory)
    worker = EmailSyncWorker(settings=settings, session_factory=session_factory)
    await worker.run_forever()


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
