"""Команды обслуживания.

Создать первого администратора (пароль вводится скрыто):
    poetry run python -m app.cli create-admin admin@example.com
В Docker:
    docker compose exec api python -m app.cli create-admin admin@example.com
"""

import argparse
import asyncio
import getpass
import os
import sys

from app.application.use_cases.users import ManageUsers
from app.config import get_settings
from app.domain.exceptions import DomainError
from app.domain.user import UserRole
from app.infrastructure.auth.passwords import Argon2PasswordHasher
from app.infrastructure.db.company_repositories import SqlAlchemyMembershipRepository
from app.infrastructure.db.repositories import (
    SqlAlchemyRefreshTokenRepository,
    SqlAlchemyUnitOfWork,
    SqlAlchemyUserRepository,
)
from app.infrastructure.db.session import create_engine, create_session_factory


def _read_password() -> str:
    # Для автоматизации пароль можно передать через переменную окружения.
    from_env = os.environ.get("BARALY_ADMIN_PASSWORD")
    if from_env:
        return from_env
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match")
    return password


async def _create_admin(email: str, password: str) -> None:
    engine = create_engine(get_settings())
    try:
        async with create_session_factory(engine)() as session:
            use_case = ManageUsers(
                SqlAlchemyUserRepository(session),
                SqlAlchemyUnitOfWork(session),
                Argon2PasswordHasher(),
                SqlAlchemyRefreshTokenRepository(session),
                SqlAlchemyMembershipRepository(session),
            )
            user = await use_case.create_staff(
                None, email=email, role=UserRole.ADMIN, password=password
            )
            print(f"Admin ready: {user.email}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create_admin = commands.add_parser("create-admin", help="создать или обновить администратора")
    create_admin.add_argument("email")
    args = parser.parse_args()

    if args.command == "create-admin":
        try:
            asyncio.run(_create_admin(args.email, _read_password()))
        except DomainError as exc:
            sys.exit(str(exc))


if __name__ == "__main__":
    main()
