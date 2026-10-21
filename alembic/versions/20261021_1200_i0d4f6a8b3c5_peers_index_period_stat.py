"""peers_index_period_stat

- Índice de cobertura (ano, mes, cargo) INCLUDE (valor_bruto) para comparar a remuneração com a de
  quem ocupa o mesmo cargo (de ~4,5 s para milissegundos).
- Tabela period_stat: agregados por competência para os gráficos.

Revision ID: i0d4f6a8b3c5
Revises: h9c3e5f7a2b4
Create Date: 2026-10-21 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i0d4f6a8b3c5"
down_revision: Union[str, None] = "h9c3e5f7a2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "period_stat",
        sa.Column("fonte", sa.String(10), nullable=False),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("mes", sa.Integer(), nullable=False),
        sa.Column("registros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("media_bruto", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_bruto", sa.Float(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("fonte", "ano", "mes"),
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_remuneration_peers "
            "ON remuneration_collected (ano_exercicio, mes_referencia, cargo) INCLUDE (valor_bruto)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_federal_rem_peers "
            "ON federal_remuneracao (ano, mes, cargo) INCLUDE (valor_bruto)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_senado_rem_peers "
            "ON senado_remuneracao (ano, mes, cargo) INCLUDE (valor_bruto)"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_senado_rem_peers")
    op.execute("DROP INDEX IF EXISTS ix_federal_rem_peers")
    op.execute("DROP INDEX IF EXISTS ix_remuneration_peers")
    op.drop_table("period_stat")
