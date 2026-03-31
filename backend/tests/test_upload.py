"""Tests for file upload: valid files, oversized files, invalid MIME types, staging flow."""

from __future__ import annotations

import io
from unittest.mock import patch

from tests.conftest import MOCK_CONVERSATION_ID, MOCK_USER_ID


def _make_pdf_bytes(size: int = 1024) -> bytes:
    """Return dummy bytes of the given size."""
    return b"%PDF-" + b"x" * (size - 5)


def _make_png_bytes(size: int = 1024) -> bytes:
    """Return dummy PNG-like bytes."""
    return b"\x89PNG" + b"x" * (size - 4)


class TestUploadValid:
    def test_upload_pdf_to_conversation(self, client, mock_minio):
        with patch(
            "services.minio_client.upload_file",
            return_value=f"minio://uploads/{MOCK_USER_ID}/{MOCK_CONVERSATION_ID}/test.pdf",
        ):
            response = client.post(
                "/api/upload",
                files={"file": ("test.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf")},
                data={"conversation_id": MOCK_CONVERSATION_ID},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["filename"] == "test.pdf"
            assert data["content_type"] == "application/pdf"
            assert "upload_id" in data
            assert data["path"].startswith("minio://uploads/")

    def test_upload_png(self, client, mock_minio):
        with patch(
            "services.minio_client.upload_file",
            return_value=f"minio://uploads/{MOCK_USER_ID}/staging/uid/photo.png",
        ):
            response = client.post(
                "/api/upload",
                files={"file": ("photo.png", io.BytesIO(_make_png_bytes()), "image/png")},
            )
            assert response.status_code == 200
            assert response.json()["content_type"] == "image/png"

    def test_upload_jpeg(self, client, mock_minio):
        with patch(
            "services.minio_client.upload_file",
            return_value="minio://uploads/staging/test.jpg",
        ):
            response = client.post(
                "/api/upload",
                files={"file": ("test.jpg", io.BytesIO(b"\xff\xd8" + b"x" * 100), "image/jpeg")},
            )
            assert response.status_code == 200

    def test_upload_webp(self, client, mock_minio):
        with patch(
            "services.minio_client.upload_file",
            return_value="minio://uploads/staging/test.webp",
        ):
            response = client.post(
                "/api/upload",
                files={"file": ("test.webp", io.BytesIO(b"RIFF" + b"x" * 100), "image/webp")},
            )
            assert response.status_code == 200


class TestUploadStaging:
    def test_upload_without_conversation_goes_to_staging(self, client, mock_minio):
        with patch("services.minio_client.upload_file") as mock_upload:
            mock_upload.return_value = "minio://uploads/staging/uid/file.pdf"
            response = client.post(
                "/api/upload",
                files={"file": ("file.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf")},
            )
            assert response.status_code == 200
            # Verify the path contains 'staging'
            call_args = mock_upload.call_args
            path_arg = call_args.kwargs.get("path") or call_args[0][1]
            assert "staging" in path_arg

    def test_upload_with_conversation_skips_staging(self, client, mock_minio):
        with patch("services.minio_client.upload_file") as mock_upload:
            mock_upload.return_value = f"minio://uploads/{MOCK_USER_ID}/{MOCK_CONVERSATION_ID}/f.pdf"
            response = client.post(
                "/api/upload",
                files={"file": ("f.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf")},
                data={"conversation_id": MOCK_CONVERSATION_ID},
            )
            assert response.status_code == 200
            call_args = mock_upload.call_args
            path_arg = call_args.kwargs.get("path") or call_args[0][1]
            assert MOCK_CONVERSATION_ID in path_arg
            assert "staging" not in path_arg


class TestUploadInvalidMime:
    def test_reject_text_plain(self, client, mock_minio):
        response = client.post(
            "/api/upload",
            files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        assert response.status_code == 415
        assert "Unsupported file type" in response.json()["detail"]

    def test_reject_zip(self, client, mock_minio):
        response = client.post(
            "/api/upload",
            files={"file": ("archive.zip", io.BytesIO(b"PK" + b"x" * 100), "application/zip")},
        )
        assert response.status_code == 415

    def test_reject_html(self, client, mock_minio):
        response = client.post(
            "/api/upload",
            files={"file": ("page.html", io.BytesIO(b"<html>"), "text/html")},
        )
        assert response.status_code == 415


class TestUploadOversized:
    def test_reject_oversized_file(self, client, mock_minio):
        # Create a file just over 100MB
        big_data = b"x" * (100 * 1024 * 1024 + 1)
        response = client.post(
            "/api/upload",
            files={"file": ("big.pdf", io.BytesIO(big_data), "application/pdf")},
        )
        assert response.status_code == 413
        assert "too large" in response.json()["detail"].lower()
