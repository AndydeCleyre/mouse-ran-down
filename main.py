#!/usr/bin/env python3
import re

from plumbum import local
from telebot import TeleBot
from telebot.types import InputFile, ReplyParameters
from yt_dlp import YoutubeDL

from credentials import TOKEN

bot = TeleBot(TOKEN)
TIKTOK_PATTERN = r'https://www\.tiktok\.com/t/[^/ ]+'


def get_tiktok_urls(message):
    urls = []
    for ent in message.entities:
        if ent.type == 'url':
            url = ent.url or message.text[ent.offset : ent.offset + ent.length]
            if re.match(TIKTOK_PATTERN, url):
                urls.append(url)
    return urls


@bot.message_handler(func=lambda m: True)
def tiktok_link_handler(message):
    # bot.reply_to(message, "MESSAGE ACKNOWLEDGED")
    urls = get_tiktok_urls(message)
    if urls:
        with local.tempdir() as tmp:
            with YoutubeDL(params={'paths': {'home': tmp}}) as ydl:
                ydl.download(urls)
            for vid in tmp // '*':
                bot.send_video(
                    chat_id=message.chat.id,
                    video=InputFile(vid),
                    reply_parameters=ReplyParameters(message_id=message.id),
                )


if __name__ == '__main__':
    bot.infinity_polling()
