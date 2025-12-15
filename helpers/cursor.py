import base64, binascii, json
from fastapi import HTTPException


def encode_cursor(payload: dict) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Cursor payload must be a dict")
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode("ascii")


def decode_cursor(cursor: str) -> dict:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid cursor")
    if not isinstance(data, dict) or "id" not in data or "score" not in data:
        raise HTTPException(status_code=400, detail="Malformed cursor")
    return data
