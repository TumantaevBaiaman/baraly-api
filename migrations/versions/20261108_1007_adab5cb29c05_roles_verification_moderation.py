"""roles verification moderation

Revision ID: adab5cb29c05
Revises: 49e5279bebc5
Create Date: 2026-11-08 10:07:37.605469

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'adab5cb29c05'
down_revision: str | None = '49e5279bebc5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('tour_operators', sa.Column('is_verified', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('tour_operators', sa.Column('verification_note', sa.Text(), nullable=True))
    op.add_column('tour_operators', sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True))

    # Уже опубликованные туры были доступны на сайте, поэтому считаются одобренными.
    moderation_status = sa.Enum('pending', 'approved', 'rejected', name='moderation_status', native_enum=False, create_constraint=True, length=20)
    op.add_column('tours', sa.Column('moderation_status', moderation_status, server_default='approved', nullable=False))
    op.alter_column('tours', 'moderation_status', server_default=None)
    op.add_column('tours', sa.Column('moderation_comment', sa.Text(), nullable=True))
    op.add_column('tours', sa.Column('moderated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tours', sa.Column('moderated_by', sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f('fk_tours_moderated_by_users'), 'tours', 'users', ['moderated_by'], ['id'], ondelete='SET NULL')

    # is_admin заменяется ролью; администраторы сохраняют доступ.
    user_role = sa.Enum('user', 'moderator', 'admin', name='user_role', native_enum=False, create_constraint=True, length=20)
    op.add_column('users', sa.Column('role', user_role, server_default='user', nullable=False))
    op.alter_column('users', 'role', server_default=None)
    op.execute("UPDATE users SET role = 'admin' WHERE is_admin")
    op.drop_column('users', 'is_admin')


def downgrade() -> None:
    op.add_column('users', sa.Column('is_admin', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False))
    op.execute("UPDATE users SET is_admin = true WHERE role = 'admin'")
    op.drop_column('users', 'role')
    op.drop_constraint(op.f('fk_tours_moderated_by_users'), 'tours', type_='foreignkey')
    op.drop_column('tours', 'moderated_by')
    op.drop_column('tours', 'moderated_at')
    op.drop_column('tours', 'moderation_comment')
    op.drop_column('tours', 'moderation_status')
    op.drop_column('tour_operators', 'verified_at')
    op.drop_column('tour_operators', 'verification_note')
    op.drop_column('tour_operators', 'is_verified')
