"""summary_covering_index

Índice de cobertura para o /summary do dashboard: com ele o Postgres responde por index-only
scan, sem ler o raw_payload_json de milhões de linhas (inviável em servidores pequenos).

Revision ID: e6f4a0b3c924
Revises: d5e3f9a2b813
Create Date: 2026-10-20 01:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "e6f4a0b3c924"
down_revision: Union[str, None] = "d5e3f9a2b813"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CONCURRENTLY: não bloqueia a coleta que está gravando (roda fora de transação)
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_remuneration_summary "
            "ON remuneration_collected (ano_exercicio, nome_orgao) INCLUDE (valor_bruto)"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_remuneration_summary")
