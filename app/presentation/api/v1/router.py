from fastapi import APIRouter

from app.presentation.api.v1.admin import moderation_router
from app.presentation.api.v1.admin import router as admin_router
from app.presentation.api.v1.auth import router as auth_router
from app.presentation.api.v1.bookings import router as bookings_router
from app.presentation.api.v1.company import router as company_router
from app.presentation.api.v1.tours import router as tours_router
from app.presentation.api.v1.users import router as users_router

router = APIRouter(prefix="/api/v1")
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(tours_router)
router.include_router(bookings_router)
router.include_router(company_router)
router.include_router(moderation_router)
router.include_router(admin_router)
