#!/usr/bin/env python3
"""Download videos from any sent popular video site links, and upload them to the chat."""

import re
from collections.abc import Callable, Iterator
from contextlib import suppress
from itertools import batched
from json import load
from mimetypes import guess_file_type
from typing import Any, TypedDict, cast

import instaloader
import stamina
import structlog
from credentials import TOKEN  # pyright: ignore [reportMissingImports]
from plumbum import LocalPath, local
from plumbum.cmd import gallery_dl
from telebot import TeleBot
from telebot.formatting import escape_html
from telebot.types import (
    InputFile,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
    Message,
    MessageEntity,
    ReplyParameters,
)
from telebot.util import smart_split
from yt_dlp import DownloadError, YoutubeDL
from yt_dlp.networking.impersonate import ImpersonateTarget

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
    'x': r'(https://x\.com/[^/]+/status/\d+|https://t.co/[^/]+)',
    'bluesky': r'https://bsky\.app/profile/[^/]+/post/[^/]+',
    'insta': r'https://www\.instagram\.com/([^/]+/)?(p|reel)/(?P<shortcode>[^/]+).*',
    'vreddit': r'https://v\.redd\.it/[^/]+',
    'reddit': r'https://www\.reddit\.com/r/[^/]+/(comments|s)/[a-zA-Z0-9_/]+',
    'youtube': (
        r'https://(youtu\.be/[^/]+'
        r'|www\.youtube\.com/shorts/[^/]+'
        r'|(www|m)\.youtube\.com/watch\?v=[^/]+)'
    ),
    'vimeo': (
        r'https://(player\.vimeo\.com/video/[^/]+'
        r'|vimeo\.com/[0-9]+[^/]*)'
    ),
}

LOOT_ACTION = {'video': 'upload_video', 'image': 'upload_photo', 'text': 'typing'}
LOOT_SEND_FUNC = {'video': bot.send_video, 'image': bot.send_photo, 'text': bot.send_message}
LOOT_SEND_KEY = {'video': 'video', 'image': 'photo', 'text': 'text'}
LOOT_WRAPPER = {'video': InputFile, 'image': InputFile, 'text': LocalPath.read}

COLLAPSE_AT_CHARS = 300
MAX_CAPTION_CHARS = 1024
MAX_MEDIA_GROUP_MEMBERS = 10
MAX_MEGABYTES = 50

TIMEOUT = 120


class LootItems(TypedDict):
    """Video, image, and text items downloaded from URLs."""

    video: list[InputFile]
    image: list[InputFile]
    text: list[str]


def get_entity_text(message_text: str, entity: MessageEntity) -> str:
    """Get the text of an entity."""
    return message_text.encode('utf-16-le')[
        entity.offset * 2 : entity.offset * 2 + entity.length * 2
    ].decode('utf-16-le')


def message_urls(message: Message) -> Iterator[str]:
    """Yield all URLs in a message."""
    if message.entities:
        for ent in message.entities:
            if ent.type == 'url':
                yield ent.url or get_entity_text(cast(str, message.text), ent)


def path_is_type(path: str, typestr: str) -> bool:
    """Return True if the path has the given file type."""
    log = logger.bind(path=path, target_type=typestr)

    filetype, _ = guess_file_type(path, strict=False)
    if not filetype:
        if path.endswith('.description'):
            filetype = 'text'
        elif path.endswith('.mkv'):
            filetype = 'video'

    if filetype:
        return filetype.startswith(typestr)

    log.info("Found unidentified file")
    return False


def str_to_collapsed_quotation_html(text: str) -> str:
    """Convert a string to an expandable quotation HTML string."""
    return f"<blockquote expandable>{escape_html(text)}</blockquote>"


def send_potentially_collapsed_text(message: Message, text: str):
    """Send text, as an expandable quotation if it's long, and split if very long."""
    for txt in smart_split(text):
        parse_mode = None
        if len(txt) >= COLLAPSE_AT_CHARS:
            txt = str_to_collapsed_quotation_html(txt)  # noqa: PLW2901
            parse_mode = 'HTML'
        bot.send_message(
            chat_id=message.chat.id,
            parse_mode=parse_mode,
            text=txt,
            reply_parameters=ReplyParameters(message_id=message.id),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )


def send_loot_items_as_media_group(message: Message, loot_items: LootItems, context: Any = None):  # noqa: ANN401
    """Send loot items as a media group."""
    bot.send_chat_action(chat_id=message.chat.id, action='upload_video')

    media_group = [InputMediaPhoto(img) for img in loot_items['image']] + [
        InputMediaVideo(vid) for vid in loot_items['video']
    ]

    text = '\n\n'.join(loot_items['text'])
    if len(text) <= MAX_CAPTION_CHARS:
        if len(text) >= COLLAPSE_AT_CHARS:
            text = str_to_collapsed_quotation_html(text)
            media_group[0].parse_mode = 'HTML'

        media_group[0].caption = text
    else:
        send_potentially_collapsed_text(message, text)

    logger.info("Uploading", loot=media_group, context=context)
    bot.send_chat_action(chat_id=message.chat.id, action='upload_video')
    bot.send_media_group(
        chat_id=message.chat.id,
        media=media_group,  # pyright: ignore [reportArgumentType]
        reply_parameters=ReplyParameters(message_id=message.id),
        timeout=TIMEOUT,
    )


def send_loot_items_individually(message: Message, loot_items: LootItems, context: Any = None):  # noqa: ANN401
    """Send loot items individually."""
    caption = None
    parse_mode = None
    if len(loot_items['video'] + loot_items['image']) == 1 and loot_items['text']:
        text = '\n\n'.join(loot_items['text'])
        if len(text) <= MAX_CAPTION_CHARS:
            if len(text) >= COLLAPSE_AT_CHARS:
                text = str_to_collapsed_quotation_html(text)
                parse_mode = 'HTML'

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
                parse_mode=parse_mode,
                reply_parameters=ReplyParameters(message_id=message.id),
                timeout=TIMEOUT,
            )


def batch_loot_items(loot_items: LootItems) -> list[LootItems]:
    """Return a list of LootItems dicts batched by maximum media group size."""
    media_items = [
        (filetype, media_item)
        for filetype in ('video', 'image')
        for media_item in loot_items[filetype]
    ]

    loot_items_batches = []
    for media_batch in batched(media_items, MAX_MEDIA_GROUP_MEMBERS, strict=False):
        loot_items_batch = {'video': [], 'image': [], 'text': []}
        for filetype, item in media_batch:
            loot_items_batch[filetype].append(item)
        loot_items_batches.append(loot_items_batch)
    if loot_items_batches:
        loot_items_batches[0]['text'].append('\n\n'.join(loot_items['text']))

    return loot_items_batches


def send_potential_media_groups(message: Message, loot_folder: LocalPath, context: Any = None):  # noqa: ANN401
    """Send all media from a directory as a reply."""
    # Regarding B023: https://github.com/astral-sh/ruff/issues/7847
    loot_items = {}
    for filetype in ('video', 'image', 'text'):
        loot_items[filetype] = [
            LOOT_WRAPPER[filetype](loot_file)
            for loot_file in loot_folder.walk(
                filter=lambda p: p.is_file() and path_is_type(p, filetype)  # noqa: B023
            )
        ]

    for loot_items_batch in batch_loot_items(cast(LootItems, loot_items)):
        if (len(loot_items_batch['video']) + len(loot_items_batch['image'])) > 1:
            send = send_loot_items_as_media_group
        else:
            send = send_loot_items_individually

        send(message, cast(LootItems, loot_items_batch), context)


def ytdlp_estimate_bytes(format_candidate: dict, duration: int | None = None) -> int | None:
    """Estimate the size of a video format candidate."""
    if size := format_candidate.get('filesize') or format_candidate.get('filesize_approx'):
        return size
    if (tbr := format_candidate.get('tbr')) and duration:
        return int(duration * tbr * (1000 / 8))
    logger.error(
        "Failed to estimate filesize",
        format_keys=list(format_candidate.keys()),
        info_duration=duration,
    )
    return None


def choose_ytdlp_format(url: str, max_height: int = 1080) -> str | None:
    """Choose the best format for the video."""
    template = 'bestvideo[height<={}]+bestaudio/best[height<={}]'
    heights = [h for h in (1080, 720, 540, 480) if h <= max_height]
    if not heights:
        heights = [max_height]

    with YoutubeDL(params={} if not COOKIES else {'cookiefile': COOKIES}) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            return None

        for height in tuple(heights):
            fmt = template.format(height, height)
            selector = ydl.build_format_selector(fmt)
            candidate_gen = selector(info)

            while True:
                try:
                    candidate = next(candidate_gen)
                except StopIteration:
                    break
                except KeyError as e:
                    logger.error(
                        "Couldn't build format selector",
                        url=url,
                        fmt=fmt,
                        exc_type=type(e),
                        exc_str=str(e),
                    )
                    continue

                logger.info(
                    "Checking candidate",
                    target_height=height,
                    format_id=candidate.get('format_id'),
                )
                estimated_bytes = ytdlp_estimate_bytes(candidate, info.get('duration'))
                if not estimated_bytes:
                    logger.error("Bad filesize data", url=url)
                    continue

                logger.info(
                    "Estimated size",
                    url=url,
                    estimated_bytes=estimated_bytes,
                    estimated_megabytes=estimated_bytes / 10**6,
                )
                if estimated_bytes / 10**6 < MAX_MEGABYTES:
                    return fmt
                heights.remove(height)

    if heights:
        return template.format(heights[0], heights[0])
    return None


@stamina.retry(on=Exception)
def ytdlp_url_handler(message: Message, url: str):
    """Download videos and upload them to the chat."""
    bot.send_chat_action(chat_id=message.chat.id, action='record_video')

    url = url.split('&', 1)[0]

    skip_download = not (vid_format := choose_ytdlp_format(url))

    with local.tempdir() as tmp:
        params = {
            'paths': {'home': tmp},
            'outtmpl': {'default': '%(id)s.%(ext)s'},
            'writethumbnail': True,
            'writedescription': True,
            'format': vid_format,
            'format_sort': ['res', 'ext:mp4:m4a'],
            'final_ext': 'mp4',
            'max_filesize': MAX_MEGABYTES * 10**6,
            'skip_download': skip_download,
            'impersonate': ImpersonateTarget(),
            'noplaylist': True,
            'playlist_items': '1:1',
            'postprocessors': [
                {'format': 'png', 'key': 'FFmpegThumbnailsConvertor', 'when': 'before_dl'},
                {'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'},
            ],
        }

        if COOKIES:
            params['cookiefile'] = COOKIES

        with YoutubeDL(params=params) as ydl:
            logger.info("Downloading video", url=url, downloader='yt-dlp')
            ydl.download([url])

        send_potential_media_groups(message, tmp, context=url)


@stamina.retry(on=Exception)
def gallerydl_url_handler(message: Message, url: str):
    """Download whatever we can and upload it to the chat."""
    bot.send_chat_action(chat_id=message.chat.id, action='typing')

    with local.tempdir() as tmp:
        logger.info("Downloading whatever", url=url, downloader='gallery-dl')

        flags = ['--directory', tmp, '--write-info-json']
        if COOKIES:
            flags += ['--cookies', COOKIES]

        gallery_dl(*flags, url)

        texts = []
        for json in tmp.walk(filter=lambda p: p.name == 'info.json'):
            for key in ('title', 'content', 'selftext'):
                with suppress(KeyError):
                    texts.append(load(json)[key])
            (json.parent / 'info.txt').write('\n\n'.join(texts))

        send_potential_media_groups(message, tmp, context=url)


@stamina.retry(on=Exception)
def insta_url_handler(message: Message, url: str):
    """Download Instagram posts and upload them to the chat."""
    bot.send_chat_action(chat_id=message.chat.id, action='record_video')

    with local.tempdir() as tmp:
        insta = instaloader.Instaloader(dirname_pattern=tmp / '{target}')
        if match := re.match(PATTERNS['insta'], url, re.IGNORECASE):
            shortcode = match.group('shortcode')
            logger.info("Downloading insta", shortcode=shortcode, downloader='instaloader')
            insta.download_post(
                post=instaloader.Post.from_shortcode(insta.context, shortcode), target='loot'
            )

            send_potential_media_groups(message, tmp, context=shortcode)


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


def get_url_handler(url: str) -> Callable | None:
    """Return the best handler for the given URL."""
    if re.match(PATTERNS['insta'], url, re.IGNORECASE):
        return insta_url_handler
    if re.match(
        f"({
            '|'.join(
                (PATTERNS['tiktok'], PATTERNS['vreddit'], PATTERNS['youtube'], PATTERNS['vimeo'])
            )
        })",
        url,
        re.IGNORECASE,
    ):
        return ytdlp_url_handler
    if re.match(
        f"({'|'.join((PATTERNS['x'], PATTERNS['reddit'], PATTERNS['bluesky']))})",
        url,
        re.IGNORECASE,
    ):
        if ytdlp_url_has_video(url):
            return ytdlp_url_handler
        return gallerydl_url_handler

    return None


def get_forced_url_handler(url: str) -> Callable:
    """Return a handler for the URL no matter what."""
    if ytdlp_url_has_video(url):
        return ytdlp_url_handler
    return gallerydl_url_handler


def bot_mentioned(message: Message) -> bool:
    """Return True if the bot was mentioned in the message."""
    target = f"@{bot.get_me().username}".casefold()
    if message.entities:
        for ent in message.entities:
            if (
                ent.type == 'mention'
                and get_entity_text(cast(str, message.text), ent).casefold() == target
            ):
                logger.info("Mentioned")
                return True
    return False


@bot.message_handler(func=bool)
def media_link_handler(message: Message):
    """Download from any URLs that we handle and upload content to the chat."""
    mentioned = bot_mentioned(message)
    for url in message_urls(message):
        handler = get_url_handler(url)
        if not handler and mentioned:
            handler = get_forced_url_handler(url)
        if handler:
            try:
                handler(message, url)
            except Exception as e:
                logger.exception("Crashed", exc_info=e)
                raise


if __name__ == '__main__':
    bot.infinity_polling(timeout=TIMEOUT, long_polling_timeout=TIMEOUT)
