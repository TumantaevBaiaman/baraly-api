from collections.abc import AsyncIterator, Mapping
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.application.exceptions import AccessDeniedError, AuthenticationError
from app.application.ports.auth import IdentityVerifier, PasswordHasher, TokenService
from app.application.ports.bookings import PaymentGateway
from app.application.ports.storage import ImageStorage
from app.application.use_cases.admin_catalog import (
    ManageOperators,
    ManageReferenceData,
    ManageTours,
    ModerateReviews,
)
from app.application.use_cases.auth import (
    PasswordSignIn,
    RefreshSession,
    SignInWithProvider,
    SignOut,
)
from app.application.use_cases.bookings import (
    CancelBooking,
    ConfirmPayment,
    PayForBooking,
    ReserveSeats,
    ViewBookings,
)
from app.application.use_cases.catalog import BrowseCatalog
from app.application.use_cases.check_readiness import CheckReadiness
from app.application.use_cases.company import (
    CompanyAccess,
    ListMyCompanies,
    ManageCompanyMembers,
    ManageCompanyProfile,
    ManageCompanyTours,
    UploadCompanyImage,
)
from app.application.use_cases.engagement import ManageFavorites, ManageOwnReviews
from app.application.use_cases.images import UploadUserImage
from app.application.use_cases.users import (
    ChangeAvatar,
    ChangeOwnPassword,
    GetCurrentUser,
    ManageUsers,
    UpdateProfile,
)
from app.domain.auth import AuthProvider
from app.domain.user import User
from app.infrastructure.db.booking_repository import SqlAlchemyBookingRepository
from app.infrastructure.db.catalog_reader import SqlAlchemyCatalogReader
from app.infrastructure.db.company_repositories import (
    SqlAlchemyInvitationRepository,
    SqlAlchemyMembershipRepository,
)
from app.infrastructure.db.probe import SqlAlchemyDatabaseProbe
from app.infrastructure.db.repositories import (
    SqlAlchemyRefreshTokenRepository,
    SqlAlchemyUnitOfWork,
    SqlAlchemyUserRepository,
)
from app.infrastructure.db.tour_repositories import (
    SqlAlchemyFavoriteRepository,
    SqlAlchemyOperatorRepository,
    SqlAlchemyReferenceRepository,
    SqlAlchemyReviewRepository,
    SqlAlchemyTourRepository,
)

_bearer = HTTPBearer(auto_error=False)


def get_engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_token_service(request: Request) -> TokenService:
    service: TokenService = request.app.state.token_service
    return service


def get_image_storage(request: Request) -> ImageStorage:
    storage: ImageStorage = request.app.state.image_storage
    return storage


def get_identity_verifiers(request: Request) -> Mapping[AuthProvider, IdentityVerifier]:
    verifiers: Mapping[AuthProvider, IdentityVerifier] = request.app.state.identity_verifiers
    return verifiers


SessionDep = Annotated[AsyncSession, Depends(get_session)]
TokenServiceDep = Annotated[TokenService, Depends(get_token_service)]
IdentityVerifiersDep = Annotated[
    Mapping[AuthProvider, IdentityVerifier], Depends(get_identity_verifiers)
]


def get_check_readiness(engine: Annotated[AsyncEngine, Depends(get_engine)]) -> CheckReadiness:
    return CheckReadiness(SqlAlchemyDatabaseProbe(engine))


def get_sign_in(
    session: SessionDep, verifiers: IdentityVerifiersDep, tokens: TokenServiceDep
) -> SignInWithProvider:
    return SignInWithProvider(
        verifiers,
        SqlAlchemyUserRepository(session),
        SqlAlchemyRefreshTokenRepository(session),
        tokens,
        SqlAlchemyUnitOfWork(session),
        SqlAlchemyInvitationRepository(session),
    )


def get_refresh_session(session: SessionDep, tokens: TokenServiceDep) -> RefreshSession:
    return RefreshSession(
        SqlAlchemyUserRepository(session),
        SqlAlchemyRefreshTokenRepository(session),
        tokens,
        SqlAlchemyUnitOfWork(session),
    )


def get_sign_out(session: SessionDep, tokens: TokenServiceDep) -> SignOut:
    return SignOut(
        SqlAlchemyRefreshTokenRepository(session),
        tokens,
        SqlAlchemyUnitOfWork(session),
    )


def get_update_profile(session: SessionDep) -> UpdateProfile:
    return UpdateProfile(SqlAlchemyUserRepository(session), SqlAlchemyUnitOfWork(session))


def get_change_avatar(
    request: Request,
    session: SessionDep,
    storage: Annotated[ImageStorage, Depends(get_image_storage)],
) -> ChangeAvatar:
    return ChangeAvatar(
        SqlAlchemyUserRepository(session),
        SqlAlchemyUnitOfWork(session),
        storage,
        request.app.state.max_image_bytes,
    )


ChangeAvatarDep = Annotated[ChangeAvatar, Depends(get_change_avatar)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: SessionDep,
    tokens: TokenServiceDep,
) -> User:
    if credentials is None:
        raise AuthenticationError("Not authenticated")
    use_case = GetCurrentUser(SqlAlchemyUserRepository(session), tokens)
    return await use_case.execute(credentials.credentials)


async def get_current_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_admin:
        raise AccessDeniedError("Administrator access required")
    return user


async def get_current_staff(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Админ или модератор."""
    if not user.is_staff:
        raise AccessDeniedError("Moderator access required")
    return user


def get_password_hasher(request: Request) -> PasswordHasher:
    hasher: PasswordHasher = request.app.state.password_hasher
    return hasher


PasswordHasherDep = Annotated[PasswordHasher, Depends(get_password_hasher)]


def get_manage_users(session: SessionDep, hasher: PasswordHasherDep) -> ManageUsers:
    return ManageUsers(
        SqlAlchemyUserRepository(session),
        SqlAlchemyUnitOfWork(session),
        hasher,
        SqlAlchemyRefreshTokenRepository(session),
        SqlAlchemyMembershipRepository(session),
    )


def get_password_sign_in(
    request: Request, session: SessionDep, hasher: PasswordHasherDep, tokens: TokenServiceDep
) -> PasswordSignIn:
    return PasswordSignIn(
        SqlAlchemyUserRepository(session),
        hasher,
        SqlAlchemyRefreshTokenRepository(session),
        tokens,
        SqlAlchemyUnitOfWork(session),
        request.app.state.dummy_password_hash,
    )


def get_change_own_password(session: SessionDep, hasher: PasswordHasherDep) -> ChangeOwnPassword:
    return ChangeOwnPassword(
        SqlAlchemyUserRepository(session),
        hasher,
        SqlAlchemyRefreshTokenRepository(session),
        SqlAlchemyUnitOfWork(session),
    )


PasswordSignInDep = Annotated[PasswordSignIn, Depends(get_password_sign_in)]
ChangeOwnPasswordDep = Annotated[ChangeOwnPassword, Depends(get_change_own_password)]


ManageUsersDep = Annotated[ManageUsers, Depends(get_manage_users)]


def get_payment_gateway(request: Request) -> PaymentGateway:
    gateway: PaymentGateway = request.app.state.payment_gateway
    return gateway


def get_reserve_seats(request: Request, session: SessionDep) -> ReserveSeats:
    return ReserveSeats(
        SqlAlchemyBookingRepository(session),
        SqlAlchemyUnitOfWork(session),
        request.app.state.booking_hold,
    )


def get_pay_for_booking(
    session: SessionDep, payments: Annotated[PaymentGateway, Depends(get_payment_gateway)]
) -> PayForBooking:
    return PayForBooking(
        SqlAlchemyBookingRepository(session), payments, SqlAlchemyUnitOfWork(session)
    )


def get_confirm_payment(session: SessionDep) -> ConfirmPayment:
    return ConfirmPayment(SqlAlchemyBookingRepository(session), SqlAlchemyUnitOfWork(session))


def get_cancel_booking(session: SessionDep) -> CancelBooking:
    return CancelBooking(SqlAlchemyBookingRepository(session), SqlAlchemyUnitOfWork(session))


def get_view_bookings(session: SessionDep) -> ViewBookings:
    access = CompanyAccess(
        SqlAlchemyMembershipRepository(session), SqlAlchemyCatalogReader(session)
    )
    return ViewBookings(SqlAlchemyBookingRepository(session), access)


ReserveSeatsDep = Annotated[ReserveSeats, Depends(get_reserve_seats)]
PayForBookingDep = Annotated[PayForBooking, Depends(get_pay_for_booking)]
ConfirmPaymentDep = Annotated[ConfirmPayment, Depends(get_confirm_payment)]
CancelBookingDep = Annotated[CancelBooking, Depends(get_cancel_booking)]
ViewBookingsDep = Annotated[ViewBookings, Depends(get_view_bookings)]


def get_browse_catalog(session: SessionDep) -> BrowseCatalog:
    return BrowseCatalog(SqlAlchemyCatalogReader(session), SqlAlchemyReviewRepository(session))


def get_manage_tours(session: SessionDep) -> ManageTours:
    return ManageTours(
        SqlAlchemyTourRepository(session),
        SqlAlchemyCatalogReader(session),
        SqlAlchemyUnitOfWork(session),
    )


def get_manage_operators(session: SessionDep) -> ManageOperators:
    return ManageOperators(
        SqlAlchemyOperatorRepository(session),
        SqlAlchemyCatalogReader(session),
        SqlAlchemyUnitOfWork(session),
    )


def get_manage_reference_data(session: SessionDep) -> ManageReferenceData:
    return ManageReferenceData(
        SqlAlchemyReferenceRepository(session), SqlAlchemyUnitOfWork(session)
    )


def get_moderate_reviews(session: SessionDep) -> ModerateReviews:
    return ModerateReviews(SqlAlchemyReviewRepository(session), SqlAlchemyUnitOfWork(session))


def get_manage_favorites(session: SessionDep) -> ManageFavorites:
    return ManageFavorites(
        SqlAlchemyFavoriteRepository(session),
        SqlAlchemyCatalogReader(session),
        SqlAlchemyUnitOfWork(session),
    )


def get_manage_own_reviews(
    session: SessionDep, storage: Annotated[ImageStorage, Depends(get_image_storage)]
) -> ManageOwnReviews:
    return ManageOwnReviews(
        SqlAlchemyReviewRepository(session),
        SqlAlchemyCatalogReader(session),
        SqlAlchemyUnitOfWork(session),
        storage,
    )


BrowseCatalogDep = Annotated[BrowseCatalog, Depends(get_browse_catalog)]
ManageToursDep = Annotated[ManageTours, Depends(get_manage_tours)]
ManageOperatorsDep = Annotated[ManageOperators, Depends(get_manage_operators)]
ManageReferenceDataDep = Annotated[ManageReferenceData, Depends(get_manage_reference_data)]
ModerateReviewsDep = Annotated[ModerateReviews, Depends(get_moderate_reviews)]
ManageFavoritesDep = Annotated[ManageFavorites, Depends(get_manage_favorites)]
ManageOwnReviewsDep = Annotated[ManageOwnReviews, Depends(get_manage_own_reviews)]
CheckReadinessDep = Annotated[CheckReadiness, Depends(get_check_readiness)]


def get_company_access(session: SessionDep) -> CompanyAccess:
    return CompanyAccess(SqlAlchemyMembershipRepository(session), SqlAlchemyCatalogReader(session))


CompanyAccessDep = Annotated[CompanyAccess, Depends(get_company_access)]


def get_list_my_companies(session: SessionDep) -> ListMyCompanies:
    return ListMyCompanies(SqlAlchemyMembershipRepository(session))


def get_manage_company_profile(
    session: SessionDep, access: CompanyAccessDep
) -> ManageCompanyProfile:
    return ManageCompanyProfile(
        access, get_manage_operators(session), SqlAlchemyReviewRepository(session)
    )


def get_manage_company_members(
    session: SessionDep, access: CompanyAccessDep, hasher: PasswordHasherDep
) -> ManageCompanyMembers:
    return ManageCompanyMembers(
        access,
        SqlAlchemyMembershipRepository(session),
        SqlAlchemyInvitationRepository(session),
        SqlAlchemyUserRepository(session),
        SqlAlchemyUnitOfWork(session),
        hasher,
    )


def get_manage_company_tours(session: SessionDep, access: CompanyAccessDep) -> ManageCompanyTours:
    return ManageCompanyTours(
        access,
        get_manage_tours(session),
        get_manage_reference_data(session),
        SqlAlchemyCatalogReader(session),
    )


def get_upload_company_image(
    request: Request,
    access: CompanyAccessDep,
    storage: Annotated[ImageStorage, Depends(get_image_storage)],
) -> UploadCompanyImage:
    return UploadCompanyImage(access, storage, request.app.state.max_image_bytes)


ListMyCompaniesDep = Annotated[ListMyCompanies, Depends(get_list_my_companies)]
ManageCompanyProfileDep = Annotated[ManageCompanyProfile, Depends(get_manage_company_profile)]
ManageCompanyMembersDep = Annotated[ManageCompanyMembers, Depends(get_manage_company_members)]
ManageCompanyToursDep = Annotated[ManageCompanyTours, Depends(get_manage_company_tours)]
UploadCompanyImageDep = Annotated[UploadCompanyImage, Depends(get_upload_company_image)]


def get_upload_user_image(
    request: Request, storage: Annotated[ImageStorage, Depends(get_image_storage)]
) -> UploadUserImage:
    return UploadUserImage(storage, request.app.state.max_image_bytes)


UploadUserImageDep = Annotated[UploadUserImage, Depends(get_upload_user_image)]
SignInDep = Annotated[SignInWithProvider, Depends(get_sign_in)]
RefreshSessionDep = Annotated[RefreshSession, Depends(get_refresh_session)]
SignOutDep = Annotated[SignOut, Depends(get_sign_out)]
UpdateProfileDep = Annotated[UpdateProfile, Depends(get_update_profile)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]
CurrentAdminDep = Annotated[User, Depends(get_current_admin)]
CurrentStaffDep = Annotated[User, Depends(get_current_staff)]
