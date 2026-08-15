"""carry a link in the post itself so the platform renders its preview card

Revision ID: c5a81e6f2b30
Revises: b3f9c1d70a24
Create Date: 2026-08-15 23:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c5a81e6f2b30"
down_revision: Union[str, Sequence[str], None] = "b3f9c1d70a24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("drafts", sa.Column("card_url", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("drafts", "card_url")
