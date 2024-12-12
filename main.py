#!/usr/bin/env python3
import re
from collections.abc import Iterator
from mimetypes import guess_file_type
from typing import cast, Any

import instaloader
import stamina
import structlog
from plumbum import LocalPath, local
from telebot import TeleBot
from telebot.types import InputMediaVideo, InputMediaPhoto, InputFile, Message, ReplyParameters
from yt_dlp import DownloadError, YoutubeDL

from credentials import TOKEN

bot = TeleBot(TOKEN)
logger = structlog.get_logger()
TIKTOK_PATTERN = r'https://www\.tiktok\.com/t/[^/ ]+'
X_PATTERN = r'https://x\.com/[^/]+/status/\d+'
INSTA_PATTERN = r'https://www\.instagram\.com/(p|reel)/(?P<shortcode>[^/]+).*'

LOOT_ACTION = {'video': 'upload_video', 'image': 'upload_photo', 'text': 'typing'}
LOOT_SEND_FUNC = {'video': bot.send_video, 'image': bot.send_photo, 'text': bot.send_message}
LOOT_SEND_KEY = {'video': 'video', 'image': 'photo', 'text': 'text'}
LOOT_WRAPPER = {'video': InputFile, 'image': InputFile, 'text': LocalPath.read}


def message_urls(message: Message) -> Iterator[str]:
    """Yield all URLs in a message."""
    if message.entities:
        for ent in message.entities:
            if ent.type == 'url':
                yield ent.url or cast(str, message.text)[ent.offset : ent.offset + ent.length]


def suitable_for_ytdlp(url: str) -> bool:
    """Return True if the URL target has a yt-dlp-downloadable video."""
    log = logger.bind(url=url)
    if re.match(TIKTOK_PATTERN, url):
        log.info("Looks like tiktok")
        return True
    if re.match(X_PATTERN, url):
        with YoutubeDL() as ydl:
            try:
                ydl.extract_info(url, download=False)
            except DownloadError:
                log.info("Looks like twitter without video")
                return False
            else:
                log.info("Looks like twitter video")
                return True
    log.info("Looks unsuitable for yt-dlp")
    return False


def get_video_download_urls(message: Message) -> list[str]:
    """Return a list of URLs suitable for yt-dlp."""
    return [url for url in message_urls(message) if suitable_for_ytdlp(url)]


def path_is_type(path: str, typestr: str) -> bool:
    """Return True if the path has the given file type."""
    log = logger.bind(path=path, target_type=typestr)
    filetype, _ = guess_file_type(path, strict=False)
    if filetype:
        log.info("Identified", guessed_type=filetype)
        return filetype.startswith(typestr)
    log.info("Unidentified")
    return False


def send_potential_media_group(message: Message, loot_folder: LocalPath, context: Any = None):
    """Send all media from a directory as a reply."""
    log = logger.bind(context=context)
    loot_items = {}
    for filetype in ('video', 'image', 'text'):
        loot_items[filetype] = [
            LOOT_WRAPPER[filetype](loot)
            for loot in loot_folder.walk(filter=lambda p: path_is_type(p, filetype))
        ]
    if 1 < (len(loot_items['video']) + len(loot_items['image'])) < 11:
        bot.send_chat_action(chat_id=message.chat.id, action='upload_video')
        media_group = [InputMediaPhoto(img) for img in loot_items['image']] + [
            InputMediaVideo(vid) for vid in loot_items['video']
        ]
        media_group[0].caption = '\n\n'.join(loot_items['text'])
        log.info("Uploading", loot=media_group)
        bot.send_media_group(
            chat_id=message.chat.id,
            media=media_group,  # pyright: ignore [reportArgumentType]
            reply_parameters=ReplyParameters(message_id=message.id),
        )
    else:
        for filetype, items in loot_items.items():
            for loot in items:
                bot.send_chat_action(chat_id=message.chat.id, action=LOOT_ACTION[filetype])
                log.info("Uploading", loot=loot)
                LOOT_SEND_FUNC[filetype](
                    chat_id=message.chat.id,
                    **{LOOT_SEND_KEY[filetype]: loot},
                    reply_parameters=ReplyParameters(message_id=message.id),
                )


def video_link_handler(message: Message, urls: list[str]):
    """Download videos and upload them to the chat."""
    bot.send_chat_action(chat_id=message.chat.id, action='record_video')
    with local.tempdir() as tmp:
        with YoutubeDL(params={'paths': {'home': tmp}}) as ydl:
            logger.info("Downloading videos", urls=urls)
            ydl.download(urls)
        send_potential_media_group(message, tmp, context=urls)


def get_insta_shortcodes(message: Message) -> list[str]:
    """Return a list of Instagram shortcodes in a message."""
    shortcodes = []
    for url in message_urls(message):
        if match := re.match(INSTA_PATTERN, url):
            logger.info("Looks like insta", url=url)
            shortcodes.append(match['shortcode'])
    return shortcodes


def insta_link_handler(message: Message, shortcodes: list[str]):
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


@stamina.retry(on=Exception)
@bot.message_handler(func=lambda m: True)
def media_link_handler(message: Message):
    """Download from any URLs that we handle and upload content to the chat ."""
    for extractor, handler in (
        (get_video_download_urls, video_link_handler),
        (get_insta_shortcodes, insta_link_handler),
    ):
        loot_ids = extractor(message)
        if loot_ids:
            handler(message, loot_ids)


if __name__ == '__main__':
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
