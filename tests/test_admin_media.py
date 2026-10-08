from io import BytesIO

from PIL import Image

from app.api.routes.admin_media import MAX_IMAGE_EDGE, optimize_question_image


def test_question_image_is_resized_and_converted_to_webp() -> None:
    source = BytesIO()
    Image.new("RGB", (3000, 1800), "white").save(source, format="PNG")

    optimized, width, height = optimize_question_image(source.getvalue())

    assert max(width, height) == MAX_IMAGE_EDGE
    assert optimized[:4] == b"RIFF"
    with Image.open(BytesIO(optimized)) as result:
        assert result.format == "WEBP"
        assert result.size == (width, height)
