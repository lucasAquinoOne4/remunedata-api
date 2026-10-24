"""rfb_socios

Participação societária a partir dos dados abertos de CNPJ da Receita Federal, já filtrada
localmente para as pessoas de interesse. Só os 6 dígitos centrais do CPF são gravados; sem CPF
completo, endereço, telefone ou e-mail.

Revision ID: o6d0f2a4b8c3
Revises: n5c9e1f3a7b2
Create Date: 2026-10-24 01:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "o6d0f2a4b8c3"
down_revision: Union[str, None] = "n5c9e1f3a7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rfb_empresa",
        sa.Column("cnpj_basico", sa.String(8), nullable=False),
        sa.Column("razao_social", sa.String(255), nullable=True),
        sa.Column("nome_fantasia", sa.String(255), nullable=True),
        sa.Column("natureza_juridica_cod", sa.String(10), nullable=True),
        sa.Column("capital_social", sa.Float(), nullable=True),
        sa.Column("porte_cod", sa.String(4), nullable=True),
        sa.Column("situacao_cadastral_cod", sa.String(4), nullable=True),
        sa.Column("data_situacao", sa.Date(), nullable=True),
        sa.Column("data_inicio_atividade", sa.Date(), nullable=True),
        sa.Column("cnae_principal", sa.String(10), nullable=True),
        sa.Column("uf", sa.String(2), nullable=True),
        sa.Column("municipio_cod", sa.String(10), nullable=True),
        sa.PrimaryKeyConstraint("cnpj_basico"),
    )
    op.create_table(
        "rfb_socio",
        sa.Column("cnpj_basico", sa.String(8), nullable=False),
        sa.Column("nome_norm", sa.String(255), nullable=False),
        sa.Column("cpf_core", sa.String(6), nullable=False),
        sa.Column("nome_socio", sa.String(255), nullable=False),
        sa.Column("qualificacao_cod", sa.String(4), nullable=True),
        sa.Column("data_entrada", sa.Date(), nullable=True),
        sa.Column("faixa_etaria", sa.String(4), nullable=True),
        sa.PrimaryKeyConstraint("cnpj_basico", "nome_norm", "cpf_core"),
    )
    op.create_index("ix_rfb_socio_cpf_nome", "rfb_socio", ["cpf_core", "nome_norm"])
    op.create_table(
        "rfb_dominio",
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("cod", sa.String(10), nullable=False),
        sa.Column("descricao", sa.String(300), nullable=False),
        sa.PrimaryKeyConstraint("tipo", "cod"),
    )
    op.create_table(
        "rfb_carga",
        sa.Column("mes", sa.String(7), nullable=False),
        sa.Column("carregado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contagens", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("mes"),
    )


def downgrade() -> None:
    op.drop_table("rfb_carga")
    op.drop_table("rfb_dominio")
    op.drop_index("ix_rfb_socio_cpf_nome", table_name="rfb_socio")
    op.drop_table("rfb_socio")
    op.drop_table("rfb_empresa")
