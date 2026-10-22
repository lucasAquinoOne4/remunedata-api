"""estadual_remuneracao

Tabela única para a remuneração de servidores de estados (CE, PE, GO, SC, ...), alimentada por
adaptadores de fonte. Índices: competência, pessoa, busca por nome (trigram), cobertura para
gráficos e comparação com pares.

Revision ID: l3a7c9d1e5f8
Revises: k2f6b8c0d4e7
Create Date: 2026-10-22 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "l3a7c9d1e5f8"
down_revision: Union[str, None] = "k2f6b8c0d4e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "estadual_remuneracao",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("mes", sa.Integer(), nullable=False),
        sa.Column("linha_hash", sa.String(32), nullable=False),
        sa.Column("ident", sa.String(40), nullable=False),
        sa.Column("nome", sa.String(255), nullable=False),
        sa.Column("cpf_mascarado", sa.String(20), nullable=True),
        sa.Column("matricula", sa.String(40), nullable=True),
        sa.Column("cargo", sa.String(255), nullable=True),
        sa.Column("orgao", sa.String(255), nullable=True),
        sa.Column("situacao", sa.String(100), nullable=True),
        sa.Column("vinculo", sa.String(100), nullable=True),
        sa.Column("valor_bruto", sa.Float(), nullable=True, server_default="0"),
        sa.Column("valor_liquido", sa.Float(), nullable=True, server_default="0"),
        sa.Column("valor_descontos", sa.Float(), nullable=True, server_default="0"),
        sa.Column("detalhes", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uf", "ano", "mes", "linha_hash", name="uq_estadual_rem_linha"),
    )
    op.create_index("ix_estadual_rem_uf_ano_mes", "estadual_remuneracao", ["uf", "ano", "mes"])
    op.create_index("ix_estadual_rem_uf_ident", "estadual_remuneracao", ["uf", "ident"])
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_estadual_rem_nome_trgm "
            "ON estadual_remuneracao USING gin (immutable_unaccent(nome) gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_estadual_rem_period_bruto "
            "ON estadual_remuneracao (uf, ano, mes) INCLUDE (valor_bruto)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_estadual_rem_peers "
            "ON estadual_remuneracao (uf, ano, mes, cargo) INCLUDE (valor_bruto)"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_estadual_rem_peers")
    op.execute("DROP INDEX IF EXISTS ix_estadual_rem_period_bruto")
    op.execute("DROP INDEX IF EXISTS ix_estadual_rem_nome_trgm")
    op.drop_table("estadual_remuneracao")
