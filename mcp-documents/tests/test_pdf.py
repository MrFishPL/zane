"""Unit tests for PDF processing module."""

from __future__ import annotations

import pytest

from pdf_processor import classify_page, extract_text, render_all_pages, render_page


class TestRenderAllPages:
    def test_renders_all_pages(self, sample_pdf_bytes: bytes):
        pages = render_all_pages(sample_pdf_bytes)
        assert len(pages) == 2
        for page_num, png_data in pages:
            assert isinstance(page_num, int)
            assert page_num >= 1
            # PNG magic bytes
            assert png_data[:4] == b"\x89PNG"

    def test_page_numbers_are_one_based(self, sample_pdf_bytes: bytes):
        pages = render_all_pages(sample_pdf_bytes)
        page_nums = [p[0] for p in pages]
        assert page_nums == [1, 2]

    def test_custom_dpi(self, sample_pdf_bytes: bytes):
        pages_150 = render_all_pages(sample_pdf_bytes, dpi=150)
        pages_300 = render_all_pages(sample_pdf_bytes, dpi=300)
        # Higher DPI should produce larger images
        assert len(pages_300[0][1]) > len(pages_150[0][1])

    def test_invalid_pdf_raises(self):
        with pytest.raises(ValueError, match="Cannot open PDF"):
            render_all_pages(b"not a pdf")


class TestRenderPage:
    def test_renders_single_page(self, sample_pdf_bytes: bytes):
        png_data = render_page(sample_pdf_bytes, 1)
        assert png_data[:4] == b"\x89PNG"

    def test_renders_second_page(self, sample_pdf_bytes: bytes):
        png_data = render_page(sample_pdf_bytes, 2)
        assert png_data[:4] == b"\x89PNG"

    def test_page_out_of_range_raises(self, sample_pdf_bytes: bytes):
        with pytest.raises(ValueError, match="out of range"):
            render_page(sample_pdf_bytes, 0)
        with pytest.raises(ValueError, match="out of range"):
            render_page(sample_pdf_bytes, 99)

    def test_invalid_pdf_raises(self):
        with pytest.raises(ValueError, match="Cannot open PDF"):
            render_page(b"not a pdf", 1)


class TestClassifyPage:
    def test_text_page(self, sample_pdf_bytes: bytes):
        result = classify_page(sample_pdf_bytes, 1)
        assert result == "text"

    def test_schematic_page(self, sample_pdf_bytes: bytes):
        result = classify_page(sample_pdf_bytes, 2)
        assert result == "schematic"

    def test_page_out_of_range_raises(self, sample_pdf_bytes: bytes):
        with pytest.raises(ValueError, match="out of range"):
            classify_page(sample_pdf_bytes, 0)

    def test_invalid_pdf_raises(self):
        with pytest.raises(ValueError, match="Cannot open PDF"):
            classify_page(b"corrupted data", 1)


class TestExtractText:
    def test_extracts_text_from_page_1(self, sample_pdf_bytes: bytes):
        text = extract_text(sample_pdf_bytes, 1)
        assert "Test Document" in text
        assert "Page 1" in text
        assert "electronic components" in text

    def test_extracts_text_from_page_2(self, sample_pdf_bytes: bytes):
        text = extract_text(sample_pdf_bytes, 2)
        assert "Schematic" in text

    def test_page_out_of_range_raises(self, sample_pdf_bytes: bytes):
        with pytest.raises(ValueError, match="out of range"):
            extract_text(sample_pdf_bytes, 5)

    def test_invalid_pdf_raises(self):
        with pytest.raises(ValueError, match="Cannot open PDF"):
            extract_text(b"garbage", 1)
