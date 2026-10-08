from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Form, UploadFile, status
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import AdminPrincipal
from app.api.routes.catalog import Session
from app.core.config import get_settings
from app.core.problems import ProblemException

router = APIRouter(prefix="/admin/media", tags=["Admin media"])

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_EDGE = 2400
WEBP_QUALITY = 90


def optimize_question_image(source: bytes) -> tuple[bytes, int, int]:
    try:
        with Image.open(BytesIO(source)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ProblemException(400, "invalid_image", "Файл не является изображением") from exc

    if image.width < 1 or image.height < 1:
        raise ProblemException(400, "invalid_image", "Некорректные размеры изображения")
    image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
    has_alpha = image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    )
    image = image.convert("RGBA" if has_alpha else "RGB")
    output = BytesIO()
    image.save(
        output,
        format="WEBP",
        quality=WEBP_QUALITY,
        method=6,
        exact=has_alpha,
    )
    return output.getvalue(), image.width, image.height


@router.post(
    "/images",
    status_code=status.HTTP_201_CREATED,
    operation_id="adminUploadQuestionImage",
    include_in_schema=False,
)
async def upload_question_image(
    admin: AdminPrincipal,
    session: Session,
    file: Annotated[UploadFile, File()],
    alt_text: Annotated[str | None, Form(alias="altText")] = None,
) -> dict[str, object]:
    settings = get_settings()
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise ProblemException(400, "unsupported_image_type", "Допустимы JPEG, PNG и WebP")
    source = await file.read(settings.max_image_upload_bytes + 1)
    if len(source) > settings.max_image_upload_bytes:
        raise ProblemException(413, "image_too_large", "Размер изображения превышает 20 МБ")
    if not source:
        raise ProblemException(400, "empty_image", "Выбран пустой файл")
    cleaned_alt = alt_text.strip() if alt_text else None
    if cleaned_alt and len(cleaned_alt) > 500:
        raise ProblemException(400, "alt_text_too_long", "Описание изображения слишком длинное")

    optimized, width, height = await run_in_threadpool(optimize_question_image, source)
    asset_id = uuid4()
    now = datetime.now(UTC)
    object_key = f"questions/{now:%Y/%m}/{asset_id}.webp"
    target = settings.media_root.resolve() / Path(object_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(optimized)
    checksum = sha256(optimized).hexdigest()
    try:
        await session.execute(
            text(
                """
                INSERT INTO app.media_assets (
                    id, object_key, original_filename, mime_type, byte_size,
                    width, height, alt_text, checksum_sha256, uploaded_by
                ) VALUES (
                    :id, :object_key, :filename, 'image/webp', :byte_size,
                    :width, :height, :alt_text, :checksum, :uploaded_by
                )
                """
            ),
            {
                "id": asset_id,
                "object_key": object_key,
                "filename": file.filename or "image",
                "byte_size": len(optimized),
                "width": width,
                "height": height,
                "alt_text": cleaned_alt,
                "checksum": checksum,
                "uploaded_by": admin.user_id,
            },
        )
        await session.commit()
    except Exception:
        target.unlink(missing_ok=True)
        await session.rollback()
        raise

    saved_percent = max(0, round((1 - len(optimized) / len(source)) * 100))
    return {
        "id": asset_id,
        "url": f"/media/{object_key}",
        "filename": file.filename or "image",
        "mimeType": "image/webp",
        "byteSize": len(optimized),
        "originalByteSize": len(source),
        "savedPercent": saved_percent,
        "width": width,
        "height": height,
        "altText": cleaned_alt,
        "checksumSha256": checksum,
        "createdAt": now,
    }
