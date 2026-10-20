# df-remuneration-collector Makefile
# Comandos para facilitar o desenvolvimento local com Docker

.PHONY: help up down build restart test migrate migrate-hml migrate-test db-create logs shell clean status

help:
	@echo "Comandos disponiveis:"
	@echo "  make up       - Sobe todos os servicos (detached)"
	@echo "  make down     - Para todos os servicos"
	@echo "  make build    - Build/Rebuild das imagens"
	@echo "  make restart  - Restart dos servicos"
	@echo "  make test     - Suite de testes (SEMPRE no banco df_remuneration_test, nunca no local)"
	@echo "  make migrate  - Migrations no banco LOCAL (df_remuneration)"
	@echo "  make migrate-hml - Migrations no banco de homologacao"
	@echo "  make db-create - Cria os bancos hml e test (idempotente)"
	@echo "  make logs     - Visualiza logs de todos os servicos (follow)"
	@echo "  make shell    - Abre um terminal bash no container da app"
	@echo "  make clean    - Para servicos e remove volumes (Alerta: apaga o banco!)"
	@echo "  make status   - Verifica o status dos containers"

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

restart:
	docker compose restart

# Bancos por ambiente: local = df_remuneration | hml = df_remuneration_hml | test = df_remuneration_test
db-create:
	docker compose up -d postgres
	docker compose exec -T postgres sh /docker-entrypoint-initdb.d/init-databases.sh

# Carrega o .env.test no shell e repassa ao container, sobrescrevendo o .env local do app
TEST_VARS = DATABASE_URL DATABASE_URL_SYNC REDIS_URL APP_ENV LOG_LEVEL SUPABASE_URL SUPABASE_JWT_SECRET SUPABASE_JWKS_URL SUPABASE_SERVICE_ROLE_KEY ADMIN_EMAILS CORS_ORIGINS
TEST_E = $(foreach v,$(TEST_VARS),-e $(v))
RUN_TEST = set -a; . ./.env.test; set +a; docker compose run --rm $(TEST_E) -e PYTHONPATH=. app

migrate-test: db-create
	$(RUN_TEST) alembic upgrade head

# A suite faz TRUNCATE: roda isolada no banco _test e no Redis 15, e o conftest recusa qualquer outro banco.
test: migrate-test
	$(RUN_TEST) pytest tests/ -v --tb=short

migrate:
	docker compose run --rm -e PYTHONPATH=. app alembic upgrade head

migrate-hml: db-create
	docker compose run --rm --no-deps -e PYTHONPATH=. \
		-e DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/df_remuneration_hml \
		-e DATABASE_URL_SYNC=postgresql+psycopg2://postgres:postgres@postgres:5432/df_remuneration_hml \
		app alembic upgrade head

logs:
	docker compose logs -f

shell:
	docker compose run --rm app bash

clean:
	docker compose down -v

status:
	docker compose ps
