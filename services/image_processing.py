"""Phase 3F.1: safe image preprocessing before sending a photo to Gemini.

Resizes an oversized photo down to a reasonable maximum dimension while
preserving aspect ratio, so a full-resolution phone photo isn't sent to the
vision API unnecessarily. A photo that's already small is returned
untouched - no pointless re-encode. Nothing here is written to disk or kept
around after the call returns.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from PIL import Image

logger = logging.getLogger(__name__)

# Gemini's own guidance is that detailed image understanding doesn't need
# more than roughly 1024-1568px on the long side; nothing in this repo's
# existing Gemini usage suggested otherwise, so we use the phase's own
# suggested default.
MAX_DIMENSION = 1280
JPEG_QUALITY = 85


@dataclass(frozen=True)
class ImagePreprocessResult:
    image_bytes: bytes
    mime_type: str
    original_width: int
    original_height: int
    processed_width: int
    processed_height: int
    original_bytes: int
    processed_bytes: int

    @property
    def was_resized(self) -> bool:
        return (self.processed_width, self.processed_height) != (self.original_width, self.original_height)


def preprocess_image(image_bytes: bytes, max_dimension: int = MAX_DIMENSION) -> ImagePreprocessResult:
    """Resize `image_bytes` so its longest side is at most `max_dimension`.

    Aspect ratio is always preserved. An image already at or under the
    limit is returned byte-for-byte unchanged (only decoded once, to read
    its dimensions) - never enlarged, never needlessly re-encoded. If the
    bytes can't be decoded as an image at all, the original bytes are
    returned unchanged rather than failing the whole request - a photo
    Pillow can't parse might still be something Gemini can.
    """
    original_bytes = len(image_bytes)

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img.load()
            original_width, original_height = img.size
    except Exception:
        logger.warning("Could not decode photo for preprocessing; sending it unchanged")
        return ImagePreprocessResult(
            image_bytes=image_bytes,
            mime_type="image/jpeg",
            original_width=0,
            original_height=0,
            processed_width=0,
            processed_height=0,
            original_bytes=original_bytes,
            processed_bytes=original_bytes,
        )

    longest_side = max(original_width, original_height)
    if longest_side <= max_dimension:
        return ImagePreprocessResult(
            image_bytes=image_bytes,
            mime_type="image/jpeg",
            original_width=original_width,
            original_height=original_height,
            processed_width=original_width,
            processed_height=original_height,
            original_bytes=original_bytes,
            processed_bytes=original_bytes,
        )

    scale = max_dimension / longest_side
    new_width = max(1, round(original_width * scale))
    new_height = max(1, round(original_height * scale))

    with Image.open(io.BytesIO(image_bytes)) as img:
        resized = img.convert("RGB").resize((new_width, new_height), Image.LANCZOS)
        buffer = io.BytesIO()
        resized.save(buffer, format="JPEG", quality=JPEG_QUALITY)
        processed = buffer.getvalue()

    return ImagePreprocessResult(
        image_bytes=processed,
        mime_type="image/jpeg",
        original_width=original_width,
        original_height=original_height,
        processed_width=new_width,
        processed_height=new_height,
        original_bytes=original_bytes,
        processed_bytes=len(processed),
    )
