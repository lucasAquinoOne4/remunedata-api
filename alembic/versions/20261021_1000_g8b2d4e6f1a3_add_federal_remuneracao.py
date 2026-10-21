"""add_federal_remuneracao

Tabela de remuneração de servidores civis do Executivo federal (download em lote do Portal da
Transparência) e índice trigram para a busca por nome (reaproveita immutable_unaccent).

Revision ID: g8b2d4e6f1a3
Revises: f7a1c3d9e5b2
Create Date: 2026-10-21 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g8b2d4e6f1a3"
down_revision: Union[str, None] = "f7a1c3d9e5b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FLOATS = [
    "remuneracao_basica_bruta",
    "abate_teto",
    "gratificacao_natalina",
    "abate_teto_natalina",
    "ferias",
    "outras_eventuais",
    "irrf",
    "pss_rpgs",
    "demais_deducoes",
    "verbas_indenizatorias",
    "valor_bruto",
    "valor_liquido",
]


def upgrade() -> None:
    op.create_table(
        "federal_remuneracao",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("id_servidor_portal", sa.BigInteger(), nullable=False),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("mes", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(255), nullable=False),
        sa.Column("cpf_mascarado", sa.String(20), nullable=True),
        sa.Column("matricula_mascarada", sa.String(30), nullable=True),
        sa.Column("cargo", sa.String(255), nullable=True),
        sa.Column("funcao", sa.String(255), nullable=True),
        sa.Column("org_lotacao", sa.String(255), nullable=True),
        sa.Column("org_exercicio", sa.String(255), nullable=True),
        sa.Column("orgao_superior", sa.String(255), nullable=True),
        sa.Column("tipo_vinculo", sa.String(100), nullable=True),
        sa.Column("situacao_vinculo", sa.String(100), nullable=True),
        sa.Column("regime_juridico", sa.String(100), nullable=True),
        sa.Column("jornada", sa.String(100), nullable=True),
        sa.Column("uf_exercicio", sa.String(5), nullable=True),
        *[sa.Column(c, sa.Float(), nullable=True, server_default="0") for c in _FLOATS],
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id_servidor_portal", "ano", "mes", name="uq_federal_rem_periodo"),
    )
    op.create_index("ix_federal_remuneracao_id_servidor_portal", "federal_remuneracao", ["id_servidor_portal"])
    op.create_index("ix_federal_rem_ano_mes", "federal_remuneracao", ["ano", "mes"])
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_federal_rem_nome_unaccent_trgm "
            "ON federal_remuneracao USING gin (immutable_unaccent(nome) gin_trgm_ops)"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_federal_rem_nome_unaccent_trgm")
    op.drop_index("ix_federal_rem_ano_mes", table_name="federal_remuneracao")
    op.drop_index("ix_federal_remuneracao_id_servidor_portal", table_name="federal_remuneracao")
    op.drop_table("federal_remuneracao")
