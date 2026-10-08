import os

os.environ.setdefault("EGE_JWT_SECRET", "test-secret-that-is-longer-than-thirty-two-characters")
os.environ.setdefault(
    "EGE_DATABASE_URL",
    "postgresql+asyncpg://ege:ege_dev_password@localhost:5432/ege",
)
os.environ.setdefault("EGE_OPENAPI_CONTRACT_PATH", "../api/openapi.yaml")

