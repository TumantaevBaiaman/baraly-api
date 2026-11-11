from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from app.application.dto import (
    CompanyMembershipView,
    InvitationView,
    InviteResult,
    LocationView,
    MemberView,
    OperatorAdminView,
    Page,
    PageParams,
    ReviewFilters,
    ReviewView,
    TourCard,
    TourDetail,
    TourFilters,
)
from app.application.exceptions import AccessDeniedError
from app.application.ports.auth import PasswordHasher
from app.application.ports.company import InvitationRepository, MembershipRepository
from app.application.ports.storage import ImageStorage
from app.application.ports.tours import CatalogReader, ReviewRepository
from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.users import UserRepository
from app.application.use_cases.admin_catalog import (
    ManageOperators,
    ManageReferenceData,
    ManageTours,
)
from app.application.use_cases.images import store_image
from app.domain.company import CompanyPermission, CompanyRole, role_allows
from app.domain.exceptions import ConflictError, NotFoundError
from app.domain.tour import OperatorContent, TourContent, TourModerationStatus
from app.domain.user import User, normalize_email, validate_password


class CompanyAccess:
    """Проверяет, что пользователь работает в компании и его роль позволяет действие."""

    def __init__(self, memberships: MembershipRepository, reader: CatalogReader) -> None:
        self._memberships = memberships
        self._reader = reader

    async def require(
        self, user: User, operator_id: UUID, permission: CompanyPermission | None = None
    ) -> None:
        if user.is_admin:
            if await self._reader.get_operator(operator_id, include_inactive=True) is None:
                raise NotFoundError("Tour operator not found")
            return
        role = await self._memberships.get_role(operator_id, user.id)
        if role is None:
            raise AccessDeniedError("You do not have access to this company")
        operator = await self._reader.get_operator(operator_id, include_inactive=True)
        if operator is None or not operator.is_active:
            raise AccessDeniedError("Company is deactivated by the platform")
        if permission is not None and not role_allows(role, permission):
            raise AccessDeniedError("Your role does not allow this action")


class ListMyCompanies:
    def __init__(self, memberships: MembershipRepository) -> None:
        self._memberships = memberships

    async def execute(self, user: User) -> list[CompanyMembershipView]:
        return await self._memberships.list_for_user(user.id)


class ManageCompanyProfile:
    def __init__(
        self, access: CompanyAccess, operators: ManageOperators, reviews: ReviewRepository
    ) -> None:
        self._access = access
        self._operators = operators
        self._reviews = reviews

    async def get(self, user: User, operator_id: UUID) -> OperatorAdminView:
        await self._access.require(user, operator_id)
        return await self._operators.get(operator_id)

    async def update(
        self, user: User, operator_id: UUID, content: OperatorContent
    ) -> OperatorAdminView:
        await self._access.require(user, operator_id, CompanyPermission.MANAGE_PROFILE)
        return await self._operators.update(operator_id, content)

    async def list_reviews(
        self, user: User, operator_id: UUID, filters: ReviewFilters, page: PageParams
    ) -> Page[ReviewView]:
        """Компания видит все отзывы о себе и своих турах, включая скрытые админом."""
        await self._access.require(user, operator_id)
        scoped = replace(filters, operator_id=operator_id)
        return await self._reviews.search(scoped, page, visible_only=False)


class ManageCompanyMembers:
    def __init__(
        self,
        access: CompanyAccess,
        memberships: MembershipRepository,
        invitations: InvitationRepository,
        users: UserRepository,
        unit_of_work: UnitOfWork,
        hasher: PasswordHasher,
    ) -> None:
        self._access = access
        self._memberships = memberships
        self._invitations = invitations
        self._users = users
        self._unit_of_work = unit_of_work
        self._hasher = hasher

    async def list_members(self, user: User, operator_id: UUID) -> list[MemberView]:
        await self._access.require(user, operator_id)
        return await self._memberships.list_members(operator_id)

    async def list_invitations(self, user: User, operator_id: UUID) -> list[InvitationView]:
        await self._access.require(user, operator_id, CompanyPermission.MANAGE_MEMBERS)
        return await self._invitations.list(operator_id)

    async def invite(
        self,
        user: User,
        operator_id: UUID,
        email: str,
        role: CompanyRole,
        password: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> InviteResult:
        """Добавляет сотрудника в компанию.

        - email уже зарегистрирован: доступ выдаётся сразу, человек входит как раньше;
        - новый email с паролем: создаётся аккаунт для входа в кабинет по паролю;
        - новый email без пароля: приглашение ждёт первого входа через Google или Apple.

        Пароль задаётся только новому аккаунту: иначе владелец компании мог бы
        поставить свой пароль на чужой существующий аккаунт."""
        await self._access.require(user, operator_id, CompanyPermission.MANAGE_MEMBERS)
        email = normalize_email(email)
        now = datetime.now(UTC)
        invitee = await self._users.get_by_email(email)
        status: Literal["member", "created", "invited"]
        if invitee is not None:
            if password:
                raise ConflictError(
                    "This email already has an account; add it without a password, "
                    "the person signs in as before"
                )
            if await self._memberships.get_role(operator_id, invitee.id) is not None:
                raise ConflictError("User is already a member of this company")
            await self._memberships.add(operator_id, invitee.id, role, now)
            status = "member"
        elif password:
            validate_password(password)
            account = User.register(
                email=email, first_name=first_name, last_name=last_name, now=now
            )
            await self._users.add(account)
            await self._users.set_password_hash(account.id, self._hasher.hash(password))
            await self._memberships.add(operator_id, account.id, role, now)
            status = "created"
        else:
            await self._invitations.create(operator_id, email, role, user.id, now)
            status = "invited"
        await self._unit_of_work.commit()
        return InviteResult(status=status, email=email, role=role)

    async def change_role(
        self, user: User, operator_id: UUID, member_id: UUID, role: CompanyRole
    ) -> None:
        await self._access.require(user, operator_id, CompanyPermission.MANAGE_MEMBERS)
        current = await self._require_member(operator_id, member_id)
        if current is CompanyRole.OWNER and role is not CompanyRole.OWNER:
            await self._ensure_another_owner(operator_id, member_id)
        await self._memberships.set_role(operator_id, member_id, role)
        await self._unit_of_work.commit()

    async def remove(self, user: User, operator_id: UUID, member_id: UUID) -> None:
        await self._access.require(user, operator_id, CompanyPermission.MANAGE_MEMBERS)
        if await self._require_member(operator_id, member_id) is CompanyRole.OWNER:
            await self._ensure_another_owner(operator_id, member_id)
        await self._memberships.remove(operator_id, member_id)
        await self._unit_of_work.commit()

    async def cancel_invitation(self, user: User, operator_id: UUID, invitation_id: UUID) -> None:
        await self._access.require(user, operator_id, CompanyPermission.MANAGE_MEMBERS)
        if not await self._invitations.delete(operator_id, invitation_id):
            raise NotFoundError("Invitation not found")
        await self._unit_of_work.commit()

    async def _require_member(self, operator_id: UUID, member_id: UUID) -> CompanyRole:
        role = await self._memberships.get_role(operator_id, member_id)
        if role is None:
            raise NotFoundError("Member not found")
        return role

    async def _ensure_another_owner(self, operator_id: UUID, member_id: UUID) -> None:
        # Блокировка строк владельцев не даёт двум запросам одновременно убрать последних.
        owners = await self._memberships.lock_owner_ids(operator_id)
        if not any(owner != member_id for owner in owners):
            raise ConflictError("Company must keep at least one owner")


class ManageCompanyTours:
    def __init__(
        self,
        access: CompanyAccess,
        tours: ManageTours,
        reference: ManageReferenceData,
        reader: CatalogReader,
    ) -> None:
        self._access = access
        self._tours = tours
        self._reference = reference
        self._reader = reader

    async def search(
        self, user: User, operator_id: UUID, filters: TourFilters, page: PageParams
    ) -> Page[TourCard]:
        await self._access.require(user, operator_id, CompanyPermission.MANAGE_TOURS)
        return await self._tours.search(replace(filters, operator_id=operator_id), page)

    async def get(self, user: User, operator_id: UUID, tour_id: UUID) -> TourDetail:
        await self._access.require(user, operator_id, CompanyPermission.MANAGE_TOURS)
        return await self._own_tour(operator_id, tour_id)

    async def create(self, user: User, operator_id: UUID, content: TourContent) -> TourDetail:
        await self._access.require(user, operator_id, CompanyPermission.MANAGE_TOURS)
        self._ensure_same_company(operator_id, content)
        status = await self._moderation_status(user, operator_id)
        return await self._tours.create(content, status)

    async def update(
        self, user: User, operator_id: UUID, tour_id: UUID, content: TourContent
    ) -> TourDetail:
        await self._access.require(user, operator_id, CompanyPermission.MANAGE_TOURS)
        self._ensure_same_company(operator_id, content)
        await self._own_tour(operator_id, tour_id)
        status = await self._moderation_status(user, operator_id)
        return await self._tours.update(tour_id, content, status)

    async def delete(self, user: User, operator_id: UUID, tour_id: UUID) -> None:
        await self._access.require(user, operator_id, CompanyPermission.MANAGE_TOURS)
        await self._own_tour(operator_id, tour_id)
        await self._tours.delete(tour_id)

    async def add_location(
        self,
        user: User,
        operator_id: UUID,
        name: str,
        latitude: float | None,
        longitude: float | None,
    ) -> LocationView:
        """Локации общие для всех компаний, добавлять их может любая компания."""
        await self._access.require(user, operator_id, CompanyPermission.MANAGE_TOURS)
        return await self._reference.add_location(name, latitude, longitude)

    async def _moderation_status(self, user: User, operator_id: UUID) -> TourModerationStatus:
        """Проверенная компания и сотрудники платформы публикуют без модерации."""
        if user.is_staff:
            return TourModerationStatus.APPROVED
        operator = await self._reader.get_operator(operator_id, include_inactive=True)
        if operator is not None and operator.is_verified:
            return TourModerationStatus.APPROVED
        return TourModerationStatus.PENDING

    async def _own_tour(self, operator_id: UUID, tour_id: UUID) -> TourDetail:
        tour = await self._tours.get(tour_id)
        if tour.operator.id != operator_id:
            raise NotFoundError("Tour not found")
        return tour

    @staticmethod
    def _ensure_same_company(operator_id: UUID, content: TourContent) -> None:
        if content.operator_id != operator_id:
            raise AccessDeniedError("Tour must belong to this company")


class UploadCompanyImage:
    def __init__(self, access: CompanyAccess, storage: ImageStorage, max_bytes: int) -> None:
        self._access = access
        self._storage = storage
        self._max_bytes = max_bytes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    async def execute(self, user: User, operator_id: UUID, data: bytes) -> str:
        await self._access.require(user, operator_id, CompanyPermission.MANAGE_TOURS)
        return await store_image(self._storage, self._max_bytes, f"operators/{operator_id}/", data)
