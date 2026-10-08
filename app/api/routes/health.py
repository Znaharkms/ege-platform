from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from app import __version__

router = APIRouter(tags=["System"])


@router.get("/health", operation_id="getHealth", include_in_schema=False)
async def get_health() -> dict[str, str]:
    return {
        "status": "ok",
        "version": __version__,
        "time": datetime.now(UTC).isoformat(),
    }

