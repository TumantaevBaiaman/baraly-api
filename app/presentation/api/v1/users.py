from datetime import datetime
from typing import cast
from uuid import UUID

from fastapi import APIRouter, File, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from app.domain.user import MAX_NAME_LENGTH, ProfileUpdate, User, UserRole
from app.presentation.api.dependencies import (
    ChangeAvatarDep,
    ChangeOwnPasswordDep,
    CurrentUserDep,
    ListMyCompaniesDep,
    ManageFavoritesDep,
    ManageOwnReviewsDep,
    UpdateProfileDep,
    UploadUserImageDep,
)
from app.presentation.api.v1.schemas import (
    ChangePasswordRequest,
    CompanyMembershipResponse,
    ImageUploadResponse,
    PageQueryDep,
    PageResponse,
    ReviewResponse,
    TourCardResponse,
)

router = APIRouter(prefix="/users", tags=["users"])


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    first_name: str | None
    last_name: str | None
    phone: str | None
    avatar_url: str | None
    role: UserRole
    created_at: datetime


class UpdateProfileRequest(BaseModel):
    """Email приходит от провайдера и через этот запрос не меняется."""

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)
    last_name: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)
    phone: str | None = Field(default=None, max_length=32, examples=["+996502800202"])


def _to_response(user: User) -> UserResponse:
    return UserResponse.model_validate(user)


@router.get("/me")
async def get_me(user: CurrentUserDep) -> UserResponse:
    return _to_response(user)


@router.patch("/me")
async def update_me(
    body: UpdateProfileRequest, user: CurrentUserDep, use_case: UpdateProfileDep
) -> UserResponse:
    changes = cast(ProfileUpdate, body.model_dump(exclude_unset=True))
    return _to_response(await use_case.execute(user, changes))


@router.get("/me/favorites", tags=["favorites"])
async def list_favorites(
    query: PageQueryDep, user: CurrentUserDep, use_case: ManageFavoritesDep
) -> PageResponse[TourCardResponse]:
    page = await use_case.list_tours(user.id, query.to_params())
    return PageResponse[TourCardResponse].model_validate(page)


@router.get("/me/favorites/ids", tags=["favorites"])
async def list_favorite_ids(user: CurrentUserDep, use_case: ManageFavoritesDep) -> list[UUID]:
    return await use_case.list_tour_ids(user.id)


@router.put("/me/favorites/{tour_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["favorites"])
async def add_favorite(tour_id: UUID, user: CurrentUserDep, use_case: ManageFavoritesDep) -> None:
    await use_case.add(user.id, tour_id)


@router.delete(
    "/me/favorites/{tour_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["favorites"]
)
async def remove_favorite(
    tour_id: UUID, user: CurrentUserDep, use_case: ManageFavoritesDep
) -> None:
    await use_case.remove(user.id, tour_id)


@router.get("/me/reviews", tags=["reviews"])
async def list_my_reviews(
    query: PageQueryDep, user: CurrentUserDep, use_case: ManageOwnReviewsDep
) -> PageResponse[ReviewResponse]:
    page = await use_case.list_mine(user.id, query.to_params())
    return PageResponse[ReviewResponse].model_validate(page)


@router.get("/me/companies", tags=["company"])
async def list_my_companies(
    user: CurrentUserDep, use_case: ListMyCompaniesDep
) -> list[CompanyMembershipResponse]:
    companies = await use_case.execute(user)
    return [CompanyMembershipResponse.model_validate(item) for item in companies]


@router.post("/me/images", status_code=status.HTTP_201_CREATED, tags=["reviews"])
async def upload_my_image(
    user: CurrentUserDep,
    use_case: UploadUserImageDep,
    file: UploadFile = File(description="JPEG, PNG или WebP"),
) -> ImageUploadResponse:
    """Фото для отзыва: ссылку из ответа передайте в photo_urls."""
    data = await file.read(use_case.max_bytes + 1)
    return ImageUploadResponse(url=await use_case.execute(user, data))


@router.post("/me/avatar")
async def upload_avatar(
    user: CurrentUserDep,
    use_case: ChangeAvatarDep,
    file: UploadFile = File(description="JPEG, PNG или WebP"),
) -> UserResponse:
    data = await file.read(use_case.max_bytes + 1)
    return _to_response(await use_case.upload(user, data))


@router.delete("/me/avatar")
async def remove_avatar(user: CurrentUserDep, use_case: ChangeAvatarDep) -> UserResponse:
    return _to_response(await use_case.remove(user))


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_my_password(
    body: ChangePasswordRequest, user: CurrentUserDep, use_case: ChangeOwnPasswordDep
) -> None:
    """Для сотрудников с паролем. После смены нужно войти заново на всех устройствах."""
    await use_case.execute(user, body.current_password, body.new_password)
