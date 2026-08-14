"""track the site pull request that will publish an article

Revision ID: 7c1f3a2b9d41
Revises: 35b771c92609
Create Date: 2026-08-14 16:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c1f3a2b9d41'
down_revision: Union[str, Sequence[str], None] = '35b771c92609'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('articles', sa.Column('site_pr_url', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('articles', 'site_pr_url')
