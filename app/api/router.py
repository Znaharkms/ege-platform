from app.api.routes.admin_students import router as admin_students_router
from fastapi import APIRouter

from app.api.routes.admin_content import router as admin_content_router
from app.api.routes.admin_media import router as admin_media_router
from app.api.routes.admin_practice import router as admin_practice_router
from app.api.routes.auth import router as auth_router
from app.api.routes.catalog import router as catalog_router
from app.api.routes.health import router as health_router
from app.api.routes.practice import router as practice_router
from app.api.routes.profile import router as profile_router
from app.api.routes.reference_books import router as reference_books_router
from app.api.routes.student_dashboard import router as student_dashboard_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(admin_content_router)
api_router.include_router(admin_media_router)
api_router.include_router(admin_practice_router)
api_router.include_router(auth_router)
api_router.include_router(catalog_router)
api_router.include_router(profile_router)
api_router.include_router(practice_router)
api_router.include_router(reference_books_router)

api_router.include_router(student_dashboard_router)

api_router.include_router(admin_students_router)

from app.api.routes.promotions import router as promotions_router
api_router.include_router(promotions_router)
