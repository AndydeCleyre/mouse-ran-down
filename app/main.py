#!/usr/bin/env python3
"""Download videos from any sent instagram/reddit/bluesky/x links, and upload them to the chat."""

import re
from collections.abc import Iterator
from mimetypes import guess_file_type
from typing import Any, TypedDict, cast

import instaloader
import stamina
import structlog
from credentials import TOKEN  # pyright: ignore [reportMissingImports]
from plumbum import LocalPath, local
from telebot import TeleBot
from telebot.formatting import escape_html
from telebot.types import InputFile, InputMediaPhoto, InputMediaVideo, Message, ReplyParameters
from telebot.util import smart_split
from yt_dlp import DownloadError, YoutubeDL

try:
    from credentials import COOKIES  # pyright: ignore [reportMissingImports]

    (local.path(__file__).up() / 'cookies.txt').write(COOKIES)
    COOKIES = str(local.path(__file__).up() / 'cookies.txt')
except ImportError:
    COOKIES = None


structlog.configure(
    processors=[
        structlog.processors.dict_tracebacks,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt='iso'),
        structlog.processors.JSONRenderer(sort_keys=True),
    ]
)
logger = structlog.get_logger()

bot = TeleBot(TOKEN)
PATTERNS = {
    'tiktok': (
        r'https://(www\.tiktok\.com/'
        r'(t/[^/ ]+|@[^/]+/video/\d+|@[^\?]+[^/]+)'
        r'|vm\.tiktok\.com/[^/]+)'
    ),
    'x': r'https://x\.com/[^/]+/status/\d+',
    'bluesky': r'https://bsky\.app/profile/[^/]+/post/[^/]+',
    'insta': r'https://www\.instagram\.com/([^/]+/)?(p|reel)/(?P<shortcode>[^/]+).*',
    'vreddit': r'https://v\.redd\.it/[^/]+',
    'reddit': r'https://www\.reddit\.com/r/[^/]+/comments/[a-zA-Z0-9_/]+',
}

LOOT_ACTION = {'video': 'upload_video', 'image': 'upload_photo', 'text': 'typing'}
LOOT_SEND_FUNC = {'video': bot.send_video, 'image': bot.send_photo, 'text': bot.send_message}
LOOT_SEND_KEY = {'video': 'video', 'image': 'photo', 'text': 'text'}
LOOT_WRAPPER = {'video': InputFile, 'image': InputFile, 'text': LocalPath.read}

MAX_CAPTION_CHARS = 1024
MAX_MEDIA_GROUP_MEMBERS = 10


class LootItems(TypedDict):
    """Video, image, and text items downloaded from URLs."""

    video: list[InputFile]
    image: list[InputFile]
    text: list[str]


def message_urls(message: Message) -> Iterator[str]:
    """Yield all URLs in a message."""
    if message.entities:
        for ent in message.entities:
            logger.debug(
                "Entity", type=ent.type, url=ent.url, offset=ent.offset, length=ent.length
            )
            if ent.type == 'url':
                yield ent.url or cast(str, message.text).encode('utf-16-le')[
                    ent.offset * 2 : ent.offset * 2 + ent.length * 2
                ].decode('utf-16-le')


@stamina.retry(on=Exception)
def ytdlp_url_has_video(url: str) -> bool:
    """Return True if the yt-dlp-suitable URL really has a video."""
    log = logger.bind(url=url)
    params = {}
    if COOKIES:
        params['cookiefile'] = COOKIES
    with YoutubeDL(params=params) as ydl:
        try:
            ydl.extract_info(url, download=False)
        except DownloadError:
            log.info("Video not found")
            return False
        else:
            log.info("Video found")
            return True


def suitable_for_ytdlp(url: str) -> bool:
    """Return True if the URL target has a yt-dlp-downloadable video."""
    log = logger.bind(url=url)
    if re.match(f"({'|'.join((PATTERNS['tiktok'], PATTERNS['vreddit']))})", url, re.IGNORECASE):
        log.info("Looks suitable for yt-dlp")
        return True
    if re.match(
        f"({'|'.join((PATTERNS['x'], PATTERNS['reddit'], PATTERNS['bluesky']))})",
        url,
        re.IGNORECASE,
    ):
        log.info("Looks potentially suitable for yt-dlp")
        return ytdlp_url_has_video(url)
    log.info("Looks unsuitable for yt-dlp")
    return False


def get_ytdlp_download_urls(message: Message) -> list[str]:
    """Return a list of URLs suitable for yt-dlp."""
    return [url for url in message_urls(message) if suitable_for_ytdlp(url)]


def path_is_type(path: str, typestr: str) -> bool:
    """Return True if the path has the given file type."""
    log = logger.bind(path=path, target_type=typestr)
    filetype, _ = guess_file_type(path, strict=False)
    if filetype:
        if not re.match(r'(video|image|text)', filetype):
            log.info("Unhandled filetype", guessed_type=filetype)
        return filetype.startswith(typestr)
    if typestr == 'text' and path.endswith('.description'):
        return True
    log.info("Unidentified")
    return False


def str_to_collapsed_quotation_html(text: str) -> str:
    """Convert a string to an expandable quotation HTML string."""
    return f"<blockquote expandable>{escape_html(text)}</blockquote>"


def send_potentially_collapsed_text(message: Message, text: str):
    """Send text, as an expandable quotation if it's long, and split if very long."""
    for txt in smart_split(text):
        parse_mode = None
        if len(txt) > MAX_CAPTION_CHARS:
            txt = str_to_collapsed_quotation_html(txt)  # noqa: PLW2901
            parse_mode = 'HTML'
        bot.send_message(
            chat_id=message.chat.id,
            parse_mode=parse_mode,
            text=txt,
            reply_parameters=ReplyParameters(message_id=message.id),
        )


@stamina.retry(on=Exception)
def send_loot_items_as_media_group(message: Message, loot_items: LootItems, context: Any = None):  # noqa: ANN401
    """Send loot items as a media group."""
    bot.send_chat_action(chat_id=message.chat.id, action='upload_video')
    media_group = [InputMediaPhoto(img) for img in loot_items['image']] + [
        InputMediaVideo(vid) for vid in loot_items['video']
    ]

    text = '\n\n'.join(loot_items['text'])
    if len(text) <= MAX_CAPTION_CHARS:
        media_group[0].caption = text
    else:
        send_potentially_collapsed_text(message, text)

    logger.info("Uploading", loot=media_group, context=context)
    bot.send_chat_action(chat_id=message.chat.id, action='upload_video')
    bot.send_media_group(
        chat_id=message.chat.id,
        media=media_group,  # pyright: ignore [reportArgumentType]
        reply_parameters=ReplyParameters(message_id=message.id),
    )


@stamina.retry(on=Exception)
def send_loot_items_individually(message: Message, loot_items: LootItems, context: Any = None):  # noqa: ANN401
    """Send loot items individually."""
    caption = None
    if len(loot_items['video'] + loot_items['image']) == 1 and loot_items['text']:
        text = '\n\n'.join(loot_items['text'])
        if len(text) <= MAX_CAPTION_CHARS:
            caption = text
            loot_items['text'] = []
    for filetype, items in loot_items.items():
        for loot in cast(list, items):
            bot.send_chat_action(chat_id=message.chat.id, action=LOOT_ACTION[filetype])
            logger.info("Uploading", loot=loot, context=context)
            if filetype == 'text':
                send_potentially_collapsed_text(message, loot)
                continue
            LOOT_SEND_FUNC[filetype](
                chat_id=message.chat.id,
                **{LOOT_SEND_KEY[filetype]: loot},
                caption=caption,
                reply_parameters=ReplyParameters(message_id=message.id),
            )


def send_potential_media_group(message: Message, loot_folder: LocalPath, context: Any = None):  # noqa: ANN401
    """Send all media from a directory as a reply."""
    # Regarding B023: https://github.com/astral-sh/ruff/issues/7847
    loot_items = {}
    for filetype in ('video', 'image', 'text'):
        loot_items[filetype] = [
            LOOT_WRAPPER[filetype](loot)
            for loot in loot_folder.walk(
                filter=lambda p: p.is_file() and path_is_type(p, filetype)  # noqa: B023
            )
        ]
    if 1 < (len(loot_items['video']) + len(loot_items['image'])) <= MAX_MEDIA_GROUP_MEMBERS:
        send = send_loot_items_as_media_group
    else:
        send = send_loot_items_individually
    send(message, cast(LootItems, loot_items), context)


@stamina.retry(on=Exception)
def ytdlp_url_handler(message: Message, urls: list[str]):
    """Download videos and upload them to the chat."""
    bot.send_chat_action(chat_id=message.chat.id, action='record_video')
    with local.tempdir() as tmp:
        params = {
            'paths': {'home': tmp},
            'outtmpl': {'default': '%(id)s.%(ext)s'},
            'writethumbnail': True,
            'writedescription': True,
        }
        if COOKIES:
            params['cookiefile'] = COOKIES
        with YoutubeDL(params=params) as ydl:
            logger.info("Downloading videos", urls=urls)
            ydl.download(urls)
        send_potential_media_group(message, tmp, context=urls)


def get_insta_shortcodes(message: Message) -> list[str]:
    """Return a list of Instagram shortcodes in a message."""
    shortcodes = []
    for url in message_urls(message):
        if match := re.match(PATTERNS['insta'], url, re.IGNORECASE):
            logger.info("Looks like insta", url=url)
            shortcodes.append(match['shortcode'])
    return shortcodes


@stamina.retry(on=Exception)
def insta_shortcode_handler(message: Message, shortcodes: list[str]):
    """Download Instagram posts and upload them to the chat."""
    bot.send_chat_action(chat_id=message.chat.id, action='record_video')
    with local.tempdir() as tmp:
        insta = instaloader.Instaloader(dirname_pattern=tmp / '{target}')
        for shortcode in shortcodes:
            logger.info("Downloading insta", shortcode=shortcode)
            insta.download_post(
                post=instaloader.Post.from_shortcode(insta.context, shortcode), target='loot'
            )
            send_potential_media_group(message, tmp, context=shortcode)


@bot.message_handler(func=bool)
def media_link_handler(message: Message):
    """Download from any URLs that we handle and upload content to the chat ."""
    for extractor, handler in (
        (get_ytdlp_download_urls, ytdlp_url_handler),
        (get_insta_shortcodes, insta_shortcode_handler),
    ):
        try:
            loot_ids = extractor(message)
            if loot_ids:
                handler(message, loot_ids)
        except Exception as e:
            logger.exception("Crashed", exc_info=e)
            raise


if __name__ == '__main__':
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
