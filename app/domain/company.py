from enum import StrEnum


class CompanyRole(StrEnum):
    OWNER = "owner"
    MANAGER = "manager"


class CompanyPermission(StrEnum):
    MANAGE_TOURS = "manage_tours"
    MANAGE_PROFILE = "manage_profile"
    MANAGE_MEMBERS = "manage_members"


_ROLE_PERMISSIONS: dict[CompanyRole, frozenset[CompanyPermission]] = {
    CompanyRole.OWNER: frozenset(CompanyPermission),
    CompanyRole.MANAGER: frozenset({CompanyPermission.MANAGE_TOURS}),
}


def role_allows(role: CompanyRole, permission: CompanyPermission) -> bool:
    return permission in _ROLE_PERMISSIONS[role]
