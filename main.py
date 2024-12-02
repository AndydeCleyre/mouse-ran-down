#!/usr/bin/env python3
import re
from mimetypes import guess_file_type

import instaloader
import stamina
import structlog
from plumbum import local
from telebot import TeleBot
from telebot.types import InputFile, ReplyParameters
from yt_dlp import DownloadError, YoutubeDL

from credentials import TOKEN

bot = TeleBot(TOKEN)
logger = structlog.get_logger()
TIKTOK_PATTERN = r'https://www\.tiktok\.com/t/[^/ ]+'
X_PATTERN = r'https://x\.com/[^/]+/status/\d+'
INSTA_PATTERN = r'https://www\.instagram\.com/(p|reel)/(?P<shortcode>[^/]+).*'

def message_urls(message):
    if message.entities:
        for ent in message.entities:
            if ent.type == 'url':
                yield ent.url or message.text[ent.offset : ent.offset + ent.length]


def suitable_for_ytdlp(url):
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


def get_video_download_urls(message):
    return [url for url in message_urls(message) if suitable_for_ytdlp(url)]


def video_link_handler(message, urls):
    bot.send_chat_action(chat_id=message.chat.id, action='record_video')
    with local.tempdir() as tmp:
        with YoutubeDL(params={'paths': {'home': tmp}}) as ydl:
            logger.info("Downloading videos", urls=urls)
            ydl.download(urls)
        for vid in tmp // '*':
            logger.info("Uploading", video=vid)
            bot.send_chat_action(chat_id=message.chat.id, action='upload_video')
            bot.send_video(
                chat_id=message.chat.id,
                video=InputFile(vid),
                reply_parameters=ReplyParameters(message_id=message.id),
            )


def get_insta_shortcodes(message):
    # TODO: reel?
    shortcodes = []
    for url in message_urls(message):
        if match := re.match(INSTA_PATTERN, url):
            logger.info("Looks like insta", url=url)
            shortcodes.append(match['shortcode'])
    return shortcodes


def path_is_type(path, typestr):
    log = logger.bind(path=path, target_type=typestr)
    filetype, _ = guess_file_type(path, strict=False)
    if filetype:
        log.info("Identified", guessed_type=filetype)
        return filetype.startswith(typestr)
    log.info("Unidentified")
    return False


def insta_link_handler(message, shortcodes):
    bot.send_chat_action(chat_id=message.chat.id, action='record_video')
    with local.tempdir() as tmp:
        insta = instaloader.Instaloader(dirname_pattern=tmp / '{target}')
        for shortcode in shortcodes:
            logger.info("Downloading insta", shortcode=shortcode)
            insta.download_post(
                post=instaloader.Post.from_shortcode(insta.context, shortcode), target='loot'
            )
            for loot in tmp.walk(filter=lambda p: path_is_type(p, 'video')):
                logger.info("Sending insta", shortcode=shortcode, video=loot)
                bot.send_chat_action(chat_id=message.chat.id, action='upload_video')
                bot.send_video(
                    chat_id=message.chat.id,
                    video=InputFile(loot),
                    reply_parameters=ReplyParameters(message_id=message.id),
                )
            for loot in tmp.walk(filter=lambda p: path_is_type(p, 'image')):
                logger.info("Sending insta", shortcode=shortcode, image=loot)
                bot.send_chat_action(chat_id=message.chat.id, action='upload_photo')
                bot.send_photo(
                    chat_id=message.chat.id,
                    photo=InputFile(loot),
                    reply_parameters=ReplyParameters(message_id=message.id),
                )
            for loot in tmp.walk(filter=lambda p: path_is_type(p, 'text')):
                logger.info("Sending insta", shortcode=shortcode, text=loot)
                bot.send_chat_action(chat_id=message.chat.id, action='typing')
                bot.send_message(
                    chat_id=message.chat.id,
                    text=loot.read(),
                    reply_parameters=ReplyParameters(message_id=message.id),
                )


@stamina.retry(on=Exception)
@bot.message_handler(func=lambda m: True)
def media_link_handler(message):
    urls = get_video_download_urls(message)
    if urls:
        video_link_handler(message, urls)
    shortcodes = get_insta_shortcodes(message)
    if shortcodes:
        insta_link_handler(message, shortcodes)


if __name__ == '__main__':
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
