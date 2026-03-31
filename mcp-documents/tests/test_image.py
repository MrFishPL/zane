"""Unit tests for image processing module."""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from image_processor import annotate, crop_zoom, get_info, to_base64


class TestToBase64:
    def test_converts_png_to_base64(self, sample_png_bytes: bytes):
        result = to_base64(sample_png_bytes)
        # Should be valid base64
        decoded = base64.b64decode(result)
        assert decoded[:4] == b"\x89PNG"

    def test_output_format(self, sample_png_bytes: bytes):
        result = to_base64(sample_png_bytes, format="PNG")
        decoded = base64.b64decode(result)
        img = Image.open(io.BytesIO(decoded))
        assert img.format == "PNG"

    def test_returns_string(self, sample_png_bytes: bytes):
        result = to_base64(sample_png_bytes)
        assert isinstance(result, str)


class TestCropZoom:
    def test_basic_crop(self, sample_png_bytes: bytes):
        result = crop_zoom(sample_png_bytes, 0, 0, 50, 50)
        assert result[:4] == b"\x89PNG"
        img = Image.open(io.BytesIO(result))
        assert img.width > 0
        assert img.height > 0

    def test_crop_center_region(self, sample_png_bytes: bytes):
        result = crop_zoom(sample_png_bytes, 25, 25, 75, 75)
        img = Image.open(io.BytesIO(result))
        assert img.width > 0
        assert img.height > 0

    def test_full_image_crop(self, sample_png_bytes: bytes):
        result = crop_zoom(sample_png_bytes, 0, 0, 100, 100)
        assert result[:4] == b"\x89PNG"

    def test_upscales_to_target_dpi(self, sample_png_bytes: bytes):
        # 600 DPI target vs 300 DPI source should double dimensions
        result = crop_zoom(sample_png_bytes, 0, 0, 50, 50, target_dpi=600)
        img = Image.open(io.BytesIO(result))
        # Original crop would be 100x75, scaled 2x should be 200x150
        assert img.width == 200
        assert img.height == 150

    def test_invalid_coords_out_of_range(self, sample_png_bytes: bytes):
        with pytest.raises(ValueError, match="must be between 0 and 100"):
            crop_zoom(sample_png_bytes, -5, 0, 50, 50)
        with pytest.raises(ValueError, match="must be between 0 and 100"):
            crop_zoom(sample_png_bytes, 0, 0, 150, 50)

    def test_invalid_coords_inverted(self, sample_png_bytes: bytes):
        with pytest.raises(ValueError, match="Invalid crop region"):
            crop_zoom(sample_png_bytes, 50, 0, 25, 50)
        with pytest.raises(ValueError, match="Invalid crop region"):
            crop_zoom(sample_png_bytes, 0, 50, 50, 25)


class TestAnnotate:
    def test_single_rectangle(self, sample_png_bytes: bytes):
        rects = [{"x1": 10, "y1": 10, "x2": 90, "y2": 70, "label": "R1"}]
        result = annotate(sample_png_bytes, rects)
        assert result[:4] == b"\x89PNG"
        img = Image.open(io.BytesIO(result))
        # Output should be same dimensions as input
        assert img.width == 200
        assert img.height == 150

    def test_multiple_rectangles(self, sample_png_bytes: bytes):
        rects = [
            {"x1": 10, "y1": 10, "x2": 90, "y2": 70, "label": "U1"},
            {"x1": 110, "y1": 10, "x2": 190, "y2": 70, "label": "C3"},
        ]
        result = annotate(sample_png_bytes, rects)
        assert result[:4] == b"\x89PNG"

    def test_empty_rectangles(self, sample_png_bytes: bytes):
        result = annotate(sample_png_bytes, [])
        assert result[:4] == b"\x89PNG"

    def test_missing_keys_raises(self, sample_png_bytes: bytes):
        with pytest.raises(ValueError, match="missing keys"):
            annotate(sample_png_bytes, [{"x1": 10, "y1": 10}])

    def test_preserves_dimensions(self, sample_png_bytes: bytes):
        rects = [{"x1": 10, "y1": 10, "x2": 50, "y2": 50, "label": "test"}]
        result = annotate(sample_png_bytes, rects)
        original = Image.open(io.BytesIO(sample_png_bytes))
        annotated = Image.open(io.BytesIO(result))
        assert annotated.width == original.width
        assert annotated.height == original.height


class TestGetInfo:
    def test_returns_correct_info(self, sample_png_bytes: bytes):
        info = get_info(sample_png_bytes)
        assert info["width"] == 200
        assert info["height"] == 150
        assert info["format"] == "PNG"
        assert info["file_size"] == len(sample_png_bytes)

    def test_all_keys_present(self, sample_png_bytes: bytes):
        info = get_info(sample_png_bytes)
        assert "width" in info
        assert "height" in info
        assert "format" in info
        assert "file_size" in info

    def test_file_size_is_positive(self, sample_png_bytes: bytes):
        info = get_info(sample_png_bytes)
        assert info["file_size"] > 0
