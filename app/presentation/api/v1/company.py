from uuid import UUID

from fastapi import APIRouter, File, UploadFile, status

from app.presentation.api.dependencies import (
    CurrentUserDep,
    ManageCompanyMembersDep,
    ManageCompanyProfileDep,
    ManageCompanyToursDep,
    UploadCompanyImageDep,
)
from app.presentation.api.v1.schemas import (
    ChangeRoleRequest,
    CompanyTourRequest,
    CompanyTourSearchQueryDep,
    ImageUploadResponse,
    InvitationResponse,
    InviteMemberRequest,
    InviteResultResponse,
    LocationRequest,
    LocationResponse,
    MemberResponse,
    OperatorAdminResponse,
    OperatorRequest,
    PageResponse,
    ReviewResponse,
    ReviewSearchQueryDep,
    TourCardResponse,
    TourResponse,
)

router = APIRouter(prefix="/company/{operator_id}", tags=["company"])


@router.get("")
async def get_company(
    operator_id: UUID, user: CurrentUserDep, use_case: ManageCompanyProfileDep
) -> OperatorAdminResponse:
    return OperatorAdminResponse.model_validate(await use_case.get(user, operator_id))


@router.put("")
async def update_company(
    operator_id: UUID,
    body: OperatorRequest,
    user: CurrentUserDep,
    use_case: ManageCompanyProfileDep,
) -> OperatorAdminResponse:
    operator = await use_case.update(user, operator_id, body.to_content())
    return OperatorAdminResponse.model_validate(operator)


@router.get("/reviews")
async def list_company_reviews(
    operator_id: UUID,
    query: ReviewSearchQueryDep,
    user: CurrentUserDep,
    use_case: ManageCompanyProfileDep,
) -> PageResponse[ReviewResponse]:
    page = await use_case.list_reviews(user, operator_id, query.to_filters(), query.to_params())
    return PageResponse[ReviewResponse].model_validate(page)


@router.get("/members")
async def list_members(
    operator_id: UUID, user: CurrentUserDep, use_case: ManageCompanyMembersDep
) -> list[MemberResponse]:
    members = await use_case.list_members(user, operator_id)
    return [MemberResponse.model_validate(item) for item in members]


@router.post("/members", status_code=status.HTTP_201_CREATED)
async def invite_member(
    operator_id: UUID,
    body: InviteMemberRequest,
    user: CurrentUserDep,
    use_case: ManageCompanyMembersDep,
) -> InviteResultResponse:
    result = await use_case.invite(
        user,
        operator_id,
        body.email,
        body.role,
        password=body.password,
        first_name=body.first_name,
        last_name=body.last_name,
    )
    return InviteResultResponse.model_validate(result)


@router.patch("/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def change_member_role(
    operator_id: UUID,
    member_id: UUID,
    body: ChangeRoleRequest,
    user: CurrentUserDep,
    use_case: ManageCompanyMembersDep,
) -> None:
    await use_case.change_role(user, operator_id, member_id, body.role)


@router.delete("/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    operator_id: UUID, member_id: UUID, user: CurrentUserDep, use_case: ManageCompanyMembersDep
) -> None:
    await use_case.remove(user, operator_id, member_id)


@router.get("/invitations")
async def list_invitations(
    operator_id: UUID, user: CurrentUserDep, use_case: ManageCompanyMembersDep
) -> list[InvitationResponse]:
    invitations = await use_case.list_invitations(user, operator_id)
    return [InvitationResponse.model_validate(item) for item in invitations]


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_invitation(
    operator_id: UUID,
    invitation_id: UUID,
    user: CurrentUserDep,
    use_case: ManageCompanyMembersDep,
) -> None:
    await use_case.cancel_invitation(user, operator_id, invitation_id)


@router.get("/tours")
async def list_company_tours(
    operator_id: UUID,
    query: CompanyTourSearchQueryDep,
    user: CurrentUserDep,
    use_case: ManageCompanyToursDep,
) -> PageResponse[TourCardResponse]:
    page = await use_case.search(user, operator_id, query.to_company_filters(), query.to_params())
    return PageResponse[TourCardResponse].model_validate(page)


@router.post("/tours", status_code=status.HTTP_201_CREATED)
async def create_company_tour(
    operator_id: UUID,
    body: CompanyTourRequest,
    user: CurrentUserDep,
    use_case: ManageCompanyToursDep,
) -> TourResponse:
    tour = await use_case.create(user, operator_id, body.build(operator_id))
    return TourResponse.model_validate(tour)


@router.get("/tours/{tour_id}")
async def get_company_tour(
    operator_id: UUID, tour_id: UUID, user: CurrentUserDep, use_case: ManageCompanyToursDep
) -> TourResponse:
    return TourResponse.model_validate(await use_case.get(user, operator_id, tour_id))


@router.put("/tours/{tour_id}")
async def update_company_tour(
    operator_id: UUID,
    tour_id: UUID,
    body: CompanyTourRequest,
    user: CurrentUserDep,
    use_case: ManageCompanyToursDep,
) -> TourResponse:
    tour = await use_case.update(user, operator_id, tour_id, body.build(operator_id))
    return TourResponse.model_validate(tour)


@router.delete("/tours/{tour_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company_tour(
    operator_id: UUID, tour_id: UUID, user: CurrentUserDep, use_case: ManageCompanyToursDep
) -> None:
    await use_case.delete(user, operator_id, tour_id)


@router.post("/locations", status_code=status.HTTP_201_CREATED)
async def add_location(
    operator_id: UUID,
    body: LocationRequest,
    user: CurrentUserDep,
    use_case: ManageCompanyToursDep,
) -> LocationResponse:
    location = await use_case.add_location(
        user, operator_id, body.name, body.latitude, body.longitude
    )
    return LocationResponse.model_validate(location)


@router.post("/images", status_code=status.HTTP_201_CREATED)
async def upload_image(
    operator_id: UUID,
    user: CurrentUserDep,
    use_case: UploadCompanyImageDep,
    file: UploadFile = File(description="JPEG, PNG или WebP"),
) -> ImageUploadResponse:
    # Читаем на байт больше лимита, чтобы отличить большой файл, не загружая его целиком.
    data = await file.read(use_case.max_bytes + 1)
    return ImageUploadResponse(url=await use_case.execute(user, operator_id, data))
