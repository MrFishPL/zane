"""Image processing utilities using Pillow."""

from __future__ import annotations

import base64
import io

import structlog
from PIL import Image, ImageDraw, ImageFont

log = structlog.get_logger()


def to_base64(image_bytes: bytes, format: str = "PNG") -> str:
    """Convert image bytes to a base64-encoded string.

    Args:
        image_bytes: Raw image file bytes.
        format: Output image format (default "PNG").

    Returns:
        Base64-encoded string of the image.
    """
    img = Image.open(io.BytesIO(image_bytes))
    buf = io.BytesIO()
    img.save(buf, format=format)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def crop_zoom(
    image_bytes: bytes,
    x1_pct: float,
    y1_pct: float,
    x2_pct: float,
    y2_pct: float,
    target_dpi: int = 600,
) -> bytes:
    """Crop a region of an image (percentages 0-100) and upscale.

    Args:
        image_bytes: Raw image file bytes.
        x1_pct: Left edge as percentage (0-100).
        y1_pct: Top edge as percentage (0-100).
        x2_pct: Right edge as percentage (0-100).
        y2_pct: Bottom edge as percentage (0-100).
        target_dpi: Target DPI for the output (default 600).

    Returns:
        PNG bytes of the cropped and upscaled region.

    Raises:
        ValueError: If coordinates are invalid.
    """
    for name, val in [("x1_pct", x1_pct), ("y1_pct", y1_pct), ("x2_pct", x2_pct), ("y2_pct", y2_pct)]:
        if not (0 <= val <= 100):
            raise ValueError(f"{name} must be between 0 and 100, got {val}")
    if x1_pct >= x2_pct or y1_pct >= y2_pct:
        raise ValueError(
            f"Invalid crop region: ({x1_pct}, {y1_pct}) must be top-left of ({x2_pct}, {y2_pct})"
        )

    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size

    x1 = int(w * x1_pct / 100)
    y1 = int(h * y1_pct / 100)
    x2 = int(w * x2_pct / 100)
    y2 = int(h * y2_pct / 100)

    cropped = img.crop((x1, y1, x2, y2))

    # Upscale: assume source was 300 DPI, scale to target_dpi
    scale_factor = target_dpi / 300.0
    new_w = int(cropped.width * scale_factor)
    new_h = int(cropped.height * scale_factor)
    if new_w > 0 and new_h > 0:
        cropped = cropped.resize((new_w, new_h), Image.LANCZOS)

    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()


def annotate(image_bytes: bytes, rectangles: list[dict]) -> bytes:
    """Draw red rectangles with labels on an image.

    Args:
        image_bytes: Raw image file bytes.
        rectangles: List of dicts with keys: x1, y1, x2, y2 (pixel coords), label (str).

    Returns:
        PNG bytes of the annotated image.

    Raises:
        ValueError: If any rectangle is missing required keys.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    required_keys = {"x1", "y1", "x2", "y2", "label"}
    for i, rect in enumerate(rectangles):
        missing = required_keys - set(rect.keys())
        if missing:
            raise ValueError(f"Rectangle {i} missing keys: {missing}")

        x1, y1 = int(rect["x1"]), int(rect["y1"])
        x2, y2 = int(rect["x2"]), int(rect["y2"])
        label = str(rect["label"])

        # Draw red rectangle with 3px outline
        for offset in range(3):
            draw.rectangle(
                [x1 - offset, y1 - offset, x2 + offset, y2 + offset],
                outline=(255, 0, 0, 200),
            )

        # Draw label background and text
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        except (IOError, OSError):
            font = ImageFont.load_default()

        bbox = draw.textbbox((x1, y1 - 20), label, font=font)
        text_bg = [bbox[0] - 2, bbox[1] - 2, bbox[2] + 2, bbox[3] + 2]
        draw.rectangle(text_bg, fill=(255, 0, 0, 180))
        draw.text((x1, y1 - 20), label, fill=(255, 255, 255, 255), font=font)

    result = Image.alpha_composite(img, overlay).convert("RGB")
    buf = io.BytesIO()
    result.save(buf, format="PNG")
    return buf.getvalue()


def get_info(image_bytes: bytes) -> dict:
    """Get image metadata.

    Args:
        image_bytes: Raw image file bytes.

    Returns:
        Dict with keys: width, height, format, file_size.
    """
    img = Image.open(io.BytesIO(image_bytes))
    return {
        "width": img.width,
        "height": img.height,
        "format": img.format or "UNKNOWN",
        "file_size": len(image_bytes),
    }
