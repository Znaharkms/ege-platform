FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY backend /srv/backend
RUN pip install --upgrade pip && pip install "/srv/backend[dev]"

COPY api /srv/api
COPY database /srv/database

WORKDIR /srv/backend

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
