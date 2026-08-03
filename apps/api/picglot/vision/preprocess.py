"""Image loading and preprocessing.

The pipeline is deliberately conservative: every enhancement is measured and
skipped when it would not help, because over-processing a clean screenshot
costs accuracy. Each step reports what it did so the editor can explain the
result and the user can undo individual steps.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps

from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import get_logger

log = get_logger(__name__)

# Pillow's own bomb guard; ours is stricter and configurable.
Image.MAX_IMAGE_PIXELS = None

_heif_registered = False


def _register_heif() -> None:
    global _heif_registered
    if _heif_registered:
        return
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except Exception:  # pragma: no cover - optional dependency
        log.info("preprocess.heif_unavailable")
    _heif_registered = True


@dataclass(slots=True)
class PreprocessReport:
    """What actually happened, so the UI can explain it and offer an undo."""

    steps: list[str] = field(default_factory=list)
    rotation_applied: int = 0
    deskew_angle: float = 0.0
    perspective_corrected: bool = False
    shadow_removed: bool = False
    scale_factor: float = 1.0
    original_size: tuple[int, int] = (0, 0)
    output_size: tuple[int, int] = (0, 0)

    def note(self, step: str) -> None:
        self.steps.append(step)

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "rotation_applied": self.rotation_applied,
            "deskew_angle": round(self.deskew_angle, 3),
            "perspective_corrected": self.perspective_corrected,
            "shadow_removed": self.shadow_removed,
            "scale_factor": round(self.scale_factor, 4),
            "original_size": list(self.original_size),
            "output_size": list(self.output_size),
        }


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_image(data: bytes, *, max_pixels: int | None = None) -> Image.Image:
    """Decode bytes to RGB, honouring EXIF orientation and guarding against bombs."""
    _register_heif()
    limit = max_pixels or settings.max_image_pixels

    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
            frames = getattr(probe, "n_frames", 1)
            if width * height > limit:
                raise AppError(
                    code=ErrorCode.IMAGE_TOO_LARGE,
                    details={"pixels": width * height, "limit": limit},
                )
            probe.seek(0)  # animated inputs: first frame only
            image = probe.convert("RGB") if probe.mode not in {"RGB", "L"} else probe.copy()
            if frames > 1:
                image.info["frame_count"] = frames
    except AppError:
        raise
    except Exception as exc:
        raise AppError(code=ErrorCode.CORRUPTED_FILE, internal=str(exc)) from exc

    # EXIF orientation, then drop the metadata entirely.
    try:
        image = ImageOps.exif_transpose(image) or image
    except Exception:  # pragma: no cover - malformed EXIF
        pass
    if image.mode != "RGB":
        image = image.convert("RGB")
    return strip_metadata(image)


def strip_metadata(image: Image.Image) -> Image.Image:
    """Return a copy with EXIF/GPS/ICC removed — uploads must not leak location."""
    clean = Image.new(image.mode, image.size)
    clean.putdata(list(image.getdata()))
    return clean


def to_cv(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def to_pil(array: np.ndarray) -> Image.Image:
    if array.ndim == 2:
        return Image.fromarray(array, mode="L").convert("RGB")
    return Image.fromarray(cv2.cvtColor(array, cv2.COLOR_BGR2RGB))


def encode(image: Image.Image, fmt: str = "PNG", quality: int = 92) -> bytes:
    buffer = io.BytesIO()
    fmt = fmt.upper()
    if fmt in {"JPG", "JPEG"}:
        image.convert("RGB").save(buffer, "JPEG", quality=quality, optimize=True, progressive=True)
    elif fmt == "WEBP":
        image.save(buffer, "WEBP", quality=quality, method=4)
    else:
        image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Individual operations
# --------------------------------------------------------------------------- #
def limit_dimension(image: Image.Image, max_dimension: int) -> tuple[Image.Image, float]:
    """Downscale so the longest side fits, preserving aspect ratio."""
    width, height = image.size
    longest = max(width, height)
    if longest <= max_dimension:
        return image, 1.0
    factor = max_dimension / float(longest)
    size = (max(1, round(width * factor)), max(1, round(height * factor)))
    return image.resize(size, Image.Resampling.LANCZOS), factor


def estimate_skew(gray: np.ndarray) -> float:
    """Skew angle in degrees using the dominant text-line orientation.

    Hough lines on the edge map are more reliable on photographs than
    ``minAreaRect`` on a threshold, which collapses on sparse text.
    """
    height, width = gray.shape[:2]
    scale = 1000.0 / max(height, width)
    working = cv2.resize(gray, None, fx=scale, fy=scale) if scale < 1 else gray

    edges = cv2.Canny(working, 60, 180, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 720,
        threshold=90,
        minLineLength=max(30, working.shape[1] // 8),
        maxLineGap=12,
    )
    if lines is None or len(lines) == 0:
        return 0.0

    # HoughLinesP returns (N, 1, 4) on some OpenCV builds and (N, 4) on others.
    segments = np.asarray(lines).reshape(-1, 4)

    angles: list[float] = []
    for segment in segments[:400]:
        x1, y1, x2, y2 = (float(value) for value in segment)
        if x2 == x1:
            continue
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        # Only near-horizontal lines describe text baselines.
        if -25 < angle < 25:
            angles.append(angle)
    if len(angles) < 5:
        return 0.0
    median = float(np.median(angles))
    return 0.0 if abs(median) < 0.15 else round(median, 3)


def deskew(image: Image.Image, *, max_angle: float = 20.0) -> tuple[Image.Image, float]:
    array = to_cv(image)
    gray = cv2.cvtColor(array, cv2.COLOR_BGR2GRAY)
    angle = estimate_skew(gray)
    if abs(angle) < 0.25 or abs(angle) > max_angle:
        return image, 0.0

    height, width = array.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_width = int(height * sin + width * cos)
    new_height = int(height * cos + width * sin)
    matrix[0, 2] += new_width / 2 - width / 2
    matrix[1, 2] += new_height / 2 - height / 2
    rotated = cv2.warpAffine(
        array,
        matrix,
        (new_width, new_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return to_pil(rotated), angle


def find_document_corners(image: Image.Image, *, min_area_ratio: float = 0.25) -> np.ndarray | None:
    """Locate a quadrilateral page inside a photo. ``None`` when there is none."""
    array = to_cv(image)
    height, width = array.shape[:2]
    scale = 900.0 / max(height, width)
    working = cv2.resize(array, None, fx=scale, fy=scale) if scale < 1 else array.copy()

    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 50, 160)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    working_area = working.shape[0] * working.shape[1]

    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
        area = cv2.contourArea(contour)
        if area < working_area * min_area_ratio:
            break
        perimeter = cv2.arcLength(contour, True)
        approximation = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approximation) == 4 and cv2.isContourConvex(approximation):
            corners = approximation.reshape(4, 2).astype(np.float32)
            if scale < 1:
                corners /= scale
            # Reject near-full-frame detections: that is just the image border.
            if cv2.contourArea(corners) > 0.985 * width * height:
                return None
            return _order_corners(corners)
    return None


def _order_corners(points: np.ndarray) -> np.ndarray:
    """Order as top-left, top-right, bottom-right, bottom-left."""
    ordered = np.zeros((4, 2), dtype=np.float32)
    total = points.sum(axis=1)
    diff = np.diff(points, axis=1).ravel()
    ordered[0] = points[np.argmin(total)]
    ordered[2] = points[np.argmax(total)]
    ordered[1] = points[np.argmin(diff)]
    ordered[3] = points[np.argmax(diff)]
    return ordered


def correct_perspective(
    image: Image.Image, corners: np.ndarray | None = None
) -> tuple[Image.Image, bool]:
    corners = corners if corners is not None else find_document_corners(image)
    if corners is None:
        return image, False

    top_left, top_right, bottom_right, bottom_left = corners
    width = int(
        max(np.linalg.norm(bottom_right - bottom_left), np.linalg.norm(top_right - top_left))
    )
    height = int(
        max(np.linalg.norm(top_right - bottom_right), np.linalg.norm(top_left - bottom_left))
    )
    if width < 80 or height < 80:
        return image, False

    destination = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32
    )
    matrix = cv2.getPerspectiveTransform(corners, destination)
    warped = cv2.warpPerspective(to_cv(image), matrix, (width, height), flags=cv2.INTER_CUBIC)
    return to_pil(warped), True


def remove_shadows(image: Image.Image) -> tuple[Image.Image, bool]:
    """Flatten uneven illumination by dividing out a heavily blurred background."""
    array = to_cv(image)
    channels = cv2.split(array)
    result = []
    for channel in channels:
        dilated = cv2.dilate(channel, np.ones((7, 7), np.uint8))
        background = cv2.medianBlur(dilated, 21)
        difference = 255 - cv2.absdiff(channel, background)
        result.append(cv2.normalize(difference, None, 0, 255, cv2.NORM_MINMAX, cv2.CV_8UC1))
    flattened = cv2.merge(result)

    # Only accept the change when illumination really was uneven.
    gray_before = cv2.cvtColor(array, cv2.COLOR_BGR2GRAY)
    if float(np.std(cv2.resize(gray_before, (32, 32)))) < 18.0:
        return image, False
    return to_pil(flattened), True


def enhance_contrast(image: Image.Image, *, clip_limit: float = 2.0) -> Image.Image:
    """CLAHE on the L channel — improves faint text without blowing out colours."""
    lab = cv2.cvtColor(to_cv(image), cv2.COLOR_BGR2LAB)
    lightness, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
    merged = cv2.merge((clahe.apply(lightness), a_channel, b_channel))
    return to_pil(cv2.cvtColor(merged, cv2.COLOR_LAB2BGR))


def denoise(image: Image.Image, strength: int = 5) -> Image.Image:
    return to_pil(cv2.fastNlMeansDenoisingColored(to_cv(image), None, strength, strength, 7, 21))


def sharpen(image: Image.Image, amount: float = 0.6) -> Image.Image:
    array = to_cv(image)
    blurred = cv2.GaussianBlur(array, (0, 0), 2.0)
    return to_pil(cv2.addWeighted(array, 1 + amount, blurred, -amount, 0))


def adaptive_threshold(
    image: Image.Image, *, block_size: int = 31, offset: int = 15
) -> Image.Image:
    gray = cv2.cvtColor(to_cv(image), cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        block_size if block_size % 2 else block_size + 1,
        offset,
    )
    return to_pil(binary)


def to_black_and_white(image: Image.Image) -> Image.Image:
    return adaptive_threshold(remove_shadows(image)[0])


def detect_orientation(image: Image.Image) -> int:
    """Return 0/90/180/270 — the rotation needed to make text upright.

    Tesseract's OSD is used when available (it is the only reliable way to tell
    0 from 180); otherwise a projection-profile heuristic decides 0 vs 90.
    """
    try:
        import pytesseract

        if settings.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
        osd = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
        rotate = int(osd.get("rotate", 0)) % 360
        confidence = float(osd.get("orientation_conf", 0))
        if confidence >= 1.5:
            return rotate
    except Exception:
        pass

    gray = cv2.cvtColor(to_cv(image), cv2.COLOR_BGR2GRAY)
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    horizontal_variance = float(np.var(binary.sum(axis=1)))
    vertical_variance = float(np.var(binary.sum(axis=0)))
    # Horizontal text produces strongly varying row sums (lines vs gaps).
    if vertical_variance > horizontal_variance * 1.6:
        return 90
    return 0


def rotate_by(image: Image.Image, degrees: int) -> Image.Image:
    degrees %= 360
    if degrees == 0:
        return image
    return image.rotate(-degrees, expand=True, resample=Image.Resampling.BICUBIC)


def auto_crop_borders(image: Image.Image, *, tolerance: int = 12) -> Image.Image:
    """Trim uniform scanner borders."""
    gray = cv2.cvtColor(to_cv(image), cv2.COLOR_BGR2GRAY)
    mask = cv2.threshold(gray, 255 - tolerance, 255, cv2.THRESH_BINARY_INV)[1]
    coords = cv2.findNonZero(mask)
    if coords is None:
        return image
    x, y, width, height = cv2.boundingRect(coords)
    if width < image.width * 0.5 or height < image.height * 0.5:
        return image
    if width >= image.width - 2 and height >= image.height - 2:
        return image
    return image.crop((x, y, x + width, y + height))


# --------------------------------------------------------------------------- #
# Composed pipelines
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class PreprocessOptions:
    auto_rotate: bool = True
    deskew: bool = True
    perspective: bool = False
    shadow_removal: bool = False
    enhance: bool = True
    denoise: bool = False
    sharpen: bool = False
    crop_borders: bool = False
    black_and_white: bool = False
    max_dimension: int | None = None

    @classmethod
    def for_tool(cls, tool_slug: str, overrides: dict[str, Any] | None = None) -> PreprocessOptions:
        presets: dict[str, dict[str, Any]] = {
            # Screenshots are already pixel perfect: touching them loses accuracy.
            "screenshot-translator": {
                "auto_rotate": False,
                "deskew": False,
                "enhance": False,
            },
            "translate-photo": {"perspective": True, "shadow_removal": True, "sharpen": True},
            "document-scanner": {
                "perspective": True,
                "shadow_removal": True,
                "crop_borders": True,
                "sharpen": True,
            },
            "receipt-scanner": {"perspective": True, "shadow_removal": True, "sharpen": True},
            "invoice-ocr": {"perspective": True, "shadow_removal": True},
            "handwriting-to-text": {"enhance": True, "denoise": True},
        }
        options = cls(**presets.get(tool_slug, {}))
        for key, value in (overrides or {}).items():
            if hasattr(options, key) and value is not None:
                setattr(options, key, value)
        return options


def preprocess(
    image: Image.Image, options: PreprocessOptions | None = None
) -> tuple[Image.Image, PreprocessReport]:
    options = options or PreprocessOptions()
    report = PreprocessReport(original_size=image.size)
    working = image

    if options.perspective:
        working, corrected = correct_perspective(working)
        report.perspective_corrected = corrected
        if corrected:
            report.note("perspective_correction")

    if options.crop_borders:
        before = working.size
        working = auto_crop_borders(working)
        if working.size != before:
            report.note("crop_borders")

    if options.auto_rotate:
        rotation = detect_orientation(working)
        if rotation:
            working = rotate_by(working, rotation)
            report.rotation_applied = rotation
            report.note(f"rotate_{rotation}")

    if options.deskew:
        working, angle = deskew(working)
        if angle:
            report.deskew_angle = angle
            report.note("deskew")

    if options.shadow_removal:
        working, removed = remove_shadows(working)
        report.shadow_removed = removed
        if removed:
            report.note("shadow_removal")

    if options.denoise:
        working = denoise(working)
        report.note("denoise")

    if options.enhance:
        working = enhance_contrast(working)
        report.note("contrast")

    if options.sharpen:
        working = sharpen(working)
        report.note("sharpen")

    if options.black_and_white:
        working = to_black_and_white(working)
        report.note("black_and_white")

    max_dimension = options.max_dimension or settings.processing_max_dimension
    working, factor = limit_dimension(working, max_dimension)
    report.scale_factor = factor
    if factor < 1.0:
        report.note("downscale")

    report.output_size = working.size
    return working, report


def make_preview(image: Image.Image) -> Image.Image:
    return limit_dimension(image, settings.preview_max_dimension)[0]


def make_thumbnail(image: Image.Image) -> Image.Image:
    return limit_dimension(image, settings.thumbnail_max_dimension)[0]


def dominant_color(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """Median colour of a crop — used to estimate a text region's background."""
    left, top, right, bottom = (int(value) for value in box)
    left, top = max(0, left), max(0, top)
    right, bottom = min(image.width, right), min(image.height, bottom)
    if right <= left or bottom <= top:
        return (255, 255, 255)
    crop = np.array(image.crop((left, top, right, bottom)))
    if crop.size == 0:
        return (255, 255, 255)
    median = np.median(crop.reshape(-1, crop.shape[-1]), axis=0)
    return tuple(int(value) for value in median[:3])  # type: ignore[return-value]


def background_complexity(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    """0 = flat colour, 1 = highly textured. Chooses the inpainting strategy."""
    left, top, right, bottom = (int(value) for value in box)
    left, top = max(0, left), max(0, top)
    right, bottom = min(image.width, right), min(image.height, bottom)
    if right - left < 4 or bottom - top < 4:
        return 0.0
    crop = cv2.cvtColor(np.array(image.crop((left, top, right, bottom))), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(crop, 60, 160)
    edge_ratio = float(np.count_nonzero(edges)) / edges.size
    deviation = float(np.std(crop)) / 128.0
    return float(min(1.0, edge_ratio * 3.0 + deviation * 0.5))


def surround_complexity(
    image: Image.Image, box: tuple[int, int, int, int], *, band: int = 8
) -> float:
    """Complexity of the ring *around* a box, ignoring the box itself.

    Measuring inside the box would always score high — the text is the edges we
    are about to remove. What matters for choosing an inpainting strategy is
    what the background does next to the text.
    """
    left, top, right, bottom = (int(value) for value in box)
    band = max(3, band)
    strips: list[tuple[int, int, int, int]] = [
        (left, top - band, right, top),  # above
        (left, bottom, right, bottom + band),  # below
        (left - band, top, left, bottom),  # left
        (right, top, right + band, bottom),  # right
    ]
    scores: list[float] = []
    for strip in strips:
        x1 = max(0, strip[0])
        y1 = max(0, strip[1])
        x2 = min(image.width, strip[2])
        y2 = min(image.height, strip[3])
        if x2 - x1 < 3 or y2 - y1 < 3:
            continue
        scores.append(background_complexity(image, (x1, y1, x2, y2)))
    if not scores:
        return background_complexity(image, box)
    # The median keeps one busy side (a photo edge) from dominating.
    scores.sort()
    return scores[len(scores) // 2]
