"""name_search_trigram

Índices trigram para a busca por nome (DF e Senado). A busca usa unaccent(...) ILIKE '%x%';
unaccent é STABLE (não indexável), então cria-se um wrapper IMMUTABLE e o índice GIN sobre ele.
Sem isso a busca por nome é uma varredura completa (8,6 GB) a cada consulta.

Revision ID: f7a1c3d9e5b2
Revises: e6f4a0b3c924
Create Date: 2026-10-20 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "f7a1c3d9e5b2"
down_revision: Union[str, None] = "e6f4a0b3c924"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION immutable_unaccent(text) RETURNS text
        AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        """
    )
    # índice antigo (upper(nome)) nunca era usado pela consulta
    op.execute("DROP INDEX IF EXISTS ix_senado_rem_nome_trgm")
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_senado_rem_nome_unaccent_trgm "
            "ON senado_remuneracao USING gin (immutable_unaccent(nome) gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_remuneration_nome_unaccent_trgm "
            "ON remuneration_collected USING gin (immutable_unaccent(nome_servidor) gin_trgm_ops)"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_remuneration_nome_unaccent_trgm")
    op.execute("DROP INDEX IF EXISTS ix_senado_rem_nome_unaccent_trgm")
    op.execute("DROP FUNCTION IF EXISTS immutable_unaccent(text)")
