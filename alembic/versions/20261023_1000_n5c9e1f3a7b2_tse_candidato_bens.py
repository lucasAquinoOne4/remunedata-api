"""tse_candidato_bens

Candidaturas e bens declarados ao TSE (dados abertos), para enriquecer o perfil. A tabela de
candidatos guarda apenas os 6 dígitos centrais do CPF; CPF completo e e-mail não são gravados.

Revision ID: n5c9e1f3a7b2
Revises: m4b8d0e2f6a1
Create Date: 2026-10-23 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "n5c9e1f3a7b2"
down_revision: Union[str, None] = "m4b8d0e2f6a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tse_candidato",
        sa.Column("ano_eleicao", sa.Integer(), nullable=False),
        sa.Column("sq_candidato", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.String(255), nullable=False),
        sa.Column("nome_norm", sa.String(255), nullable=False),
        sa.Column("cpf_core", sa.String(6), nullable=True),
        sa.Column("uf", sa.String(2), nullable=True),
        sa.Column("cargo", sa.String(100), nullable=True),
        sa.Column("partido", sa.String(20), nullable=True),
        sa.Column("ocupacao", sa.String(150), nullable=True),
        sa.Column("situacao", sa.String(100), nullable=True),
        sa.Column("resultado", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("ano_eleicao", "sq_candidato"),
    )
    op.create_index("ix_tse_cand_cpf_nome", "tse_candidato", ["cpf_core", "nome_norm"])
    op.create_table(
        "tse_bem",
        sa.Column("ano_eleicao", sa.Integer(), nullable=False),
        sa.Column("sq_candidato", sa.BigInteger(), nullable=False),
        sa.Column("tipo", sa.String(100), nullable=False),
        sa.Column("quantidade", sa.Integer(), nullable=False),
        sa.Column("valor", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("ano_eleicao", "sq_candidato", "tipo"),
    )


def downgrade() -> None:
    op.drop_table("tse_bem")
    op.drop_index("ix_tse_cand_cpf_nome", table_name="tse_candidato")
    op.drop_table("tse_candidato")
