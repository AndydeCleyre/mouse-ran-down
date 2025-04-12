"""Download videos from any sent popular video site links, and upload them to the chat."""

from __future__ import annotations

import sys
from tempfile import mkstemp
from typing import TYPE_CHECKING, cast

from instagrapi import Client as InstaClient
from nestedtext import load as nt_load
from telebot import TeleBot

from .link_handling import LinkHandlers
from .mrd_logging import StructLogger, get_logger
from .sending import LootSender

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
    return insta


def get_cookies_path(cookies_content: str | None, logger: StructLogger) -> str | None:
    """Write a cookies file and return its path, or ``None`` if no cookies can be loaded."""
    if cookies_content:
        fd, cookies = mkstemp(prefix='cookies', suffix='.txt', text=True)
        with open(fd, mode='w') as cookie_file:  # noqa: PTH123
            cookie_file.write(cookies_content)
        logger.info("Wrote cookies file", path=cookies)
        return cookies
    logger.info("No cookies found")
    return None


def load_config(path: str, logger: StructLogger) -> dict[str, str]:
    """Load NestedText config file."""
    logger.info("Loading credentials", path=path)
    return cast(dict, nt_load(path))


def main():
    """Start the bot."""
    logger = get_logger(json=False)
    config = load_config(sys.argv[1], logger)

    cookies = get_cookies_path(config.get('COOKIES'), logger)
    insta = get_insta(config.get('INSTA_USER'), config.get('INSTA_PW'), logger)
    bot = TeleBot(config['TOKEN'])
    logger.info("Initialized bot")

    timeout = 120
    loot_sender = LootSender(bot=bot, logger=logger, timeout=timeout)
    link_handlers = LinkHandlers(sender=loot_sender, logger=logger, insta=insta, cookies=cookies)

    @bot.business_message_handler(func=bool)
    @bot.message_handler(func=bool)
    def media_link_handler(message: Message):
        """Download from any URLs that we handle and upload content to the chat."""
        link_handlers.media_link_handler(message)

    bot.infinity_polling(timeout=timeout, long_polling_timeout=timeout)
