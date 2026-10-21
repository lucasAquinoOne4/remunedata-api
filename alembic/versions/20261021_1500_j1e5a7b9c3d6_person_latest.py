"""person_latest

Retrato mais recente por pessoa e fonte (uma linha por pessoa), com índice trigram pequeno.
A busca por nome deixa de varrer milhões de linhas mensais.

Revision ID: j1e5a7b9c3d6
Revises: i0d4f6a8b3c5
Create Date: 2026-10-21 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j1e5a7b9c3d6"
down_revision: Union[str, None] = "i0d4f6a8b3c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "person_latest",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("fonte", sa.String(10), nullable=False),
        sa.Column("ident", sa.String(40), nullable=False),
        sa.Column("matricula", sa.String(40), nullable=True),
        sa.Column("nome", sa.String(255), nullable=False),
        sa.Column("cargo", sa.String(255), nullable=True),
        sa.Column("orgao", sa.String(255), nullable=True),
        sa.Column("cpf", sa.String(20), nullable=True),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("mes", sa.Integer(), nullable=False),
        sa.Column("valor_bruto", sa.Float(), nullable=True, server_default="0"),
        sa.Column("valor_liquido", sa.Float(), nullable=True, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_person_latest_fonte_ident", "person_latest", ["fonte", "ident"])
    op.create_index("ix_person_latest_fonte_cargo", "person_latest", ["fonte", "cargo"])
    op.create_index("ix_person_latest_fonte_orgao", "person_latest", ["fonte", "orgao"])
    op.execute(
        "CREATE INDEX ix_person_latest_nome_trgm ON person_latest "
        "USING gin (immutable_unaccent(nome) gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_person_latest_nome_trgm")
    op.drop_table("person_latest")
