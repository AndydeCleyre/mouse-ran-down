"""Download videos from any sent popular video site links, and upload them to the chat."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .credentials import TOKEN  # pyright: ignore [reportMissingImports]
from instagrapi import Client as InstaClient
from plumbum import local
from telebot import TeleBot

from .link_handling import LinkHandlers
from .mrd_logging import StructLogger, get_logger
from .sending import LootSender

try:
    from .credentials import INSTA_PW, INSTA_USER  # pyright: ignore [reportMissingImports]
except ImportError:
    INSTA_USER, INSTA_PW = None, None

try:
    from .credentials import COOKIES  # pyright: ignore [reportMissingImports]

    (local.path(__file__).up() / 'cookies.txt').write(COOKIES)
    COOKIES = str(local.path(__file__).up() / 'cookies.txt')
except ImportError:
    COOKIES = None

if TYPE_CHECKING:
    from telebot.types import Message


def get_insta(user: str | None, pw: str | None, logger: StructLogger) -> InstaClient | None:
    """Initialize instagrapi, if we've got the credentials."""
    logger.info("Initializing instagrapi")
    if user and pw:
        try:
            insta = InstaClient()
            insta.login(user, pw)
        except Exception as e:
            logger.error("Failed to login", client='instagrapi', exc_info=e)
            insta = None
    else:
        logger.info("Instagram credentials missing, instagrapi will not be used")
        insta = None
    logger.info("Finished initializing instagrapi", insta=insta)


def main():
    """Start the bot."""
    logger = get_logger(json=False)
    bot = TeleBot(TOKEN)
    insta = get_insta(INSTA_USER, INSTA_PW, logger)
    timeout = 120
    loot_sender = LootSender(bot=bot, logger=logger, timeout=timeout)
    link_handlers = LinkHandlers(sender=loot_sender, logger=logger, insta=insta, cookies=COOKIES)

    @bot.business_message_handler(func=bool)
    @bot.message_handler(func=bool)
    def media_link_handler(message: Message):
        """Download from any URLs that we handle and upload content to the chat."""
        link_handlers.media_link_handler(message)

    bot.infinity_polling(timeout=timeout, long_polling_timeout=timeout)
