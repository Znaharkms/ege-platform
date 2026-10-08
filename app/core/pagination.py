from __future__ import annotations

import base64
import json


class InvalidCursorError(ValueError):
    """Raised when a client sends an invalid pagination cursor."""


def encode_cursor(offset: int) -> str:
    payload = json.dumps({"offset": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = base64.urlsafe_b64decode(cursor + padding)
        value = json.loads(payload)
        offset = value["offset"]
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("Некорректный курсор пагинации") from exc
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise InvalidCursorError("Некорректный курсор пагинации")
    return offset
