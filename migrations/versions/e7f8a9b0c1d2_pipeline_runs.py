"""pipeline run tracking and run_id on model_calls

Revision ID: e7f8a9b0c1d2
Revises: c5a81e6f2b30
Create Date: 2026-08-30 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "c5a81e6f2b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has(table: str, column: str = "") -> bool:
    """`init_db()` calls `create_all`, so a database can already carry a table this
    migration wants to create. Check before touching it rather than fail half way and
    leave the schema between two revisions, which is how the shared Postgres ended up
    with `pipeline_runs` but without `model_calls.pipeline_run_id`."""
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    if not column:
        return True
    return column in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    """Upgrade schema."""
    if _has("pipeline_runs"):
        _add_run_id()
        return
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stream", sa.String(length=8), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("pipeline_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_pipeline_runs_stream"), ["stream"], unique=False)
        batch_op.create_index(batch_op.f("ix_pipeline_runs_run_date"), ["run_date"], unique=False)
    _add_run_id()


def _add_run_id() -> None:
    if _has("model_calls", "pipeline_run_id"):
        return
    with op.batch_alter_table("model_calls", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pipeline_run_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_model_calls_pipeline_run_id", "pipeline_runs", ["pipeline_run_id"], ["id"]
        )
        batch_op.create_index(
            batch_op.f("ix_model_calls_pipeline_run_id"), ["pipeline_run_id"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("model_calls", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_model_calls_pipeline_run_id"))
        batch_op.drop_constraint("fk_model_calls_pipeline_run_id", type_="foreignkey")
        batch_op.drop_column("pipeline_run_id")

    with op.batch_alter_table("pipeline_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_pipeline_runs_run_date"))
        batch_op.drop_index(batch_op.f("ix_pipeline_runs_stream"))

    op.drop_table("pipeline_runs")
