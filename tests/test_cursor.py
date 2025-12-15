"""
Unit tests for cursor encoding/decoding helper functions.
Tests pagination cursor functionality.
"""
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi import HTTPException

from helpers.cursor import encode_cursor, decode_cursor


class TestCursorEncoding:
    """Test cursor encoding and decoding."""
    
    def test_encode_decode_simple(self):
        """Should encode and decode simple payload."""
        payload = {"id": 123, "score": 0.85}
        
        cursor = encode_cursor(payload)
        decoded = decode_cursor(cursor)
        
        assert decoded == payload
    
    def test_encode_decode_complex(self):
        """Should handle complex payloads."""
        payload = {
            "id": 456,
            "score": 0.95,
            "timestamp": "2023-06-15T10:30:00",
            "nested": {"key": "value"}
        }
        
        cursor = encode_cursor(payload)
        decoded = decode_cursor(cursor)
        
        assert decoded == payload
    
    def test_encode_decode_empty(self):
        """Should handle empty payload."""
        payload = {}
        
        cursor = encode_cursor(payload)
        decoded = decode_cursor(cursor)
        
        assert decoded == payload
    
    def test_decode_invalid_base64(self):
        """Should raise HTTPException for invalid base64."""
        with pytest.raises(HTTPException) as exc_info:
            decode_cursor("invalid!!base64")
        
        assert exc_info.value.status_code == 400
        assert "Invalid cursor" in exc_info.value.detail
    
    def test_decode_invalid_json(self):
        """Should raise HTTPException for invalid JSON."""
        # Valid base64 but not valid JSON
        import base64
        invalid_cursor = base64.b64encode(b"not json").decode()
        
        with pytest.raises(HTTPException) as exc_info:
            decode_cursor(invalid_cursor)
        
        assert exc_info.value.status_code == 400
        assert "Invalid cursor" in exc_info.value.detail
    
    def test_cursor_is_url_safe(self):
        """Should produce URL-safe cursors."""
        payload = {"id": 999, "score": 0.5}
        cursor = encode_cursor(payload)
        
        # URL-safe base64 should not contain +, /, or =
        assert "+" not in cursor
        assert "/" not in cursor
        # May contain - and _ which are URL-safe
    
    def test_roundtrip_with_special_chars(self):
        """Should handle special characters in payload."""
        payload = {
            "name": "Test Author",
            "query": "science fiction & fantasy",
            "special": "!@#$%^&*()"
        }
        
        cursor = encode_cursor(payload)
        decoded = decode_cursor(cursor)
        
        assert decoded == payload
    
    def test_roundtrip_with_numbers(self):
        """Should preserve number types."""
        payload = {
            "int_val": 42,
            "float_val": 3.14159,
            "negative": -100
        }
        
        cursor = encode_cursor(payload)
        decoded = decode_cursor(cursor)
        
        assert decoded["int_val"] == 42
        assert decoded["float_val"] == 3.14159
        assert decoded["negative"] == -100
    
    def test_roundtrip_with_boolean(self):
        """Should preserve boolean values."""
        payload = {
            "active": True,
            "deleted": False
        }
        
        cursor = encode_cursor(payload)
        decoded = decode_cursor(cursor)
        
        assert decoded["active"] is True
        assert decoded["deleted"] is False
    
    def test_roundtrip_with_null(self):
        """Should preserve None/null values."""
        payload = {
            "id": 123,
            "optional": None
        }
        
        cursor = encode_cursor(payload)
        decoded = decode_cursor(cursor)
        
        assert decoded["optional"] is None
