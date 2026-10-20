#!/bin/sh
# Cria os bancos por ambiente (roda só em volume novo; em volume existente use `make db-create`).
#   df_remuneration       -> local (dev do dia a dia)
#   df_remuneration_hml   -> homologação
#   df_remuneration_test  -> testes automatizados (a suíte faz TRUNCATE; NUNCA apontar para outro)
set -e
for db in df_remuneration_hml df_remuneration_test; do
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 \
    || psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE $db"
done
