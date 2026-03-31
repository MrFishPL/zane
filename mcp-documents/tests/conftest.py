"""Shared fixtures for mcp-documents tests."""

from __future__ import annotations

import io

import fitz  # PyMuPDF
import pytest
from PIL import Image


@pytest.fixture()
def sample_pdf_bytes() -> bytes:
    """Create a minimal PDF with two pages for testing.

    Page 1: text-heavy page with a title and body text.
    Page 2: a page with a drawing (rectangle) simulating a schematic.
    """
    doc = fitz.open()

    # Page 1 — text page
    page1 = doc.new_page(width=612, height=792)
    page1.insert_text((72, 72), "Test Document - Page 1", fontsize=24)
    page1.insert_text(
        (72, 120),
        "This is a text-heavy page with paragraphs of content.\n"
        "It contains specifications and descriptions of electronic components.\n"
        "Resistor values, capacitor ratings, and inductor specifications.\n"
        "This page should be classified as 'text' by the classifier.",
        fontsize=12,
    )

    # Page 2 — schematic-like page with many vector drawings
    page2 = doc.new_page(width=612, height=792)
    page2.insert_text((72, 72), "Schematic - Page 2", fontsize=14)
    # Draw many rectangles and lines to simulate a schematic
    for i in range(30):
        x = 50 + (i % 10) * 50
        y = 100 + (i // 10) * 100
        rect = fitz.Rect(x, y, x + 40, y + 30)
        page2.draw_rect(rect, color=(0, 0, 0), width=1)
        page2.draw_line(fitz.Point(x + 40, y + 15), fitz.Point(x + 50, y + 15), color=(0, 0, 0))

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.fixture()
def sample_png_bytes() -> bytes:
    """Create a small 200x150 test PNG image with colored regions."""
    img = Image.new("RGB", (200, 150), color=(255, 255, 255))

    # Draw some colored rectangles
    from PIL import ImageDraw

    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 90, 70], fill=(255, 0, 0))  # Red
    draw.rectangle([110, 10, 190, 70], fill=(0, 0, 255))  # Blue
    draw.rectangle([60, 80, 140, 140], fill=(0, 255, 0))  # Green

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def sample_pdf_path(tmp_path, sample_pdf_bytes) -> str:
    """Write sample PDF to a temp file and return its path."""
    path = tmp_path / "test.pdf"
    path.write_bytes(sample_pdf_bytes)
    return str(path)


@pytest.fixture()
def sample_png_path(tmp_path, sample_png_bytes) -> str:
    """Write sample PNG to a temp file and return its path."""
    path = tmp_path / "test.png"
    path.write_bytes(sample_png_bytes)
    return str(path)
