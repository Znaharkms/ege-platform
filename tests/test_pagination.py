import pytest

from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor


def test_cursor_round_trip() -> None:
    assert decode_cursor(encode_cursor(40)) == 40


@pytest.mark.parametrize("value", ["broken", "e30", None])
def test_invalid_cursor_is_rejected(value: str | None) -> None:
    if value is None:
        assert decode_cursor(value) == 0
        return
    with pytest.raises(InvalidCursorError):
        decode_cursor(value)
