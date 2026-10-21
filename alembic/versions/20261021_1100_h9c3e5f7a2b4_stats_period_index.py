"""stats_period_index

Índice de cobertura (ano, mes) INCLUDE (valor_bruto) para os gráficos (evolução mensal) e para a
lista de competências: contagem, média e soma por mês viram index-only scan.

Revision ID: h9c3e5f7a2b4
Revises: g8b2d4e6f1a3
Create Date: 2026-10-21 11:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "h9c3e5f7a2b4"
down_revision: Union[str, None] = "g8b2d4e6f1a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_remuneration_period_bruto "
            "ON remuneration_collected (ano_exercicio, mes_referencia) INCLUDE (valor_bruto)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_senado_rem_period_bruto "
            "ON senado_remuneracao (ano, mes) INCLUDE (valor_bruto)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_federal_rem_period_bruto "
            "ON federal_remuneracao (ano, mes) INCLUDE (valor_bruto)"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_federal_rem_period_bruto")
    op.execute("DROP INDEX IF EXISTS ix_senado_rem_period_bruto")
    op.execute("DROP INDEX IF EXISTS ix_remuneration_period_bruto")
