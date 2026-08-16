"""Tests for bounded image attachment preparation."""

from __future__ import annotations

import base64
import io

import pytest
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from PIL import Image

from custom_components.github_copilot.api import CopilotModelInfo
from custom_components.github_copilot.attachments import async_prepare_attachments
from custom_components.github_copilot.const import (
    CONF_MAX_ATTACHMENT_BYTES,
    CONF_MAX_ATTACHMENTS,
    CONF_MAX_IMAGE_DIMENSION,
)


def _model(
    *,
    model_id: str = "vision-model",
    vision: bool = True,
    media: tuple[str, ...] = ("image/jpeg", "image/png", "image/webp"),
) -> CopilotModelInfo:
    return CopilotModelInfo(
        id=model_id,
        name="Vision Model",
        supports_vision=vision,
        supported_media_types=media,
        max_prompt_images=2,
        max_prompt_image_size=5 * 1024 * 1024,
    )


def _attachment(path, mime_type: str) -> conversation.Attachment:
    return conversation.Attachment(
        media_content_id="media-source://test/image",
        mime_type=mime_type,
        path=path,
    )


def _write_image(path, mode: str, image_format: str) -> None:
    color = (10, 20, 30, 128) if mode == "RGBA" else (10, 20, 30)
    image = Image.new(mode, (3000, 1000), color)
    if image_format == "JPEG":
        exif = Image.Exif()
        exif[0x010E] = "synthetic metadata"
        image.save(path, format=image_format, exif=exif)
    else:
        image.save(path, format=image_format)


@pytest.mark.asyncio
async def test_jpeg_is_resized_reencoded_and_stripped(hass, tmp_path) -> None:
    """Camera JPEGs should become bounded metadata-free SDK blobs."""
    path = tmp_path / "camera.jpg"
    _write_image(path, "RGB", "JPEG")

    prepared = await async_prepare_attachments(
        hass,
        [_attachment(path, "image/jpeg")],
        _model(),
        {
            CONF_MAX_ATTACHMENTS: 1,
            CONF_MAX_ATTACHMENT_BYTES: 5 * 1024 * 1024,
            CONF_MAX_IMAGE_DIMENSION: 1024,
        },
    )

    assert len(prepared.blobs) == 1
    blob = prepared.blobs[0]
    assert blob["type"] == "blob"
    assert blob["mimeType"] == "image/jpeg"
    assert "path" not in blob
    decoded = base64.b64decode(blob["data"])
    with Image.open(io.BytesIO(decoded)) as image:
        assert max(image.size) == 1024
        assert image.getexif() == {}


@pytest.mark.asyncio
async def test_transparent_png_stays_png(hass, tmp_path) -> None:
    """Transparency should be preserved in normalized PNG output."""
    path = tmp_path / "transparent.png"
    _write_image(path, "RGBA", "PNG")

    prepared = await async_prepare_attachments(
        hass,
        [_attachment(path, "image/png")],
        _model(),
        {CONF_MAX_IMAGE_DIMENSION: 512},
    )

    blob = prepared.blobs[0]
    assert blob["mimeType"] == "image/png"
    with Image.open(io.BytesIO(base64.b64decode(blob["data"]))) as image:
        assert image.mode == "RGBA"
        assert max(image.size) == 512


@pytest.mark.asyncio
async def test_auto_model_is_rejected_before_read(hass, tmp_path) -> None:
    """Auto cannot prove vision support before attachment processing."""
    missing = tmp_path / "missing.jpg"

    with pytest.raises(HomeAssistantError, match="explicit vision-capable"):
        await async_prepare_attachments(
            hass,
            [_attachment(missing, "image/jpeg")],
            _model(model_id="auto"),
            {},
        )


@pytest.mark.asyncio
async def test_text_model_and_unsupported_mime_are_rejected(hass, tmp_path) -> None:
    """Model capability and MIME checks should fail before provider calls."""
    path = tmp_path / "image.jpg"
    _write_image(path, "RGB", "JPEG")

    with pytest.raises(HomeAssistantError, match="does not support vision"):
        await async_prepare_attachments(
            hass,
            [_attachment(path, "image/jpeg")],
            _model(vision=False),
            {},
        )
    with pytest.raises(HomeAssistantError, match="not supported"):
        await async_prepare_attachments(
            hass,
            [_attachment(path, "image/gif")],
            _model(),
            {},
        )


@pytest.mark.asyncio
async def test_count_and_byte_limits_are_enforced(hass, tmp_path) -> None:
    """Configured limits should bound image count and encoded input."""
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    _write_image(first, "RGB", "JPEG")
    _write_image(second, "RGB", "JPEG")

    with pytest.raises(HomeAssistantError, match="At most 1"):
        await async_prepare_attachments(
            hass,
            [_attachment(first, "image/jpeg"), _attachment(second, "image/jpeg")],
            _model(),
            {CONF_MAX_ATTACHMENTS: 1},
        )
    with pytest.raises(HomeAssistantError, match="exceeds"):
        await async_prepare_attachments(
            hass,
            [_attachment(first, "image/jpeg")],
            _model(),
            {CONF_MAX_ATTACHMENT_BYTES: 100},
        )
