from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.application.dto import CompanyMembershipView, InvitationView, MemberView
from app.domain.company import CompanyRole


class MembershipRepository(Protocol):
    async def get_role(self, operator_id: UUID, user_id: UUID) -> CompanyRole | None: ...

    async def list_members(self, operator_id: UUID) -> list[MemberView]: ...

    async def list_for_user(self, user_id: UUID) -> list[CompanyMembershipView]: ...

    async def add(
        self, operator_id: UUID, user_id: UUID, role: CompanyRole, now: datetime
    ) -> None: ...

    async def set_role(self, operator_id: UUID, user_id: UUID, role: CompanyRole) -> None: ...

    async def remove(self, operator_id: UUID, user_id: UUID) -> None: ...

    async def lock_owner_ids(self, operator_id: UUID) -> list[UUID]: ...


class InvitationRepository(Protocol):
    async def create(
        self,
        operator_id: UUID,
        email: str,
        role: CompanyRole,
        invited_by: UUID,
        now: datetime,
    ) -> InvitationView: ...

    async def list(self, operator_id: UUID) -> list[InvitationView]: ...

    async def delete(self, operator_id: UUID, invitation_id: UUID) -> bool: ...

    async def claim(self, user_id: UUID, email: str, now: datetime) -> None:
        """Превращает приглашения на этот email в членство в компаниях."""
        ...
