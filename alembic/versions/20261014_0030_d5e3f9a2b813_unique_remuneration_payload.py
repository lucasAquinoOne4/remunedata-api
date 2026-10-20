"""unique_remuneration_payload

Impede registros duplicados de um mesmo mês (mesmo payload bruto), mesmo que duas coletas
rodem em paralelo.

Revision ID: d5e3f9a2b813
Revises: c4d2e8f1a702
Create Date: 2026-10-14 00:30:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "d5e3f9a2b813"
down_revision: Union[str, None] = "c4d2e8f1a702"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A razão desta migration é a existência de duplicatas: remove as cópias exatas (mesmo
    # ano, mês e payload), mantendo a mais antiga, senão o índice único não pode ser criado.
    op.execute(
        """
        DELETE FROM remuneration_collected r
        USING (
            SELECT id, row_number() OVER (
                PARTITION BY ano_exercicio, mes_referencia, md5(raw_payload_json) ORDER BY id
            ) AS rn
            FROM remuneration_collected
        ) d
        WHERE r.id = d.id AND d.rn > 1
        """
    )
    # CONCURRENTLY não bloqueia escritas (precisa rodar fora de transação)
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_remuneration_periodo_payload "
            "ON remuneration_collected (ano_exercicio, mes_referencia, md5(raw_payload_json))"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_remuneration_periodo_payload")
