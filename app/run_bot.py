import asyncio
import logging

from app.bot.app import start_bot


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_bot())


if __name__ == "__main__":
    main()
