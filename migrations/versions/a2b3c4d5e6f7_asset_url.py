"""where an asset can be seen from another machine

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-09-04 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    inspector = sa.inspect(op.get_bind())
    if "url" not in {col["name"] for col in inspector.get_columns("assets")}:
        with op.batch_alter_table("assets", schema=None) as batch_op:
            batch_op.add_column(sa.Column("url", sa.Text(), nullable=False, server_default=""))
    if "hero_url" not in {col["name"] for col in inspector.get_columns("articles")}:
        with op.batch_alter_table("articles", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("hero_url", sa.Text(), nullable=False, server_default="")
            )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("articles", schema=None) as batch_op:
        batch_op.drop_column("hero_url")
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_column("url")
