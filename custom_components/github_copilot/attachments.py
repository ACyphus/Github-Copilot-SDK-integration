"""Prepare bounded Home Assistant image attachments for Copilot."""

from __future__ import annotations

import base64
import io
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError
from PIL import Image, ImageOps, UnidentifiedImageError

from .const import (
    CONF_ATTACHMENTS_ENABLED,
    CONF_MAX_ATTACHMENT_BYTES,
    CONF_MAX_ATTACHMENTS,
    CONF_MAX_IMAGE_DIMENSION,
    DEFAULT_ATTACHMENTS_ENABLED,
    DEFAULT_MAX_ATTACHMENT_BYTES,
    DEFAULT_MAX_ATTACHMENTS,
    DEFAULT_MAX_IMAGE_DIMENSION,
)

if TYPE_CHECKING:
    from copilot.session import BlobAttachment
    from homeassistant.components.conversation import Attachment
    from homeassistant.core import HomeAssistant

    from .api import CopilotModelInfo
_ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_FORMAT_MIME_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_MAX_DECODED_PIXELS = 16_777_216


@dataclass(frozen=True, slots=True)
class PreparedAttachments:
    """Hold normalized SDK blobs and content-free metrics."""

    blobs: tuple[BlobAttachment, ...]
    original_bytes: int
    normalized_bytes: int


async def async_prepare_attachments(
    hass: HomeAssistant,
    attachments: list[Attachment] | None,
    model: CopilotModelInfo,
    options: dict,
) -> PreparedAttachments:
    """Validate and normalize resolved Home Assistant image attachments."""
    if not attachments:
        return PreparedAttachments((), 0, 0)
    if not options.get(CONF_ATTACHMENTS_ENABLED, DEFAULT_ATTACHMENTS_ENABLED):
        msg = "Image attachments are disabled in GitHub Copilot options."
        raise HomeAssistantError(msg)
    if model.id == "auto":
        msg = "Select an explicit vision-capable AI Task model for attachments."
        raise HomeAssistantError(msg)
    if not model.supports_vision:
        msg = f"Copilot model '{model.id}' does not support vision."
        raise HomeAssistantError(msg)

    configured_count = int(options.get(CONF_MAX_ATTACHMENTS, DEFAULT_MAX_ATTACHMENTS))
    model_count = (
        model.max_prompt_images
        if model.max_prompt_images is not None
        else configured_count
    )
    effective_count = min(configured_count, model_count)
    if len(attachments) > effective_count:
        msg = f"At most {effective_count} image attachment(s) are allowed."
        raise HomeAssistantError(msg)

    configured_bytes = int(
        options.get(CONF_MAX_ATTACHMENT_BYTES, DEFAULT_MAX_ATTACHMENT_BYTES)
    )
    model_bytes = (
        model.max_prompt_image_size
        if model.max_prompt_image_size is not None
        else configured_bytes
    )
    effective_bytes = min(configured_bytes, model_bytes)
    max_dimension = int(
        options.get(CONF_MAX_IMAGE_DIMENSION, DEFAULT_MAX_IMAGE_DIMENSION)
    )
    model_media = set(model.supported_media_types)
    allowed_media = (
        _ALLOWED_MIME_TYPES
        if not model_media or "image/*" in model_media
        else _ALLOWED_MIME_TYPES & model_media
    )
    if not allowed_media:
        msg = f"Copilot model '{model.id}' accepts no supported image types."
        raise HomeAssistantError(msg)

    prepared = [
        await hass.async_add_executor_job(
            _prepare_attachment,
            attachment,
            allowed_media,
            effective_bytes,
            max_dimension,
        )
        for attachment in attachments
    ]
    return PreparedAttachments(
        blobs=tuple(item[0] for item in prepared),
        original_bytes=sum(item[1] for item in prepared),
        normalized_bytes=sum(item[2] for item in prepared),
    )


def _prepare_attachment(
    attachment: Attachment,
    allowed_media: set[str],
    max_bytes: int,
    max_dimension: int,
) -> tuple[BlobAttachment, int, int]:
    """Read, validate, normalize and encode one image."""
    if attachment.mime_type not in allowed_media:
        msg = f"Image type '{attachment.mime_type}' is not supported."
        raise HomeAssistantError(msg)

    path = Path(attachment.path)
    if path.is_symlink():
        msg = "Symbolic-link image attachments are not supported."
        raise HomeAssistantError(msg)
    try:
        resolved = path.resolve(strict=True)
    except OSError as err:
        msg = "The image attachment could not be read."
        raise HomeAssistantError(msg) from err
    if not resolved.is_file():
        msg = "The image attachment is not a regular file."
        raise HomeAssistantError(msg)

    with resolved.open("rb") as file:
        source = file.read(max_bytes + 1)
    if not source:
        msg = "The image attachment is empty."
        raise HomeAssistantError(msg)
    if len(source) > max_bytes:
        msg = f"The image attachment exceeds the {max_bytes}-byte limit."
        raise HomeAssistantError(msg)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(source)) as image:
                if getattr(image, "is_animated", False):
                    msg = "Animated image attachments are not supported."
                    raise HomeAssistantError(msg)
                actual_mime = _FORMAT_MIME_TYPES.get(image.format or "")
                if actual_mime not in allowed_media:
                    msg = "The decoded image type is not supported."
                    raise HomeAssistantError(msg)
                if actual_mime != attachment.mime_type:
                    msg = "The image content does not match its declared MIME type."
                    raise HomeAssistantError(msg)
                if image.width * image.height > _MAX_DECODED_PIXELS:
                    msg = "The decoded image dimensions are too large."
                    raise HomeAssistantError(msg)
                normalized = ImageOps.exif_transpose(image)
                normalized.thumbnail(
                    (max_dimension, max_dimension),
                    Image.Resampling.LANCZOS,
                )
                has_alpha = normalized.mode in {"LA", "RGBA"} or (
                    normalized.mode == "P" and "transparency" in normalized.info
                )
                output = io.BytesIO()
                if has_alpha and "image/png" in allowed_media:
                    normalized.convert("RGBA").save(
                        output,
                        format="PNG",
                        optimize=True,
                    )
                    mime_type = "image/png"
                    extension = "png"
                elif "image/jpeg" in allowed_media:
                    rgb_image = _flatten_to_rgb(normalized)
                    rgb_image.save(
                        output,
                        format="JPEG",
                        quality=85,
                        optimize=True,
                    )
                    mime_type = "image/jpeg"
                    extension = "jpg"
                elif "image/webp" in allowed_media:
                    normalized.convert("RGBA" if has_alpha else "RGB").save(
                        output,
                        format="WEBP",
                        quality=85,
                        method=6,
                    )
                    mime_type = "image/webp"
                    extension = "webp"
                elif "image/png" in allowed_media:
                    normalized.convert("RGB").save(
                        output,
                        format="PNG",
                        optimize=True,
                    )
                    mime_type = "image/png"
                    extension = "png"
                else:
                    msg = "No supported normalized image type is available."
                    raise HomeAssistantError(msg)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as err:
        msg = "The decoded image is too large."
        raise HomeAssistantError(msg) from err
    except (OSError, SyntaxError, UnidentifiedImageError) as err:
        msg = "The attachment is not a valid complete image."
        raise HomeAssistantError(msg) from err

    normalized_bytes = output.getvalue()
    if len(normalized_bytes) > max_bytes:
        msg = f"The normalized image exceeds the {max_bytes}-byte limit."
        raise HomeAssistantError(msg)
    blob: BlobAttachment = {
        "type": "blob",
        "data": base64.b64encode(normalized_bytes).decode("ascii"),
        "mimeType": mime_type,
        "displayName": f"image.{extension}",
    }
    return blob, len(source), len(normalized_bytes)


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    """Convert an image to RGB, flattening transparency onto white."""
    if image.mode not in {"LA", "RGBA"} and not (
        image.mode == "P" and "transparency" in image.info
    ):
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    background = Image.new("RGB", rgba.size, "white")
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background
