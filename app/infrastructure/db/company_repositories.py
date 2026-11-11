from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto import CompanyMembershipView, InvitationView, MemberView
from app.domain.company import CompanyRole
from app.infrastructure.db.errors import conflict_on_integrity_error
from app.infrastructure.db.models import (
    CompanyInvitationModel,
    CompanyMemberModel,
    TourOperatorModel,
    UserModel,
)


def _to_invitation(model: CompanyInvitationModel) -> InvitationView:
    return InvitationView(
        id=model.id, email=model.email, role=model.role, created_at=model.created_at
    )


class SqlAlchemyMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_role(self, operator_id: UUID, user_id: UUID) -> CompanyRole | None:
        return await self._session.scalar(
            select(CompanyMemberModel.role).where(
                CompanyMemberModel.operator_id == operator_id,
                CompanyMemberModel.user_id == user_id,
            )
        )

    async def list_members(self, operator_id: UUID) -> list[MemberView]:
        rows = await self._session.execute(
            select(CompanyMemberModel, UserModel)
            .join(UserModel, UserModel.id == CompanyMemberModel.user_id)
            .where(CompanyMemberModel.operator_id == operator_id)
            .order_by(CompanyMemberModel.created_at, UserModel.email)
        )
        return [
            MemberView(
                user_id=user.id,
                email=user.email,
                first_name=user.first_name,
                last_name=user.last_name,
                avatar_url=user.avatar_url,
                role=member.role,
                joined_at=member.created_at,
            )
            for member, user in rows
        ]

    async def list_for_user(self, user_id: UUID) -> list[CompanyMembershipView]:
        rows = await self._session.execute(
            select(CompanyMemberModel.role, TourOperatorModel)
            .join(TourOperatorModel, TourOperatorModel.id == CompanyMemberModel.operator_id)
            .where(CompanyMemberModel.user_id == user_id)
            .order_by(TourOperatorModel.name)
        )
        return [
            CompanyMembershipView(
                operator_id=operator.id,
                name=operator.name,
                logo_url=operator.logo_url,
                role=role,
            )
            for role, operator in rows
        ]

    async def add(self, operator_id: UUID, user_id: UUID, role: CompanyRole, now: datetime) -> None:
        with conflict_on_integrity_error("User is already a member of this company"):
            await self._session.execute(
                insert(CompanyMemberModel).values(
                    operator_id=operator_id, user_id=user_id, role=role, created_at=now
                )
            )

    async def set_role(self, operator_id: UUID, user_id: UUID, role: CompanyRole) -> None:
        await self._session.execute(
            update(CompanyMemberModel)
            .where(
                CompanyMemberModel.operator_id == operator_id,
                CompanyMemberModel.user_id == user_id,
            )
            .values(role=role)
        )

    async def remove(self, operator_id: UUID, user_id: UUID) -> None:
        await self._session.execute(
            delete(CompanyMemberModel).where(
                CompanyMemberModel.operator_id == operator_id,
                CompanyMemberModel.user_id == user_id,
            )
        )

    async def lock_owner_ids(self, operator_id: UUID) -> list[UUID]:
        owners = await self._session.scalars(
            select(CompanyMemberModel.user_id)
            .where(
                CompanyMemberModel.operator_id == operator_id,
                CompanyMemberModel.role == CompanyRole.OWNER,
            )
            .with_for_update()
        )
        return list(owners)


class SqlAlchemyInvitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        operator_id: UUID,
        email: str,
        role: CompanyRole,
        invited_by: UUID,
        now: datetime,
    ) -> InvitationView:
        invitation_id = uuid4()
        with conflict_on_integrity_error("This email is already invited"):
            await self._session.execute(
                insert(CompanyInvitationModel).values(
                    id=invitation_id,
                    operator_id=operator_id,
                    email=email,
                    role=role,
                    invited_by=invited_by,
                    created_at=now,
                )
            )
        return InvitationView(id=invitation_id, email=email, role=role, created_at=now)

    async def list(self, operator_id: UUID) -> list[InvitationView]:
        invitations = await self._session.scalars(
            select(CompanyInvitationModel)
            .where(CompanyInvitationModel.operator_id == operator_id)
            .order_by(CompanyInvitationModel.created_at)
        )
        return [_to_invitation(item) for item in invitations]

    async def delete(self, operator_id: UUID, invitation_id: UUID) -> bool:
        deleted = await self._session.scalar(
            delete(CompanyInvitationModel)
            .where(
                CompanyInvitationModel.operator_id == operator_id,
                CompanyInvitationModel.id == invitation_id,
            )
            .returning(CompanyInvitationModel.id)
        )
        return deleted is not None

    async def claim(self, user_id: UUID, email: str, now: datetime) -> None:
        claimed = await self._session.execute(
            delete(CompanyInvitationModel)
            .where(CompanyInvitationModel.email == email)
            .returning(CompanyInvitationModel.operator_id, CompanyInvitationModel.role)
        )
        for operator_id, role in claimed:
            await self._session.execute(
                pg_insert(CompanyMemberModel)
                .values(operator_id=operator_id, user_id=user_id, role=role, created_at=now)
                .on_conflict_do_nothing()
            )
