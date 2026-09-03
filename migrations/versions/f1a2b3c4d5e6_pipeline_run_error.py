"""why a pipeline run failed

Revision ID: f1a2b3c4d5e6
Revises: e7f8a9b0c1d2
Create Date: 2026-09-03 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    inspector = sa.inspect(op.get_bind())
    if "error" in {col["name"] for col in inspector.get_columns("pipeline_runs")}:
        return
    with op.batch_alter_table("pipeline_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("error", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("pipeline_runs", schema=None) as batch_op:
        batch_op.drop_column("error")
