"""contexto_economico

Séries econômicas mensais (IPCA, salário mínimo, Selic) e a tabela do teto constitucional, com a
norma citada. Valores do teto conferidos na Lei 14.520/2023 (parcelas de 04/2023, 02/2024 e
02/2025); o valor anterior (R$ 39.293,32) é o citado pela própria lei como base do reajuste.

Revision ID: m4b8d0e2f6a1
Revises: l3a7c9d1e5f8
Create Date: 2026-10-22 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m4b8d0e2f6a1"
down_revision: Union[str, None] = "l3a7c9d1e5f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEI = "Lei 14.520/2023"
_URL = "https://www2.camara.leg.br/legin/fed/lei/2023/lei-14520-9-janeiro-2023-793675-norma-pl.html"


def upgrade() -> None:
    op.create_table(
        "indice_economico",
        sa.Column("serie", sa.String(30), nullable=False),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("mes", sa.Integer(), nullable=False),
        sa.Column("valor", sa.Float(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("serie", "ano", "mes"),
    )
    teto = op.create_table(
        "teto_constitucional",
        sa.Column("vigencia_inicio", sa.Date(), nullable=False),
        sa.Column("valor", sa.Float(), nullable=False),
        sa.Column("norma", sa.String(200), nullable=False),
        sa.Column("url_norma", sa.String(300), nullable=True),
        sa.PrimaryKeyConstraint("vigencia_inicio"),
    )
    import datetime as dt

    op.bulk_insert(
        teto,
        [
            {
                "vigencia_inicio": dt.date(2019, 1, 1),
                "valor": 39293.32,
                "norma": f"Valor vigente antes da 1ª parcela da {_LEI}",
                "url_norma": _URL,
            },
            {"vigencia_inicio": dt.date(2023, 4, 1), "valor": 41650.92, "norma": f"{_LEI}, art. 1º, I", "url_norma": _URL},
            {"vigencia_inicio": dt.date(2024, 2, 1), "valor": 44008.52, "norma": f"{_LEI}, art. 1º, II", "url_norma": _URL},
            {"vigencia_inicio": dt.date(2025, 2, 1), "valor": 46366.19, "norma": f"{_LEI}, art. 1º, III", "url_norma": _URL},
        ],
    )


def downgrade() -> None:
    op.drop_table("teto_constitucional")
    op.drop_table("indice_economico")
