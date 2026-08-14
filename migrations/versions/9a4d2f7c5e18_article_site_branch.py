"""track the branch the site pull request was opened from

Revision ID: 9a4d2f7c5e18
Revises: 7c1f3a2b9d41
Create Date: 2026-08-14 18:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9a4d2f7c5e18"
down_revision: Union[str, Sequence[str], None] = "7c1f3a2b9d41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("articles", sa.Column("site_branch", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("articles", "site_branch")
