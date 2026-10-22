"""federal_categoria

Distingue CIVIL (SIAPE), MILITAR (Forças Armadas) e BACEN (Banco Central) em federal_remuneracao.
A restrição única passa a incluir a categoria.

Revision ID: k2f6b8c0d4e7
Revises: j1e5a7b9c3d6
Create Date: 2026-10-22 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "k2f6b8c0d4e7"
down_revision: Union[str, None] = "j1e5a7b9c3d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "federal_remuneracao",
        sa.Column("categoria", sa.String(10), nullable=False, server_default="CIVIL"),
    )
    op.drop_constraint("uq_federal_rem_periodo", "federal_remuneracao", type_="unique")
    op.create_unique_constraint(
        "uq_federal_rem_periodo_cat",
        "federal_remuneracao",
        ["id_servidor_portal", "ano", "mes", "categoria"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_federal_rem_periodo_cat", "federal_remuneracao", type_="unique")
    op.create_unique_constraint(
        "uq_federal_rem_periodo", "federal_remuneracao", ["id_servidor_portal", "ano", "mes"]
    )
    op.drop_column("federal_remuneracao", "categoria")
