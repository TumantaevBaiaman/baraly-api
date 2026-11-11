from uuid import UUID

from fastapi import APIRouter, status

from app.presentation.api.dependencies import (
    BrowseCatalogDep,
    CurrentUserDep,
    ManageOwnReviewsDep,
)
from app.presentation.api.v1.schemas import (
    DepartureResponse,
    DeparturesQueryDep,
    LanguageResponse,
    LocationResponse,
    OperatorResponse,
    OperatorReviewsQueryDep,
    PageQueryDep,
    PageResponse,
    ReviewPhotoResponse,
    ReviewRequest,
    ReviewResponse,
    TagResponse,
    TourCardResponse,
    TourResponse,
    TourSearchQueryDep,
)

router = APIRouter()


@router.get("/tours", tags=["tours"])
async def search_tours(
    query: TourSearchQueryDep, use_case: BrowseCatalogDep
) -> PageResponse[TourCardResponse]:
    page = await use_case.search_tours(query.to_filters(), query.to_params())
    return PageResponse[TourCardResponse].model_validate(page)


@router.get("/tours/{tour_id}", tags=["tours"])
async def get_tour(tour_id: UUID, use_case: BrowseCatalogDep) -> TourResponse:
    return TourResponse.model_validate(await use_case.get_tour(tour_id))


@router.get("/tours/{tour_id}/reviews", tags=["reviews"])
async def list_tour_reviews(
    tour_id: UUID, query: PageQueryDep, use_case: BrowseCatalogDep
) -> PageResponse[ReviewResponse]:
    page = await use_case.list_tour_reviews(tour_id, query.to_params())
    return PageResponse[ReviewResponse].model_validate(page)


@router.get("/tours/{tour_id}/departures", tags=["tours"])
async def list_tour_departures(
    tour_id: UUID, query: DeparturesQueryDep, use_case: BrowseCatalogDep
) -> list[DepartureResponse]:
    """Даты выездов для календаря с ценой за человека и числом мест."""
    departures = await use_case.list_departures(tour_id, query.date_from, query.date_to)
    return [DepartureResponse.model_validate(item) for item in departures]


@router.get("/tours/{tour_id}/reviews/photos", tags=["reviews"])
async def list_tour_review_photos(
    tour_id: UUID, query: PageQueryDep, use_case: BrowseCatalogDep
) -> PageResponse[ReviewPhotoResponse]:
    """Все фото из отзывов о туре, для блока «26 photos»."""
    page = await use_case.list_tour_review_photos(tour_id, query.to_params())
    return PageResponse[ReviewPhotoResponse].model_validate(page)


@router.put("/tours/{tour_id}/reviews/me", tags=["reviews"])
async def save_my_tour_review(
    tour_id: UUID, body: ReviewRequest, user: CurrentUserDep, use_case: ManageOwnReviewsDep
) -> ReviewResponse:
    review = await use_case.save_tour_review(tour_id, user.id, body.to_content())
    return ReviewResponse.model_validate(review)


@router.delete(
    "/tours/{tour_id}/reviews/me", status_code=status.HTTP_204_NO_CONTENT, tags=["reviews"]
)
async def delete_my_tour_review(
    tour_id: UUID, user: CurrentUserDep, use_case: ManageOwnReviewsDep
) -> None:
    await use_case.delete_tour_review(tour_id, user.id)


@router.put("/tour-operators/{operator_id}/reviews/me", tags=["reviews"])
async def save_my_company_review(
    operator_id: UUID, body: ReviewRequest, user: CurrentUserDep, use_case: ManageOwnReviewsDep
) -> ReviewResponse:
    review = await use_case.save_company_review(operator_id, user.id, body.to_content())
    return ReviewResponse.model_validate(review)


@router.delete(
    "/tour-operators/{operator_id}/reviews/me",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["reviews"],
)
async def delete_my_company_review(
    operator_id: UUID, user: CurrentUserDep, use_case: ManageOwnReviewsDep
) -> None:
    await use_case.delete_company_review(operator_id, user.id)


@router.get("/tour-operators", tags=["tour-operators"])
async def list_operators(
    query: PageQueryDep, use_case: BrowseCatalogDep
) -> PageResponse[OperatorResponse]:
    page = await use_case.list_operators(query.to_params())
    return PageResponse[OperatorResponse].model_validate(page)


@router.get("/tour-operators/{operator_id}", tags=["tour-operators"])
async def get_operator(operator_id: UUID, use_case: BrowseCatalogDep) -> OperatorResponse:
    return OperatorResponse.model_validate(await use_case.get_operator(operator_id))


@router.get("/tour-operators/{operator_id}/tours", tags=["tour-operators"])
async def list_operator_tours(
    operator_id: UUID, query: TourSearchQueryDep, use_case: BrowseCatalogDep
) -> PageResponse[TourCardResponse]:
    page = await use_case.list_operator_tours(operator_id, query.to_filters(), query.to_params())
    return PageResponse[TourCardResponse].model_validate(page)


@router.get("/tour-operators/{operator_id}/reviews", tags=["tour-operators"])
async def list_operator_reviews(
    operator_id: UUID, query: OperatorReviewsQueryDep, use_case: BrowseCatalogDep
) -> PageResponse[ReviewResponse]:
    """Отзывы о компании и о её турах."""
    page = await use_case.list_operator_reviews(operator_id, query.to_params(), query.kind)
    return PageResponse[ReviewResponse].model_validate(page)


@router.get("/tags", tags=["reference"])
async def list_tags(use_case: BrowseCatalogDep) -> list[TagResponse]:
    return [TagResponse.model_validate(item) for item in await use_case.list_tags()]


@router.get("/languages", tags=["reference"])
async def list_languages(use_case: BrowseCatalogDep) -> list[LanguageResponse]:
    return [LanguageResponse.model_validate(item) for item in await use_case.list_languages()]


@router.get("/locations", tags=["reference"])
async def list_locations(use_case: BrowseCatalogDep) -> list[LocationResponse]:
    return [LocationResponse.model_validate(item) for item in await use_case.list_locations()]
