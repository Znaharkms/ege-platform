from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from fastapi.responses import ORJSONResponse


@dataclass(slots=True)
class ProblemException(Exception):
    status: int
    code: str
    title: str
    detail: str | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)


async def problem_exception_handler(request: Request, exc: ProblemException) -> ORJSONResponse:
    trace_id = getattr(request.state, "request_id", "unknown")
    body: dict[str, Any] = {
        "type": f"https://api.ege-olesyablok.ru/problems/{exc.code}",
        "title": exc.title,
        "status": exc.status,
        "code": exc.code,
        "traceId": trace_id,
        "instance": str(request.url.path),
    }
    if exc.detail:
        body["detail"] = exc.detail
    if exc.errors:
        body["errors"] = exc.errors
    return ORJSONResponse(
        body,
        status_code=exc.status,
        media_type="application/problem+json",
    )

