"""PDF processing using PyMuPDF (fitz)."""

from __future__ import annotations

import fitz  # PyMuPDF
import structlog

log = structlog.get_logger()


def render_all_pages(pdf_bytes: bytes, dpi: int = 300) -> list[tuple[int, bytes]]:
    """Render every page of a PDF to PNG images.

    Args:
        pdf_bytes: Raw PDF file bytes.
        dpi: Resolution for rendering (default 300).

    Returns:
        List of (page_number, png_bytes) tuples. Page numbers are 1-based.

    Raises:
        ValueError: If the data is not a valid PDF.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    results: list[tuple[int, bytes]] = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    for page_num in range(len(doc)):
        page = doc[page_num]
        pixmap = page.get_pixmap(matrix=matrix)
        png_data = pixmap.tobytes("png")
        results.append((page_num + 1, png_data))
        log.debug("page_rendered", page=page_num + 1, dpi=dpi, size=len(png_data))

    doc.close()
    return results


def render_page(pdf_bytes: bytes, page_num: int, dpi: int = 300) -> bytes:
    """Render a single page of a PDF to PNG.

    Args:
        pdf_bytes: Raw PDF file bytes.
        page_num: 1-based page number.
        dpi: Resolution for rendering (default 300).

    Returns:
        PNG image bytes.

    Raises:
        ValueError: If the PDF is invalid or the page number is out of range.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    page_count = len(doc)
    if page_num < 1 or page_num > page_count:
        doc.close()
        raise ValueError(f"Page {page_num} out of range (1-{page_count})")

    page = doc[page_num - 1]
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pixmap = page.get_pixmap(matrix=matrix)
    png_data = pixmap.tobytes("png")
    doc.close()
    return png_data


def classify_page(pdf_bytes: bytes, page_num: int) -> str:
    """Classify a PDF page as 'schematic' or 'text'.

    Heuristic: if the page contains images or significant vector drawing
    operations, classify as 'schematic'. Otherwise 'text'.

    Args:
        pdf_bytes: Raw PDF file bytes.
        page_num: 1-based page number.

    Returns:
        'schematic' or 'text'.

    Raises:
        ValueError: If the PDF is invalid or the page number is out of range.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    page_count = len(doc)
    if page_num < 1 or page_num > page_count:
        doc.close()
        raise ValueError(f"Page {page_num} out of range (1-{page_count})")

    page = doc[page_num - 1]

    # Check for embedded images
    image_list = page.get_images(full=True)
    has_images = len(image_list) > 0

    # Check for vector drawings
    drawings = page.get_drawings()
    has_drawings = len(drawings) > 20  # Threshold: many vector paths suggest a schematic

    # Check text content ratio
    text = page.get_text("text").strip()
    text_length = len(text)

    doc.close()

    # Schematic if it has images/drawings and relatively little text,
    # or a large number of drawing operations
    if has_images or has_drawings:
        if text_length < 500 or has_drawings:
            return "schematic"

    return "text"


def extract_text(pdf_bytes: bytes, page_num: int) -> str:
    """Extract native text from a PDF page (not OCR).

    Args:
        pdf_bytes: Raw PDF file bytes.
        page_num: 1-based page number.

    Returns:
        Extracted text content.

    Raises:
        ValueError: If the PDF is invalid or the page number is out of range.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    page_count = len(doc)
    if page_num < 1 or page_num > page_count:
        doc.close()
        raise ValueError(f"Page {page_num} out of range (1-{page_count})")

    page = doc[page_num - 1]
    text = page.get_text("text")
    doc.close()
    return text
