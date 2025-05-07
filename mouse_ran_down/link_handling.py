"""LinkHandlers, sharing a bot sender, logger, patterns, cookies, logins, and constraints."""

from __future__ import annotations

import re
from contextlib import suppress
from http.cookiejar import MozillaCookieJar
from json import load
from mimetypes import guess_file_type  # You'd better install mailcap!
from typing import TYPE_CHECKING, Any, Literal, cast

import instaloader
import stamina
from html2text import html2text
from instaloader.exceptions import BadResponseException, ConnectionException
from plumbum import ProcessExecutionError, local
from plumbum.cmd import gallery_dl
from yt_dlp import DownloadError, YoutubeDL
from yt_dlp.networking.impersonate import ImpersonateTarget

from .mrd_logging import StructLogger, get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from instagrapi import Client as InstaClient
    from instagrapi.types import Media as InstaMedia
    from telebot.types import Message, MessageEntity

    from .sending import LootSender


def get_entity_text(message_text: str, entity: MessageEntity) -> str:
    """Get the text of an entity."""
    return message_text.encode('utf-16-le')[
        entity.offset * 2 : entity.offset * 2 + entity.length * 2
    ].decode('utf-16-le')


def message_urls(message: Message) -> Iterator[str]:
    """Yield all URLs in a message."""
    if message.entities:
        for ent in message.entities:
            if ent.type in ('url', 'text_link'):
                yield ent.url or get_entity_text(cast(str, message.text), ent)


class LinkHandlers:
    """Link handlers, sharing a bot sender, logger, patterns, cookies, logins, and constraints."""

    def __init__(
        self,
        sender: LootSender,
        logger: StructLogger | None = None,
        max_megabytes: int = 50,
        patterns: dict[str, str] | None = None,
        cookies: str | None = None,
        insta: InstaClient | None = None,
    ):
        """
        Initialize the link handlers.

        :param sender: The message sender to use.
        :param logger: The logger to use.
        :param max_megabytes: The maximum number of megabytes we can upload.
        :param patterns: The regex patterns to use for matching URLs (``{sitename: regex, ...}``).
        :param cookies: The path to the cookies file, in Netscape format.
        :param insta: The instagrapi client to use. If ``None``, will still try to use InstaLoader.
        """
        self.sender = sender
        self.max_megabytes = max_megabytes
        self.cookies = cookies
        self.insta = insta
        self.logger = logger or get_logger()
        self.patterns = patterns or {
            'tiktok': (
                r'https://(www\.tiktok\.com/'
                r'(t/[^/ ]+|@[^/]+/video/\d+|@[^\?]+[^/]+)'
                r'|vm\.tiktok\.com/[^/]+)'
            ),
            'x': r'(https://x\.com/[^/]+/status/\d+|https://t.co/[^/]+)',
            'bluesky': r'https://bsky\.app/profile/[^/]+/post/[^/]+',
            'insta': r'https://www\.instagram\.com/([^/]+/)?(p|reel)/(?P<shortcode>[^/]+).*',
            'vreddit': r'https://v\.redd\.it/[^/]+',
            'reddit': r'https://www\.reddit\.com/(r|user)/[^/]+/(comments|s)/[a-zA-Z0-9_/]+',
            'youtube': (
                r'https://(youtu\.be/[^/]+'
                r'|(www\.)?youtube\.com/shorts/[^/]+'
                r'|(www|m)\.youtube\.com/watch\?v=[^/]+)'
            ),
            'vimeo': (
                r'https://(player\.vimeo\.com/video/[^/]+'
                r'|vimeo\.com/[0-9]+[^/]*)'
            ),
            'soundcloud': r'https://soundcloud\.com/[^/]+/[^/]+',
            'bandcamp': r'https://[^\.]+\.bandcamp\.com/track/.*',
            'mastodon': r'https://mastodon\.social/@[^/]+/\d+',
        }

    def bot_mentioned(self, message: Message) -> bool:
        """Return True if the bot was mentioned in the message."""
        target = f"@{self.sender.bot.get_me().username}".casefold()
        if message.entities:
            for ent in message.entities:
                if (
                    ent.type == 'mention'
                    and get_entity_text(cast(str, message.text), ent).casefold() == target
                ):
                    self.logger.info("Mentioned")
                    return True
        return False

    def media_link_handler(self, message: Message):
        """Download from any URLs that we handle and upload content to the chat."""
        mentioned = self.bot_mentioned(message)
        for url in message_urls(message):
            log = self.logger.bind(url=url)
            handler = self.get_url_handler(url)
            if not handler and mentioned:
                self.sender.react(message, '🫡')
                handler = self.get_forced_url_handler(url)
            log.info("Chose URL handler", handler=handler.__name__ if handler else None)
            if handler:
                self.sender.react(message, '🫡')
                try:
                    handler(message, url)
                except Exception as e:
                    self.logger.error("Crashed", exc_info=e)
                    self.sender.react(message, '😢')
                    raise
                else:
                    self.sender.react(message, '😎')

    def ytdlp_url_handler_modify_and_retry(
        self,
        message: Message,
        url: str,
        *,
        media_type: Literal['video', 'audio'],
        heights: list | None,
        ignore_cookies: bool,
        embed_thumbnail: bool,
    ):
        """
        Desperately try some variant settings to download media after a failure.

        The passed args represent the *originally used* settings,
        some of which will be changed in these subsequent attempts.
        """
        try_without_cookies = self.cookies and not ignore_cookies
        try_without_thumb = embed_thumbnail

        if try_without_cookies:
            self.logger.info("Trying without cookies", url=url)
            try:
                self.ytdlp_url_handler(
                    message,
                    url,
                    media_type=media_type,
                    heights=heights,
                    ignore_cookies=True,
                    embed_thumbnail=embed_thumbnail,
                    already_desperate=True,
                )
            except DownloadError:
                if not try_without_thumb:
                    raise
            else:
                try_without_thumb = False

        if try_without_thumb:
            self.logger.info("Trying without embedding thumbnail", url=url)
            self.ytdlp_url_handler(
                message,
                url,
                media_type=media_type,
                heights=heights,
                ignore_cookies=ignore_cookies,
                embed_thumbnail=False,
                already_desperate=True,
            )

    def get_ytdlp_params(
        self,
        *,
        media_format: str,
        media_type: Literal['video', 'audio'],
        embed_thumbnail: bool,
        ignore_cookies: bool,
        folder: str,
    ) -> dict[str, Any]:
        """Get the parameters for ytdlp."""
        params = {
            'outtmpl': {'default': '%(id)s.%(ext)s'},
            'writethumbnail': True,
            'writedescription': True,
            'writesubtitles': True,
            'format': media_format,
            'final_ext': 'mp4' if media_type == 'video' else 'mp3',
            'max_filesize': self.max_megabytes * 10**6,
            'impersonate': ImpersonateTarget(),
            'noplaylist': True,
            'playlist_items': '1:1',
            'quiet': True,
            'postprocessors': [
                {'format': 'png', 'key': 'FFmpegThumbnailsConvertor', 'when': 'before_dl'},
                {'already_have_subtitle': False, 'key': 'FFmpegEmbedSubtitle'},
                {
                    'add_chapters': True,
                    'add_infojson': 'if_exists',
                    'add_metadata': True,
                    'key': 'FFmpegMetadata',
                },
                {'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}
                if media_type == 'video'
                else {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '128',
                },
            ],
        }

        if embed_thumbnail:
            params['postprocessors'].append(
                {'already_have_thumbnail': True, 'key': 'EmbedThumbnail'}
            )

        if media_type == 'video':
            params['merge_output_format'] = 'mp4'

        if self.cookies and not ignore_cookies:
            params['cookiefile'] = self.cookies

        params['paths'] = {'home': folder}

        return params

    def ytdlp_url_handler(  # noqa: PLR0913
        self,
        message: Message,
        url: str,
        *,
        media_type: Literal['video', 'audio'] = 'video',
        heights: list | None = None,
        ignore_cookies: bool = False,
        embed_thumbnail: bool = True,
        already_desperate: bool = False,
    ):
        """Download media and upload to the chat."""
        self.sender.announce_action(message=message, action=f"record_{media_type}")  # pyright: ignore [reportArgumentType]

        url = url.split('&', 1)[0]

        heights = [1080, 720, 540, 480, 360, 240, 144] if heights is None else heights
        media_format = (
            f'bestvideo[height<={heights[0]}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={heights[0]}]+bestaudio/best[height<={heights[0]}]'
            if media_type == 'video'
            else 'bestaudio[ext=mp3]/bestaudio'
        )

        with local.tempdir() as tmp:
            params = self.get_ytdlp_params(
                media_format=media_format,
                media_type=media_type,
                embed_thumbnail=embed_thumbnail,
                ignore_cookies=ignore_cookies,
                folder=str(tmp),
            )

            with YoutubeDL(params=params) as ydl:
                self.logger.info(
                    "Downloading",
                    media_type=media_type,
                    url=url,
                    downloader='yt-dlp',
                    format=media_format,
                )
                try:
                    ydl.download([url])
                except DownloadError as e:
                    self.logger.error(
                        "Failed to download", media_type=media_type, url=url, exc_info=e
                    )
                    if not already_desperate:
                        self.ytdlp_url_handler_modify_and_retry(
                            message=message,
                            url=url,
                            media_type=media_type,
                            heights=heights,
                            ignore_cookies=ignore_cookies,
                            embed_thumbnail=embed_thumbnail,
                        )
                        return
                    raise
                sizes_mb = [f.stat().st_size / 1000000 for f in tmp.list()]
                if (
                    (tmp // '*.part')
                    or (media_type == 'video' and not (tmp // '*.mp4'))
                    or any(mb > self.max_megabytes for mb in sizes_mb)
                ):
                    self.logger.error(
                        "Partial or oversized file(s) detected -- Bigger than expected?",
                        files=tmp.list(),
                        sizes_mb=sizes_mb,
                        url=url,
                        media_format=media_format,
                    )
                    if (heights := heights[1:]) and media_type == 'video':
                        self.ytdlp_url_handler(
                            message,
                            url,
                            media_type=media_type,
                            heights=heights,
                            ignore_cookies=ignore_cookies,
                            embed_thumbnail=embed_thumbnail,
                            already_desperate=already_desperate,
                        )
                    else:
                        raise DownloadError(f"Exhausted all resolutions for URL: {url}")
                    return

            self.sender.send_potential_media_groups(message, tmp, context=url)

    def ytdlp_url_handler_audio(self, message: Message, url: str):
        """Download audio files and upload them to the chat."""
        self.ytdlp_url_handler(message, url, media_type='audio')

    def gallerydl_url_handler(self, message: Message, url: str):
        """Download whatever we can and upload it to the chat."""
        self.sender.announce_action(message=message, action='typing')

        with local.tempdir() as tmp:
            self.logger.info("Downloading whatever", url=url, downloader='gallery-dl')

            flags = [
                '--directory',
                tmp,
                '--write-info-json',
                '--option',
                'extractor.twitter.text-tweets=true',
                '--option',
                'extractor.twitter.quoted=true',  # This may not work
                '--option',
                'extractor.twitter.retweets=true',  # This may not work
                '--option',
                'extractor.twitter.twitpic=true',
                '--option',
                'extractor.bluesky.quoted=true',
                '--option',
                'extractor.bluesky.reposts=true',
                '--quiet',
            ]
            if self.cookies:
                flags += ['--cookies', self.cookies]

            try:
                # TODO: redirect stderr, capture, and log
                gallery_dl(*flags, url)
            except ProcessExecutionError as e:
                self.logger.error(
                    "Failed to download", exc_info=e, url=url, downloader='gallery-dl'
                )
                raise

            texts = []
            for json in tmp.walk(filter=lambda p: p.name == 'info.json'):
                data = load(json)
                for key in ('title', 'selftext', 'text'):
                    with suppress(KeyError):
                        texts.append(data[key])
                for key in ('content',):
                    with suppress(KeyError):
                        self.logger.debug(
                            "Key: content -- is it HTML?", content=data[key], url=url
                        )
                        texts.append(html2text(data[key]))
                # TODO: recurse when data['embed']['$type'] == 'app.bsky.embed.record'
            (tmp / 'json_info.txt').write('\n\n'.join(texts))

            self.sender.send_potential_media_groups(message, tmp, context=url)

    @stamina.retry(on=Exception)
    def insta_url_handler_instaloader(self, message: Message, url: str):
        """Download Instagram posts and upload them to the chat."""
        self.sender.announce_action(message=message, action='record_video')
        log = self.logger.bind(downloader='instaloader')

        with local.tempdir() as tmp:
            insta = instaloader.Instaloader(dirname_pattern=tmp / '{target}')

            if self.cookies:
                insta.context.update_cookies(MozillaCookieJar(filename=self.cookies))

            if match := re.match(self.patterns['insta'], url, re.IGNORECASE):
                shortcode = match.group('shortcode')
                log = log.bind(shortcode=shortcode)
                try:
                    post = instaloader.Post.from_shortcode(insta.context, shortcode)
                except (BadResponseException, ConnectionException) as e:
                    log.error("Bad instagram response", exception=str(e))
                    self.gallerydl_url_handler(message, url)
                else:
                    log.info("Downloading insta")
                    # TODO: redirect stderr, capture, and log
                    insta.download_post(post=post, target='loot')

                    self.sender.send_potential_media_groups(message, tmp, context=shortcode)

    def instagrapi_downloader(self, post_info: InstaMedia) -> Callable | None:
        """
        Return a function that downloads Instagram posts.

        It takes a post ID (int) and folder (Path/str).
        """
        if not self.insta:
            return None
        downloader = None

        media_type = post_info.media_type

        if media_type == 8:  # noqa: PLR2004
            downloader = self.insta.album_download
        elif media_type == 1:
            downloader = self.insta.photo_download
        elif media_type == 2:  # noqa: PLR2004
            product_type = post_info.product_type
            if product_type == 'feed':
                downloader = self.insta.video_download
            elif product_type == 'igtv':
                downloader = self.insta.igtv_download
            elif product_type == 'clips':
                downloader = self.insta.clip_download
        return downloader

    @stamina.retry(on=Exception)
    def insta_url_handler(self, message: Message, url: str):
        """Download Instagram posts and upload them to the chat."""
        if not self.insta:
            self.insta_url_handler_instaloader(message, url)
            return

        self.sender.announce_action(message=message, action='record_video')
        log = self.logger.bind(downloader='instagrapi')

        post_id = self.insta.media_pk_from_url(url)
        post_info = self.insta.media_info(post_id)

        download = self.instagrapi_downloader(post_info)
        if not download:
            log.error("Unknown media type", post_info=post_info)
            self.insta_url_handler_instaloader(message, url)
            return

        log.info("Downloading insta")
        with local.tempdir() as tmp:
            try:
                # TODO: redirect stderr, capture, and log
                download(int(post_id), folder=tmp)  # pyright: ignore [reportArgumentType]
            except Exception as e:
                log.error("Instagrapi failed", exc_info=e)
                self.insta_url_handler_instaloader(message, url)
                return
            self.sender.send_potential_media_groups(message, tmp, context=url)

    @stamina.retry(on=Exception)
    def ytdlp_get_extensions(self, url: str, *, ignore_cookies: bool = False) -> list[str]:
        """Return a list of media file extensions available at the URL."""
        log = self.logger.bind(url=url)

        params = {}
        if self.cookies and not ignore_cookies:
            params['cookiefile'] = self.cookies

        with YoutubeDL(params=params) as ydl:
            try:
                # TODO: redirect stderr, capture, and log?
                info = ydl.extract_info(url, download=False)
            except DownloadError:
                log.info("Media not found")
                if not ignore_cookies and self.cookies:
                    log.info("Checking once more without cookies")
                    return self.ytdlp_get_extensions(url, ignore_cookies=True)
                return []
            else:
                if info:
                    log.info("Media found")
                    return [*{f['ext'] for f in info['formats']}]
                return []

    def matches_any(self, url: str, *pattern_names: str) -> bool:
        """Return True if the URL matches any of the given named patterns from PATTERNS."""
        return bool(
            re.match(
                f"{'|'.join(self.patterns[name] for name in pattern_names)}", url, re.IGNORECASE
            )
        )

    def get_forced_url_handler(self, url: str) -> Callable:
        """Return a handler for the URL no matter what."""
        if extensions := self.ytdlp_get_extensions(url):
            self.logger.info("Found media extensions", extensions=extensions, url=url)
            media = set()
            for e in extensions:
                if ft := guess_file_type(f"file.{e}", strict=False)[0]:
                    media.add(ft.split('/', 1)[0])
                else:
                    media.add(e)
            if unknown_extensions := media - {'video', 'audio'}:
                self.logger.warning(
                    "Unknown extensions", unknown_extensions=unknown_extensions, url=url
                )
            if 'video' not in media and 'audio' in media:
                return self.ytdlp_url_handler_audio
            return self.ytdlp_url_handler
        return self.gallerydl_url_handler

    def get_url_handler(self, url: str) -> Callable | None:
        """Return the best handler for the given URL."""
        if self.matches_any(url, 'insta'):
            return self.insta_url_handler
        if self.matches_any(url, 'tiktok', 'vreddit', 'youtube', 'vimeo'):
            return self.ytdlp_url_handler
        if self.matches_any(url, 'x', 'reddit', 'bluesky', 'mastodon'):
            return self.get_forced_url_handler(url)
        if self.matches_any(url, 'soundcloud', 'bandcamp'):
            return self.ytdlp_url_handler_audio

        return None
