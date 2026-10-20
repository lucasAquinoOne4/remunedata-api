# ADR 0004 — Bancos por ambiente e isolamento da suíte de testes

- **Status:** Aceito (2026-10)
- **Implementação:** `Makefile`, `docker/postgres/init-databases.sh`, `.env.test`, `.env.hml.example`, `tests/conftest.py`

## Contexto
Incidente real: `pytest` rodou no container do app contra o banco de desenvolvimento; o `conftest.py` faz `TRUNCATE` e apagou 5,2 milhões de registros sem backup. O `make test` original tinha o mesmo defeito.

## Decisão
- Três bancos no mesmo Postgres: `df_remuneration` (local), `df_remuneration_hml` e `df_remuneration_test`.
- `make test` carrega o `.env.test`, aplica as migrations no banco `_test` e usa Redis no índice 15; `make migrate-hml` e `make db-create` cobrem os demais.
- O `conftest.py` **aborta** (`SystemExit`) se `DATABASE_URL` não terminar em `_test`.
- `docker-compose.override.yml` (portas da máquina) fica fora do git.

## Alternativas descartadas
Confiar na disciplina de apontar para o banco certo; fixtures que recriam o schema (mais lentas e igualmente perigosas se o alvo errar).

## Consequências
`docker compose run ... pytest` (como no README antigo) deixa de funcionar de propósito. Volumes existentes precisam de `make db-create`.
