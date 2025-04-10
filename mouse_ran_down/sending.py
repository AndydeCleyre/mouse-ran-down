"""LootSender, for sending Telegram attachments."""

from __future__ import annotations

from collections import defaultdict
from itertools import batched
from mimetypes import guess_file_type  # You'd better install mailcap!
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast

from plumbum import LocalPath
from telebot.formatting import escape_html
from telebot.types import (
    InputFile,
    InputMediaAudio,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
    Message,
    ReplyParameters,
)
from telebot.util import smart_split

from .mrd_logging import StructLogger, get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from telebot import TeleBot


Action = Literal[
    'typing', 'record_video', 'record_voice', 'upload_voice', 'upload_video', 'upload_photo'
]
LootType = Literal['video', 'audio', 'image', 'text']
MediaGroup = list[InputMediaPhoto | InputMediaVideo] | list[InputMediaAudio]


class LootItems(TypedDict):
    """Video, audio, image, and text items downloaded from URLs."""

    video: list[InputFile]
    audio: list[InputFile]
    image: list[InputFile]
    text: list[str]


def str_to_collapsed_quotation_html(text: str) -> str:
    """Convert a string to an expandable quotation HTML string."""
    return f"<blockquote expandable>{escape_html(text)}</blockquote>"


class LootSender:
    """Sending functions for Telegram attachments, with a bot object, logger, and constraints."""

    def __init__(
        self,
        bot: TeleBot,
        logger: StructLogger | None = None,
        collapse_at_chars: int = 300,
        max_caption_chars: int = 1024,
        max_media_group_members: int = 10,
        timeout: int = 120,
    ):
        """Initialize the loot sender."""
        self.bot = bot
        self.logger = logger or get_logger()
        self.collapse_at_chars = collapse_at_chars
        self.max_caption_chars = max_caption_chars
        self.max_media_group_members = max_media_group_members
        self.timeout = timeout
        self.loot_wrapper: dict[LootType, Callable] = defaultdict(lambda: InputFile)
        self.loot_wrapper['text'] = LocalPath.read
        self.loot_action: dict[LootType, Action] = {
            'video': 'upload_video',
            'audio': 'upload_voice',
            'image': 'upload_photo',
            'text': 'typing',
        }
        self.loot_send_func: dict[LootType, Callable] = {
            'video': self.bot.send_video,
            'audio': self.bot.send_audio,
            'image': self.bot.send_photo,
            'text': self.bot.send_message,
        }
        self.loot_send_key: dict[LootType, Literal['video', 'audio', 'photo', 'text']] = {
            'video': 'video',
            'audio': 'audio',
            'image': 'photo',
            'text': 'text',
        }

    def send_reply_text(self, message: Message, text: str, **params: Any):  # noqa: ANN401
        """Send text message as a reply, with link previews disabled by default."""
        params = {
            'chat_id': message.chat.id,
            'reply_parameters': ReplyParameters(message_id=message.id),
            'business_connection_id': message.business_connection_id,
            'link_preview_options': LinkPreviewOptions(is_disabled=True),
            'text': text,
            **params,
        }
        self.bot.send_message(**params)

    def send_media_group(self, message: Message, media_group: MediaGroup, **params: Any):  # noqa: ANN401
        """Send media group as a reply."""
        params = {
            'chat_id': message.chat.id,
            'reply_parameters': ReplyParameters(message_id=message.id),
            'business_connection_id': message.business_connection_id,
            'timeout': self.timeout,
            'media': media_group,
            **params,
        }
        self.bot.send_media_group(**params)

    def send_potentially_collapsed_text(self, message: Message, text: str):
        """Send text, as an expandable quotation if it's long, and split if very long."""
        for txt in smart_split(text):
            parse_mode = None
            text = txt
            if len(txt) >= self.collapse_at_chars:
                text = str_to_collapsed_quotation_html(txt)
                parse_mode = 'HTML'

            self.send_reply_text(message=message, text=text, parse_mode=parse_mode)

    def send_action(self, message: Message, action: Action):
        """Send chat action status."""
        self.bot.send_chat_action(
            chat_id=message.chat.id,
            action=action,
            business_connection_id=message.business_connection_id,
        )

    def send_loot_items_as_media_group(
        self,
        message: Message,
        loot_items: LootItems,
        context: Any = None,  # noqa: ANN401
    ):
        """Send loot items as a media group."""
        self.send_action(message=message, action='upload_video')

        media_group = (
            [InputMediaPhoto(img) for img in loot_items['image']]
            + [InputMediaVideo(vid) for vid in loot_items['video']]
            + [InputMediaAudio(aud) for aud in loot_items['audio']]
        )

        text = '\n\n'.join(loot_items['text'])
        if len(text) <= self.max_caption_chars:
            if len(text) >= self.collapse_at_chars:
                text = str_to_collapsed_quotation_html(text)
                media_group[0].parse_mode = 'HTML'

            media_group[0].caption = text
        else:
            self.send_potentially_collapsed_text(message, text)

        self.logger.info("Uploading", loot=media_group, context=context)
        self.send_action(message=message, action='upload_video')

        self.send_media_group(message=message, media_group=cast(MediaGroup, media_group))

    def send_loot_item(
        self,
        message: Message,
        loot_type: LootType,
        loot_item: InputFile | str,
        **params: Any,  # noqa: ANN401
    ):
        """Send a single loot item."""
        params = {
            'chat_id': message.chat.id,
            'caption': None,
            'parse_mode': None,
            'reply_parameters': ReplyParameters(message_id=message.id),
            'timeout': self.timeout,
            'business_connection_id': message.business_connection_id,
            self.loot_send_key[loot_type]: loot_item,
            **params,
        }
        self.loot_send_func[loot_type](**params)

    def send_loot_items_individually(
        self,
        message: Message,
        loot_items: LootItems,
        context: Any = None,  # noqa: ANN401
    ):
        """Send loot items individually."""
        params = {}

        if (
            len(loot_items['video'] + loot_items['image'] + loot_items['audio']) == 1
            and loot_items['text']
        ):
            text = '\n\n'.join(loot_items['text'])
            if len(text) <= self.max_caption_chars:
                if len(text) >= self.collapse_at_chars:
                    text = str_to_collapsed_quotation_html(text)
                    params['parse_mode'] = 'HTML'

                params['caption'] = text
                loot_items['text'] = []

        for filetype, items in loot_items.items():
            for loot in cast(list, items):
                self.send_action(
                    message=message, action=self.loot_action[cast(LootType, filetype)]
                )
                self.logger.info("Uploading", loot=loot, context=context)

                if filetype == 'text':
                    self.send_potentially_collapsed_text(message, loot)
                    continue

                self.send_loot_item(
                    message=message, loot_type=cast(LootType, filetype), loot_item=loot, **params
                )

    def batch_loot_items(self, loot_items: LootItems) -> list[LootItems]:
        """Return a list of LootItems dicts batched by max group size and compatible formats."""
        media_items = [
            (filetype, media_item)
            for filetype in ('video', 'image')
            for media_item in loot_items[filetype]
        ]

        loot_items_batches = []
        for media_batch in batched(media_items, self.max_media_group_members, strict=False):
            loot_items_batch = {'video': [], 'image': [], 'text': [], 'audio': []}
            for filetype, item in media_batch:
                loot_items_batch[filetype].append(item)
            loot_items_batches.append(loot_items_batch)

        for audio_batch in batched(
            loot_items['audio'], self.max_media_group_members, strict=False
        ):
            loot_items_batch = {'video': [], 'image': [], 'text': [], 'audio': []}
            loot_items_batch['audio'].extend(audio_batch)
            loot_items_batches.append(loot_items_batch)

        # preserve text if no media batches?:
        if not loot_items_batches and loot_items['text']:
            loot_items_batches.append({'video': [], 'image': [], 'text': [], 'audio': []})
        if loot_items_batches:
            loot_items_batches[0]['text'].append('\n\n'.join(loot_items['text']))

        return loot_items_batches

    def path_is_type(self, path: str, typestr: str) -> bool:
        """Return True if the path has the given file type."""
        log = self.logger.bind(path=path, target_type=typestr)

        filetype, _ = guess_file_type(path, strict=False)
        if not filetype and path.endswith('.description'):
            filetype = 'text'

        if filetype:
            return filetype.startswith(typestr)

        log.info("Found unidentified file -- Is mailcap installed?")
        return False

    def send_potential_media_groups(
        self,
        message: Message,
        loot_folder: LocalPath,
        context: Any = None,  # noqa: ANN401
    ):
        """Send all media from a directory as a reply."""
        # Regarding B023: https://github.com/astral-sh/ruff/issues/7847
        loot_items = {}
        for filetype in ('video', 'audio', 'image', 'text'):
            loot_items[filetype] = [
                self.loot_wrapper[filetype](loot_file)
                for loot_file in loot_folder.walk(
                    filter=lambda p: p.is_file() and self.path_is_type(p, filetype)  # noqa: B023
                )
            ]

        for loot_items_batch in self.batch_loot_items(cast(LootItems, loot_items)):
            if (
                len(loot_items_batch['video'])
                + len(loot_items_batch['image'])
                + len(loot_items_batch['audio'])
            ) > 1:
                send = self.send_loot_items_as_media_group
            else:
                send = self.send_loot_items_individually

            send(message, cast(LootItems, loot_items_batch), context)
