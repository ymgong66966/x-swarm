"""remember an article's hero photograph so promos can reuse it

Revision ID: b3f9c1d70a24
Revises: 9a4d2f7c5e18
Create Date: 2026-08-15 22:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3f9c1d70a24"
down_revision: Union[str, Sequence[str], None] = "9a4d2f7c5e18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("articles", sa.Column("hero_path", sa.Text(), nullable=False, server_default=""))
    op.add_column("articles", sa.Column("hero_alt", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("articles", "hero_alt")
    op.drop_column("articles", "hero_path")
