"""Image payload helpers shared by the vision-capable provider adapters."""

from __future__ import annotations

import base64
from dataclasses import dataclass


@dataclass(frozen=True)
class ImagePart:
    """One image (a rendered document page or a video keyframe) sent to a model."""

    data: bytes
    media_type: str = "image/png"
    label: str = ""

    @property
    def b64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


def anthropic_image_block(image: ImagePart) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": image.media_type, "data": image.b64},
    }


def openai_image_block(image: ImagePart) -> dict:
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{image.media_type};base64,{image.b64}"},
    }
