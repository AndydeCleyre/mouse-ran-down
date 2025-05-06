"""LootSender, for sending Telegram attachments."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import batched
from mimetypes import guess_file_type  # You'd better install mailcap!
from typing import TYPE_CHECKING, Any, Literal, cast, get_args
from uuid import uuid4

from PIL import Image
from plumbum import LocalPath
from telebot.formatting import mcite
from telebot.types import (
    InputFile,
    InputMediaAudio,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
    Message,
    ReactionTypeEmoji,
    ReplyParameters,
)
from telebot.util import smart_split

from .mrd_logging import StructLogger, get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from telebot import TeleBot


MediaGroup = list[InputMediaPhoto | InputMediaVideo] | list[InputMediaAudio]

LootType = Literal['video', 'audio', 'image', 'text']
Action = Literal[
    'typing', 'record_video', 'record_voice', 'upload_voice', 'upload_video', 'upload_photo'
]

GatheredLootPaths = dict[LootType, list[LocalPath]]

LOOT_ACTION: dict[LootType, Action] = {
    'video': 'upload_video',
    'audio': 'upload_voice',
    'image': 'upload_photo',
    'text': 'typing',
}
SEND_KEY: dict[LootType, Literal['video', 'audio', 'photo', 'text']] = {
    'video': 'video',
    'audio': 'audio',
    'image': 'photo',
    'text': 'text',
}
MEDIA: dict[LootType, Callable] = {
    'video': InputMediaVideo,
    'audio': InputMediaAudio,
    'image': InputMediaPhoto,
}

MAX_THUMB_BYTES = 200000
# TODO: finer grained types, basically separating text loot items out sometimes
# and video+audio into thumbnailable type


@dataclass
class PathsBatch:
    """Video, audio, image, and text file paths."""

    audio: list[LocalPath] = field(default_factory=list)
    visual: list[LocalPath] = field(default_factory=list)
    text: list[LocalPath] = field(default_factory=list)
    thumbnails: dict[LocalPath, LocalPath] = field(default_factory=dict)


def process_thumbnail(path: LocalPath) -> InputFile | None:
    """Process a thumbnail file into a properly formatted ``InputFile``."""
    img = Image.open(path)
    thumb_path = path

    if img.format != 'JPEG' or img.size > (320, 320) or int(path.stat().st_size) > MAX_THUMB_BYTES:
        thumb_path = thumb_path.with_suffix(f".{uuid4()}.jpg")
        img.thumbnail((320, 320))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        img.save(thumb_path)
        if thumb_path.stat().st_size > MAX_THUMB_BYTES:
            return None
            # TODO: downscale

    return InputFile(thumb_path)


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
        self.loot_send_func: dict[LootType, Callable] = {
            'video': self.bot.send_video,
            'audio': self.bot.send_audio,
            'image': self.bot.send_photo,
            'text': self.bot.send_message,
        }

    def react(self, message: Message, emoji: str):
        """React to a message with an emoji."""
        self.bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.id,
            reaction=[ReactionTypeEmoji(emoji)],
            is_big=True,
        )

    def send_reply_text(self, message: Message, text: str, **params: Any):
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

    def send_media_group(
        self, message: Message, media_group: MediaGroup, context: Any = None, **params: Any
    ):
        """Send media group as a reply."""
        self.logger.info("Uploading", media_group=media_group, context=context)
        self.announce_action(message=message, action='upload_video')  # not always accurate
        params = {
            'chat_id': message.chat.id,
            'reply_parameters': ReplyParameters(message_id=message.id),
            'business_connection_id': message.business_connection_id,
            'timeout': self.timeout,
            'media': media_group,
            **params,
        }
        self.bot.send_media_group(**params)

    def send_text_as_quote(self, message: Message, text: str):
        """Send text as a quotation, expandable if it's long, and split if very long."""
        for txt in smart_split(text):
            self.send_reply_text(
                message=message,
                text=mcite(txt, expandable=len(txt) >= self.collapse_at_chars),
                parse_mode='MarkdownV2',
            )

    def announce_action(self, message: Message, action: Action):
        """Send chat action status."""
        self.bot.send_chat_action(
            chat_id=message.chat.id,
            action=action,
            business_connection_id=message.business_connection_id,
        )

    def get_thumbnail_params(
        self, paths_batch: PathsBatch, item_path: LocalPath
    ) -> dict[str, Any]:
        """Get thumbnail params if we can for an item in a PathsBatch."""
        params = {}
        if self.get_loot_type(item_path) in ('video', 'audio') and (
            thumb_path := paths_batch.thumbnails.get(item_path)
        ):
            params['thumbnail'] = process_thumbnail(thumb_path)
            if params['thumbnail'] is None:
                self.logger.error(
                    "Thumbnail still too big", item_path=item_path, thumb_path=thumb_path
                )
        return params

    def paths_batch_to_media_group(
        self, paths_batch: PathsBatch, context: Any = None
    ) -> tuple[PathsBatch, MediaGroup]:
        """
        Return a potentially modified paths_batch and a new MediaGroup.

        If the paths_batch text becomes a MediaGroup caption,
        paths_batch.text will be emptied.
        """
        self.logger.info("Creating media group", context=context, paths_batch=paths_batch)

        media_group = []
        for fp in paths_batch.visual + paths_batch.audio:  # it won't have both
            thumb_params = self.get_thumbnail_params(paths_batch, fp)
            media_group.append(
                MEDIA[self.get_loot_type(fp, context=context)](InputFile(fp), **thumb_params)
            )

        paths_batch, capt_params = self.potentially_captionize(paths_batch, media_group=True)
        if capt_params:
            media_group[0].parse_mode = capt_params['parse_mode']
            media_group[0].caption = capt_params['caption']

        return paths_batch, media_group

    def get_filetype(self, path: LocalPath, context: Any = None) -> str | None:
        """Get the generic file type of the given path, e.g. 'image', 'text', 'audio', 'video'."""
        log = self.logger.bind(path=path, context=context)

        filetype, _ = guess_file_type(path, strict=False)

        if not filetype and path.endswith(('.description', '.message')):
            filetype = 'text'

        if isinstance(filetype, str):
            return filetype.split('/')[0]

        log.error("Unexpected file type -- Is mailcap installed?", filetype=filetype)
        return None

    def get_loot_type(self, path: LocalPath, context: Any = None) -> LootType:
        """
        Get the LootType of the given path.

        Raises TypeError if the filetype doesn't match a LootType.
        """
        filetype = self.get_filetype(path, context=context)
        if filetype in get_args(LootType):
            return cast(LootType, filetype)
        raise TypeError(f"Unexpected file type: {filetype!r}")

    def send_as_media_group(self, message: Message, paths_batch: PathsBatch, context: Any = None):
        """Send paths_batch as a media group."""
        self.announce_action(message=message, action='upload_video')  # not always accurate

        paths_batch, media_group = self.paths_batch_to_media_group(paths_batch, context=context)
        if paths_batch.text:
            self.send_text_as_quote(message, '\n\n'.join(tf.read() for tf in paths_batch.text))

        self.send_media_group(message=message, media_group=media_group)

    def send_path_item(
        self, message: Message, path: LocalPath, context: Any = None, **params: Any
    ):
        """Send a single file path item."""
        loot_type = self.get_loot_type(path, context=context)

        self.announce_action(message=message, action=LOOT_ACTION[loot_type])
        self.logger.info("Uploading", loot_type=loot_type, path=path, context=context)

        if loot_type == 'text':
            self.send_text_as_quote(message, path.read())
            return

        params = {
            'chat_id': message.chat.id,
            'caption': None,
            'parse_mode': None,
            'reply_parameters': ReplyParameters(message_id=message.id),
            'timeout': self.timeout,
            'business_connection_id': message.business_connection_id,
            SEND_KEY[loot_type]: InputFile(path),
            **params,
        }
        self.loot_send_func[loot_type](**params)

    def potentially_captionize(
        self, paths_batch: PathsBatch, *, media_group: bool = False
    ) -> tuple[PathsBatch, dict[str, Any]]:
        """
        Return a possibly modified paths_batch and a possibly empty params dict.

        If all the text files can be crammed into a caption,
        the two objects will be modified accordingly:

        params will have 'caption' and 'parse_mode',
        and the paths_batch will have 'text' emptied.
        """
        if not paths_batch.text or (
            not media_group and len(paths_batch.visual + paths_batch.audio) != 1
        ):
            return paths_batch, {}

        params = {}
        text = '\n\n'.join(tf.read() for tf in paths_batch.text)
        if len(text) <= self.max_caption_chars:
            params['parse_mode'] = 'MarkdownV2'
            params['caption'] = mcite(text, expandable=len(text) >= self.collapse_at_chars)
            paths_batch.text = []

        return paths_batch, params

    def send_individually(self, message: Message, paths_batch: PathsBatch, context: Any = None):
        """Send paths_batch items individually."""
        paths_batch, capt_params = self.potentially_captionize(paths_batch)

        for fp in paths_batch.audio + paths_batch.visual + paths_batch.text:
            thumb_params = self.get_thumbnail_params(paths_batch, fp)
            self.send_path_item(message, path=fp, context=context, **capt_params, **thumb_params)

    def gather_loot_item_paths(self, loot_folder: LocalPath) -> GatheredLootPaths:
        """Return a dict of loot item paths grouped into lists by LootType."""
        all_paths: GatheredLootPaths = {'video': [], 'image': [], 'text': [], 'audio': []}
        for file_path in loot_folder.walk(filter=lambda p: p.is_file()):
            filetype = self.get_filetype(file_path)
            if filetype in ('video', 'image', 'text', 'audio'):
                if filetype == 'image':
                    img = Image.open(file_path)
                    if img.getbbox() is None:
                        self.logger.info("Skipping black image", path=file_path)
                        continue
                all_paths[filetype].append(file_path)
        return all_paths

    def assign_thumbnails(
        self, all_paths: GatheredLootPaths, *, keep_thumbnail_images: bool = True
    ) -> tuple[GatheredLootPaths, dict[LocalPath, LocalPath]]:
        """
        Return a possibly modified GatheredLootPaths and dict of media paths to thumb paths.

        If keep_thumbnail_images is False,
        those paths will be removed from the returned GatheredLootPaths.
        """
        thumbnails: dict[LocalPath, LocalPath] = {}
        for image_path in tuple(all_paths['image']):
            if not image_path.suffix:
                continue
            name_without_ext = image_path[: -len(image_path.suffix)]

            matching_videos = [
                vp for vp in all_paths['video'] if vp[: -len(vp.suffix)] == name_without_ext
            ]
            if matching_videos:
                thumbnails[matching_videos[0]] = image_path
                if not keep_thumbnail_images:
                    all_paths['image'].remove(image_path)
                continue

            matching_audios = [
                ap for ap in all_paths['audio'] if ap[: -len(ap.suffix)] == name_without_ext
            ]
            if matching_audios:
                thumbnails[matching_audios[0]] = image_path
                if not keep_thumbnail_images:
                    all_paths['image'].remove(image_path)
                continue

        return all_paths, thumbnails

    def batch_paths(
        self, loot_folder: LocalPath, *, keep_thumbnail_images: bool = True
    ) -> list[PathsBatch]:
        """
        Return a list of ``PathsBatch`` objects.

        Batches are made according to the max media group size, compatible media grouping formats,
        and thumbnail guessing by filename.
        """
        all_paths, thumbnails = self.assign_thumbnails(
            self.gather_loot_item_paths(loot_folder), keep_thumbnail_images=keep_thumbnail_images
        )

        batches: list[PathsBatch] = []
        batches.extend(
            PathsBatch(audio=list(batch))
            for batch in batched(all_paths['audio'], self.max_media_group_members, strict=False)
        )
        batches.extend(
            PathsBatch(visual=list(batch))
            for batch in batched(
                all_paths['video'] + all_paths['image'], self.max_media_group_members, strict=False
            )
        )

        if not batches and all_paths['text']:
            batches.append(PathsBatch(text=list(all_paths['text'])))
        else:
            batches[0].text.extend(all_paths['text'])

        for media_path, thumbnail_path in thumbnails.items():
            for batch in batches:
                if media_path in (batch.visual + batch.audio):
                    batch.thumbnails[media_path] = thumbnail_path
                    break

        return batches

    def send_potential_media_groups(
        self, message: Message, loot_folder: LocalPath, context: Any = None
    ):
        """Send all media from a directory as a reply."""
        for paths_batch in self.batch_paths(loot_folder):
            # We already know each batch won't have too many,
            # and won't have both visual AND audio items
            send = (
                self.send_as_media_group
                if len(paths_batch.visual + paths_batch.audio) > 1
                else self.send_individually
            )

            send(message, paths_batch, context)
