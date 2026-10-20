"""add_senado_remuneracao

Revision ID: c4d2e8f1a702
Revises: b3f1a9e2c501
Create Date: 2026-10-13 23:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4d2e8f1a702"
down_revision: Union[str, None] = "b3f1a9e2c501"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FLOATS = [
    "remuneracao_basica",
    "vantagens_pessoais",
    "funcao_comissionada",
    "gratificacao_natalina",
    "horas_extras",
    "outras_eventuais",
    "abono_permanencia",
    "vantagens_indenizatorias",
    "diarias",
    "auxilios",
    "previdencia",
    "imposto_renda",
    "faltas",
    "reversao_teto_constitucional",
    "valor_bruto",
    "valor_liquido",
]


def upgrade() -> None:
    op.create_table(
        "senado_remuneracao",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("sequencial", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.String(255), nullable=False),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("mes", sa.Integer(), nullable=False),
        sa.Column("tipo_folha", sa.String(50), nullable=False, server_default="Normal"),
        sa.Column("vinculo", sa.String(100), nullable=True),
        sa.Column("situacao", sa.String(50), nullable=True),
        sa.Column("cargo", sa.String(255), nullable=True),
        sa.Column("funcao", sa.String(255), nullable=True),
        sa.Column("lotacao_sigla", sa.String(50), nullable=True),
        sa.Column("lotacao_nome", sa.Text(), nullable=True),
        *[sa.Column(c, sa.Float(), nullable=True, server_default="0") for c in _FLOATS],
        sa.Column("raw_payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sequencial", "ano", "mes", "tipo_folha", name="uq_senado_rem_periodo"),
    )
    op.create_index("ix_senado_remuneracao_sequencial", "senado_remuneracao", ["sequencial"])
    op.create_index("ix_senado_rem_ano_mes", "senado_remuneracao", ["ano", "mes"])
    # Índice trigram para a busca por nome (unaccent + ilike) escalar bem
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_senado_rem_nome_trgm ON senado_remuneracao "
        "USING gin (upper(nome) gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_senado_rem_nome_trgm")
    op.drop_index("ix_senado_rem_ano_mes", table_name="senado_remuneracao")
    op.drop_index("ix_senado_remuneracao_sequencial", table_name="senado_remuneracao")
    op.drop_table("senado_remuneracao")
