"""OCR provider interface."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from picglot.core.ids import make_id
from picglot.providers.base import BaseProvider, ProviderKind
from picglot.vision.types import (
    BoundingBox,
    OcrOutcome,
    Region,
    normalize_polygon,
    polygon_rotation,
)

_WHITESPACE = re.compile(r"\s+")


@dataclass(slots=True)
class OcrRequest:
    image: Image.Image
    #: Language hints; empty means "detect".
    languages: list[str] = field(default_factory=list)
    handwriting: bool = False
    #: Free-form provider hints (e.g. ``{"dense_text": True}``).
    hints: dict[str, Any] = field(default_factory=dict)

    def to_png_bytes(self) -> bytes:
        buffer = io.BytesIO()
        self.image.convert("RGB").save(buffer, "PNG", optimize=False)
        return buffer.getvalue()

    def to_jpeg_bytes(self, quality: int = 92) -> bytes:
        buffer = io.BytesIO()
        self.image.convert("RGB").save(buffer, "JPEG", quality=quality)
        return buffer.getvalue()


class OcrProvider(BaseProvider):
    kind = ProviderKind.OCR
    #: Whether the engine returns per-line polygons (vs. plain text only).
    returns_geometry: bool = True
    supports_handwriting: bool = False

    def recognize(self, request: OcrRequest) -> OcrOutcome:  # pragma: no cover - interface
        raise NotImplementedError


def make_region(
    polygon: Any,
    text: str,
    confidence: float | None,
    *,
    metadata: dict[str, Any] | None = None,
) -> Region:
    """Build a Region from raw provider geometry."""
    points = normalize_polygon(polygon)
    box = BoundingBox.from_points(points) if points else BoundingBox(0, 0, 0, 0)
    return Region(
        id=make_id("region"),
        polygon=points or box.to_polygon(),
        bounding_box=box,
        text=text,
        confidence=confidence,
        rotation=polygon_rotation(points),
        metadata=metadata or {},
    )


def line_text_from_words(line_text: str, words: list[str]) -> str:
    """Rebuild a line from its word fragments when the engine dropped the spaces.

    Several engines return both a line string and the words that make it up, and
    on stylised or tightly-set type the line string comes back with the
    separators missing — ``DON'TLETANYONETELL`` for four recognised words. The
    fragments still carry the boundaries, so they are the better source.

    Only applied when the two spellings differ by whitespace alone. That guard
    matters: an engine that splits ``well-known`` into two words must not have a
    space forced into the middle of it.
    """
    line = line_text.strip()
    fragments = [word.strip() for word in words if word and word.strip()]
    if len(fragments) < 2:
        return line

    rebuilt = " ".join(fragments)
    if _WHITESPACE.sub("", rebuilt) != _WHITESPACE.sub("", line):
        return line
    # Same characters either way — keep whichever spells out more boundaries.
    return rebuilt if len(rebuilt) > len(line) else line


def average_confidence(regions: list[Region]) -> float | None:
    scores = [region.confidence for region in regions if region.confidence is not None]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 4)
