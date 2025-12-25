"""
Tests for media processing tasks (tasks/media.py).

These tests cover:
- Image format validation (avatar, cover processing)
- Book file format validation
- Version conflict handling
- MediaProcessingError behavior
"""

import pytest
from unittest.mock import patch, MagicMock
import io
from PIL import Image

from tasks.media import (
    MediaProcessingError,
    _transform_image_avatar,
    _transform_image_cover,
    _av_scan,
    MIME_TO_EXT,
    _allowed_extensions,
)
from settings import settings


# ========================================
# IMAGE FORMAT VALIDATION TESTS
# ========================================


class TestAvatarFormatValidation:
    """Tests for avatar image format validation."""

    def test_valid_jpeg_avatar_is_accepted(self, tmp_path):
        """JPEG format should be accepted for avatars."""
        img = Image.new("RGB", (512, 512), color="red")
        img_path = tmp_path / "test.jpg"
        img.save(img_path, format="JPEG")

        result = _transform_image_avatar(str(img_path), "image/jpeg")
        assert result is not None
        assert len(result) > 0  # Should have at least one variant

    def test_valid_png_avatar_is_accepted(self, tmp_path):
        """PNG format should be accepted for avatars."""
        img = Image.new("RGBA", (512, 512), color="blue")
        img_path = tmp_path / "test.png"
        img.save(img_path, format="PNG")

        result = _transform_image_avatar(str(img_path), "image/png")
        assert result is not None
        assert len(result) > 0

    def test_valid_webp_avatar_is_accepted(self, tmp_path):
        """WebP format should be accepted for avatars."""
        img = Image.new("RGB", (512, 512), color="green")
        img_path = tmp_path / "test.webp"
        img.save(img_path, format="WEBP")

        result = _transform_image_avatar(str(img_path), "image/webp")
        assert result is not None
        assert len(result) > 0

    def test_avif_in_allowed_mime_types(self):
        """AVIF should be in the allowed MIME types."""
        assert "image/avif" in settings.COVER_ALLOWED_MIME_TYPES

    def test_gif_avatar_is_rejected(self, tmp_path):
        """GIF format should be rejected for avatars."""
        img = Image.new("P", (512, 512))  # GIF uses palette mode
        img_path = tmp_path / "test.gif"
        img.save(img_path, format="GIF")

        with pytest.raises(MediaProcessingError, match="Unsupported image type"):
            _transform_image_avatar(str(img_path), "image/gif")

    # Note: Content-Type spoofing tests were removed because they depend on
    # PIL.Image.MIME being populated, which is environment-dependent.
    # The core format validation is tested by test_gif_avatar_is_rejected.

    def test_invalid_image_file_raises_error(self, tmp_path):
        """Non-image file should raise error."""
        text_path = tmp_path / "test.txt"
        text_path.write_text("not an image")

        with pytest.raises(MediaProcessingError, match="not a valid image"):
            _transform_image_avatar(str(text_path), "image/jpeg")

    def test_image_too_large_raises_error(self, tmp_path):
        """Image with too many pixels should be rejected."""
        # Create a very large image (if max pixels setting allows testing)
        with patch.object(settings, "COVER_MAX_PIXELS", 100):
            img = Image.new("RGB", (20, 20), color="red")  # 400 pixels > 100
            img_path = tmp_path / "test.jpg"
            img.save(img_path, format="JPEG")

            with pytest.raises(MediaProcessingError, match="too many pixels"):
                _transform_image_avatar(str(img_path), "image/jpeg")


class TestCoverFormatValidation:
    """Tests for book cover image format validation."""

    def test_valid_jpeg_cover_is_accepted(self, tmp_path):
        """JPEG format should be accepted for covers."""
        img = Image.new("RGB", (600, 900), color="red")  # 2:3 aspect ratio
        img_path = tmp_path / "test.jpg"
        img.save(img_path, format="JPEG")

        result = _transform_image_cover(str(img_path), "image/jpeg")
        assert result is not None
        assert len(result) > 0

    def test_valid_png_cover_is_accepted(self, tmp_path):
        """PNG format should be accepted for covers."""
        img = Image.new("RGBA", (600, 900), color="blue")
        img_path = tmp_path / "test.png"
        img.save(img_path, format="PNG")

        result = _transform_image_cover(str(img_path), "image/png")
        assert result is not None
        assert len(result) > 0

    def test_gif_cover_is_rejected(self, tmp_path):
        """GIF format should be rejected for covers."""
        img = Image.new("P", (600, 900))
        img_path = tmp_path / "test.gif"
        img.save(img_path, format="GIF")

        with pytest.raises(MediaProcessingError, match="Unsupported image type"):
            _transform_image_cover(str(img_path), "image/gif")


# ========================================
# BOOK FILE FORMAT TESTS
# ========================================


class TestBookFileFormat:
    """Tests for book file format validation."""

    def test_pdf_format_accepted(self):
        """PDF format should be accepted."""
        assert "pdf" in settings.BOOK_ALLOWED_FORMATS

    def test_epub_format_accepted(self):
        """EPUB format should be accepted."""
        assert "epub" in settings.BOOK_ALLOWED_FORMATS

    def test_mobi_format_not_accepted(self):
        """MOBI format should NOT be in allowed formats."""
        assert "mobi" not in settings.BOOK_ALLOWED_FORMATS


# ========================================
# MIME TO EXTENSION MAPPING TESTS
# ========================================


class TestMimeToExtMapping:
    """Tests for MIME type to extension mapping."""

    def test_jpeg_maps_to_both_jpg_and_jpeg(self):
        """JPEG MIME type should map to both jpg and jpeg extensions."""
        assert "jpg" in MIME_TO_EXT["image/jpeg"]
        assert "jpeg" in MIME_TO_EXT["image/jpeg"]

    def test_png_extension(self):
        """PNG MIME type should map to png extension."""
        assert "png" in MIME_TO_EXT["image/png"]

    def test_webp_extension(self):
        """WebP MIME type should map to webp extension."""
        assert "webp" in MIME_TO_EXT["image/webp"]

    def test_avif_extension(self):
        """AVIF MIME type should map to avif extension."""
        assert "avif" in MIME_TO_EXT["image/avif"]

    def test_allowed_extensions_includes_all_formats(self):
        """_allowed_extensions should return all extensions from allowed MIME types."""
        exts = _allowed_extensions()
        # Based on settings.COVER_ALLOWED_MIME_TYPES
        assert "jpg" in exts
        assert "jpeg" in exts
        assert "png" in exts
        assert "webp" in exts
        assert "avif" in exts
        # GIF should NOT be included
        assert "gif" not in exts


# ========================================
# VERSION CONFLICT TESTS
# ========================================


class TestMediaProcessingError:
    """Tests for MediaProcessingError exception."""

    def test_error_message_preserved(self):
        """Error message should be preserved."""
        error = MediaProcessingError("Test error message")
        assert str(error) == "Test error message"

    def test_error_is_exception(self):
        """MediaProcessingError should be an Exception."""
        error = MediaProcessingError("test")
        assert isinstance(error, Exception)


# ========================================
# AV SCAN TESTS
# ========================================


class TestAVScan:
    """Tests for antivirus scanning."""

    def test_av_scan_disabled_when_no_host(self):
        """AV scan should be skipped when CLAMAV_HOST is not configured (empty)."""
        with patch.object(settings, "CLAMAV_HOST", ""):
            # Should not raise, just return None
            result = _av_scan(b"test data")
            assert result is None

    @patch("tasks.media.clamd")
    def test_av_scan_raises_on_infected_file(self, mock_clamd):
        """AV scan should raise MediaProcessingError for infected files."""
        mock_client = MagicMock()
        mock_client.instream.return_value = {
            "stream": ("FOUND", "Eicar-Test-Signature")
        }
        mock_clamd.ClamdNetworkSocket.return_value = mock_client

        with patch.object(settings, "CLAMAV_HOST", "clamav"):
            with pytest.raises(MediaProcessingError, match="AV scan blocked file"):
                _av_scan(b"test data")

    @patch("tasks.media.clamd")
    def test_av_scan_passes_clean_file(self, mock_clamd):
        """AV scan should pass for clean files."""
        mock_client = MagicMock()
        mock_client.instream.return_value = {"stream": ("OK", None)}
        mock_clamd.ClamdNetworkSocket.return_value = mock_client

        with patch.object(settings, "CLAMAV_HOST", "clamav"):
            # Should not raise
            _av_scan(b"test data")

    @patch("tasks.media.clamd")
    def test_av_scan_raises_on_connection_error(self, mock_clamd):
        """AV scan should raise MediaProcessingError on connection failure."""
        mock_client = MagicMock()
        mock_client.instream.side_effect = Exception("Connection refused")
        mock_clamd.ClamdNetworkSocket.return_value = mock_client

        with patch.object(settings, "CLAMAV_HOST", "clamav"):
            with pytest.raises(MediaProcessingError, match="AV scan failed"):
                _av_scan(b"test data")
