"""Админка платформы.

Модератор видит все туры, одобряет или отклоняет их и скрывает с сайта.
Остальное доступно только админу. Сотрудниками и турами конкретной компании админ
управляет через /company/{operator_id}/...: эти запросы пропускают администратора."""

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.domain.company import CompanyRole
from app.presentation.api.dependencies import (
    CurrentAdminDep,
    CurrentStaffDep,
    ManageCompanyMembersDep,
    ManageOperatorsDep,
    ManageReferenceDataDep,
    ManageToursDep,
    ManageUsersDep,
    ModerateReviewsDep,
    get_current_admin,
    get_current_staff,
)
from app.presentation.api.v1.schemas import (
    AdminReviewResponse,
    AdminReviewSearchQueryDep,
    AdminTourSearchQueryDep,
    AdminUserResponse,
    CreateOperatorRequest,
    CreateStaffRequest,
    LanguageRequest,
    LanguageResponse,
    LocationRequest,
    LocationResponse,
    OperatorAdminResponse,
    OperatorRequest,
    OperatorSearchQueryDep,
    OperatorStatusRequest,
    OperatorVerificationRequest,
    PageResponse,
    ReviewModerationRequest,
    SetPasswordRequest,
    TagRequest,
    TagResponse,
    TourApprovalRequest,
    TourCardResponse,
    TourModerationRequest,
    TourRequest,
    TourResponse,
    UserRoleRequest,
    UserSearchQueryDep,
)

moderation_router = APIRouter(
    prefix="/admin", tags=["moderation"], dependencies=[Depends(get_current_staff)]
)
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])


@moderation_router.get("/tours")
async def search_tours(
    query: AdminTourSearchQueryDep, use_case: ManageToursDep
) -> PageResponse[TourCardResponse]:
    """Все туры всех компаний. Очередь модерации: moderation_status=pending."""
    page = await use_case.search(query.to_admin_filters(), query.to_params())
    return PageResponse[TourCardResponse].model_validate(page)


@moderation_router.get("/tours/{tour_id}")
async def get_tour(tour_id: UUID, use_case: ManageToursDep) -> TourResponse:
    return TourResponse.model_validate(await use_case.get(tour_id))


@moderation_router.patch("/tours/{tour_id}/approval")
async def review_tour(
    tour_id: UUID, body: TourApprovalRequest, moderator: CurrentStaffDep, use_case: ManageToursDep
) -> TourResponse:
    tour = await use_case.review(tour_id, body.status, body.comment, moderator)
    return TourResponse.model_validate(tour)


@moderation_router.patch("/tours/{tour_id}/moderation")
async def block_tour(
    tour_id: UUID, body: TourModerationRequest, use_case: ManageToursDep
) -> TourResponse:
    """Скрыть тур с сайта с причиной или вернуть его."""
    tour = await use_case.set_blocked(tour_id, body.is_blocked, body.reason)
    return TourResponse.model_validate(tour)


@router.post("/tours", status_code=status.HTTP_201_CREATED)
async def create_tour(body: TourRequest, use_case: ManageToursDep) -> TourResponse:
    return TourResponse.model_validate(await use_case.create(body.to_content()))


@router.put("/tours/{tour_id}")
async def update_tour(tour_id: UUID, body: TourRequest, use_case: ManageToursDep) -> TourResponse:
    return TourResponse.model_validate(await use_case.update(tour_id, body.to_content()))


@router.delete("/tours/{tour_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tour(tour_id: UUID, use_case: ManageToursDep) -> None:
    await use_case.delete(tour_id)


@router.get("/tour-operators")
async def search_operators(
    query: OperatorSearchQueryDep, use_case: ManageOperatorsDep
) -> PageResponse[OperatorAdminResponse]:
    page = await use_case.search(query.to_filters(), query.to_params())
    return PageResponse[OperatorAdminResponse].model_validate(page)


@router.post("/tour-operators", status_code=status.HTTP_201_CREATED)
async def create_operator(
    body: CreateOperatorRequest,
    admin: CurrentAdminDep,
    use_case: ManageOperatorsDep,
    members: ManageCompanyMembersDep,
) -> OperatorAdminResponse:
    operator = await use_case.create(body.to_content())
    if body.owner_email:
        await members.invite(
            admin, operator.id, body.owner_email, CompanyRole.OWNER, password=body.owner_password
        )
        operator = await use_case.get(operator.id)
    return OperatorAdminResponse.model_validate(operator)


@router.get("/tour-operators/{operator_id}")
async def get_operator(operator_id: UUID, use_case: ManageOperatorsDep) -> OperatorAdminResponse:
    return OperatorAdminResponse.model_validate(await use_case.get(operator_id))


@router.put("/tour-operators/{operator_id}")
async def update_operator(
    operator_id: UUID, body: OperatorRequest, use_case: ManageOperatorsDep
) -> OperatorAdminResponse:
    operator = await use_case.update(operator_id, body.to_content())
    return OperatorAdminResponse.model_validate(operator)


@router.patch("/tour-operators/{operator_id}/verification")
async def set_operator_verification(
    operator_id: UUID, body: OperatorVerificationRequest, use_case: ManageOperatorsDep
) -> OperatorAdminResponse:
    """Проверенная компания публикует новые туры сразу, без модерации."""
    operator = await use_case.set_verification(operator_id, body.is_verified, body.note)
    return OperatorAdminResponse.model_validate(operator)


@router.patch("/tour-operators/{operator_id}/status")
async def set_operator_status(
    operator_id: UUID, body: OperatorStatusRequest, use_case: ManageOperatorsDep
) -> OperatorAdminResponse:
    operator = await use_case.set_active(operator_id, body.is_active)
    return OperatorAdminResponse.model_validate(operator)


@router.delete("/tour-operators/{operator_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_operator(operator_id: UUID, use_case: ManageOperatorsDep) -> None:
    await use_case.delete(operator_id)


@router.get("/users")
async def search_users(
    query: UserSearchQueryDep, use_case: ManageUsersDep
) -> PageResponse[AdminUserResponse]:
    page = await use_case.search(query.to_filters(), query.to_params())
    return PageResponse[AdminUserResponse].model_validate(page)


@router.post("/staff", status_code=status.HTTP_201_CREATED)
async def create_staff(
    body: CreateStaffRequest, admin: CurrentAdminDep, use_case: ManageUsersDep
) -> AdminUserResponse:
    """Создать модератора или админа с паролем для входа в админку."""
    user = await use_case.create_staff(
        admin,
        email=body.email,
        role=body.role,
        password=body.password,
        first_name=body.first_name,
        last_name=body.last_name,
    )
    return AdminUserResponse.model_validate(user)


@router.put("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def set_staff_password(
    user_id: UUID, body: SetPasswordRequest, admin: CurrentAdminDep, use_case: ManageUsersDep
) -> None:
    await use_case.set_password(admin, user_id, body.password)


@router.patch("/users/{user_id}/role")
async def set_user_role(
    user_id: UUID, body: UserRoleRequest, admin: CurrentAdminDep, use_case: ManageUsersDep
) -> AdminUserResponse:
    user = await use_case.set_role(admin, user_id, body.role)
    return AdminUserResponse.model_validate(user)


@router.post("/tags", status_code=status.HTTP_201_CREATED)
async def create_tag(body: TagRequest, use_case: ManageReferenceDataDep) -> TagResponse:
    return TagResponse.model_validate(await use_case.add_tag(body.slug, body.name))


@router.delete("/tags/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(slug: str, use_case: ManageReferenceDataDep) -> None:
    await use_case.delete_tag(slug)


@router.post("/languages", status_code=status.HTTP_201_CREATED)
async def create_language(
    body: LanguageRequest, use_case: ManageReferenceDataDep
) -> LanguageResponse:
    return LanguageResponse.model_validate(await use_case.add_language(body.code, body.name))


@router.delete("/languages/{code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_language(code: str, use_case: ManageReferenceDataDep) -> None:
    await use_case.delete_language(code)


@router.post("/locations", status_code=status.HTTP_201_CREATED)
async def create_location(
    body: LocationRequest, use_case: ManageReferenceDataDep
) -> LocationResponse:
    location = await use_case.add_location(body.name, body.latitude, body.longitude)
    return LocationResponse.model_validate(location)


@router.delete("/locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_location(location_id: UUID, use_case: ManageReferenceDataDep) -> None:
    await use_case.delete_location(location_id)


@router.get("/reviews")
async def search_reviews(
    query: AdminReviewSearchQueryDep, use_case: ModerateReviewsDep
) -> PageResponse[AdminReviewResponse]:
    page = await use_case.search(query.to_admin_filters(), query.to_params())
    return PageResponse[AdminReviewResponse].model_validate(page)


@router.get("/reviews/{review_id}")
async def get_review(review_id: UUID, use_case: ModerateReviewsDep) -> AdminReviewResponse:
    return AdminReviewResponse.model_validate(await use_case.get(review_id))


@router.patch("/reviews/{review_id}")
async def moderate_review(
    review_id: UUID, body: ReviewModerationRequest, use_case: ModerateReviewsDep
) -> AdminReviewResponse:
    review = await use_case.set_hidden(review_id, body.is_hidden)
    return AdminReviewResponse.model_validate(review)


@router.delete("/reviews/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review(review_id: UUID, use_case: ModerateReviewsDep) -> None:
    await use_case.delete(review_id)
